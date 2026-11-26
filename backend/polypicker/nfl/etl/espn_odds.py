"""ESPN NFL opening + closing moneylines -> nfl_market_prices (source 'espn'), for the line-movement record.

nflverse only publishes closing lines; ESPN's core odds endpoint keeps each book's ``open`` and ``close`` moneyline
(2023+, usually one book per game: Caesars, ESPN BET or DraftKings). One book with both sides of both prices is
kept per game. The NFL model doesn't read this source (it uses Kalshi + Pinnacle), so it only feeds the
line-movement stats.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import Engine, text
from sqlalchemy.dialects.postgresql import insert

from polypicker.etl.issues import IssueLog
from polypicker.etl.sources.espn_odds import _ml, http_get, no_vig_home
from polypicker.nfl import models as nm
from polypicker.nfl.etl.runner import tracked

CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/{gid}/competitions/{gid}/odds"
PREFERRED = ("DraftKings", "ESPN BET", "Caesars Sportsbook (New Jersey)", "Caesars Sportsbook", "FanDuel", "Bet 365")
FIRST_SEASON = 2023


def pick_open_close(items: list[dict]) -> dict | None:
    """The preferred book with an opening and a closing moneyline for both teams."""
    books = []
    for i in items:
        name = (i.get("provider") or {}).get("name") or ""
        if not name or "live" in name.lower():
            continue
        h, a = i.get("homeTeamOdds") or {}, i.get("awayTeamOdds") or {}
        row = {"provider": name[:160], "home_ml_open": _ml(h, "open"), "away_ml_open": _ml(a, "open"),
               "home_ml": _ml(h, "close"), "away_ml": _ml(a, "close")}
        if all(row[k] is not None for k in ("home_ml_open", "away_ml_open", "home_ml", "away_ml")):
            books.append(row)
    if not books:
        return None
    rank = {n: k for k, n in enumerate(PREFERRED)}
    return min(books, key=lambda b: rank.get(b["provider"], len(PREFERRED)))


def sync_nfl_espn_odds(engine: Engine, *, backfill_from_season: int | None = None, retries: int = 2,
                       get=http_get, workers: int = 6) -> dict:
    """Default: games from the last 4 days (closing lines settle) and the next 8 (current lines). Backfill: every
    final game from that season on without an ESPN row."""
    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        with engine.connect() as c:
            if backfill_from_season:
                ids = [r[0] for r in c.execute(text("""
                    SELECT g.game_id FROM nfl_games g WHERE g.season >= :s AND g.status = 'final'
                      AND NOT EXISTS (SELECT 1 FROM nfl_market_prices x WHERE x.game_id = g.game_id AND x.source = 'espn' AND x.closed)
                    ORDER BY g.kickoff_utc"""), {"s": max(backfill_from_season, FIRST_SEASON)})]
            else:
                ids = [r[0] for r in c.execute(text("""
                    SELECT game_id FROM nfl_games WHERE status <> 'canceled'
                      AND kickoff_utc BETWEEN now() - interval '4 days' AND now() + interval '8 days'
                      AND NOT EXISTS (SELECT 1 FROM nfl_market_prices x WHERE x.game_id = nfl_games.game_id AND x.source = 'espn' AND x.closed)
                    ORDER BY kickoff_utc"""))]
            final = {r[0] for r in c.execute(text("SELECT game_id FROM nfl_games WHERE game_id = ANY(:i) AND status = 'final'"), {"i": ids})}

        def job(gid: int):
            try:
                return gid, pick_open_close((get(CORE.format(gid=gid), {"limit": 100}) or {}).get("items") or [])
            except Exception as e:  # noqa: BLE001
                issues.add("nfl_game", gid, "espn_odds_failed", "warning", "skipped", error=str(e)[:200])
                return gid, None

        now = datetime.now(timezone.utc)
        rows = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for gid, b in ex.map(job, ids):
                if not b:
                    continue
                p_close = no_vig_home(b["home_ml"], b["away_ml"])
                rows.append({"game_id": gid, "source": "espn", "ref": b["provider"], "home_ml": b["home_ml"], "away_ml": b["away_ml"],
                             "home_ml_open": b["home_ml_open"], "away_ml_open": b["away_ml_open"],
                             "p_home_pregame": p_close if gid in final else None, "p_home_live": None if gid in final else p_close,
                             "closed": gid in final, "fetched_at": now,
                             "source_id": source_id, "ingestion_run_id": run_id, "source_updated_at": now})
        if rows:
            with engine.begin() as c:
                stmt = insert(nm.NflMarketPrice).values(rows)
                c.execute(stmt.on_conflict_do_update(index_elements=["game_id", "source"], set_={
                    k: stmt.excluded[k] for k in rows[0] if k not in ("game_id", "source")}))
        return {"games_checked": len(ids), "with_open_close": len(rows), "_source_updated_at": now}

    return tracked(engine, "espn_nfl_odds", "nfl_espn_odds", {"backfill_from_season": backfill_from_season}, run, retries=retries)

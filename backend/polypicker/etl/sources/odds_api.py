"""The Odds API (the-odds-api.com) -> nba_odds_books: Pinnacle plus the major US books for upcoming games.

Pinnacle is the sharpest moneyline in the market (low margin, takes big bets, moves first), which ESPN's
feed doesn't carry. One call returns every upcoming NBA game; with ``bookmakers`` given (<= 10) and only
the h2h market it costs 1 credit. The free plan has 500 credits a month, so calls are throttled
(``MIN_GAP``) and stop when fewer than ``RESERVE`` credits remain. Needs ``ODDS_API_KEY``.

Live only: historical odds are a paid feature, so Pinnacle has no backtest yet -- it joins the market
consensus (polypicker/ml/pipeline.py) on the strength of its track record and should be re-checked
once a few hundred games are graded.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import polars as pl
from sqlalchemy import Engine, text

from polypicker.config import get_settings
from polypicker.db import models as m
from polypicker.etl import load
from polypicker.etl.issues import IssueLog
from polypicker.nfl.etl.runner import tracked

URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
BOOKMAKERS = ("pinnacle", "draftkings", "fanduel", "betmgm", "williamhill_us", "betrivers", "bovada",
              "betonlineag", "lowvig", "betfair_ex_eu")
MIN_GAP = timedelta(minutes=45)
RESERVE = 25
MATCH_WINDOW = timedelta(hours=6)

log = logging.getLogger(__name__)


def http_get(key: str) -> tuple[list[dict], dict]:
    import requests
    r = requests.get(URL, params={"apiKey": key, "markets": "h2h", "oddsFormat": "american",
                                  "bookmakers": ",".join(BOOKMAKERS)}, timeout=30)
    r.raise_for_status()
    return r.json(), {k: r.headers.get(k) for k in ("x-requests-remaining", "x-requests-used", "x-requests-last")}


def _ts(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def parse(events: list[dict], games: list[dict]) -> tuple[list[dict], int]:
    """-> nba_odds_books rows (one per game x bookmaker), unmatched event count."""
    rows, unmatched = [], 0
    for ev in events:
        start = _ts(ev["commence_time"])
        g = next((g for g in games if g["home"] == ev["home_team"].lower() and g["away"] == ev["away_team"].lower()
                  and abs(g["tipoff_utc"] - start) <= MATCH_WINDOW), None)
        if g is None:
            unmatched += 1
            continue
        for bk in ev.get("bookmakers", []):
            mk = next((x for x in bk.get("markets", []) if x.get("key") == "h2h"), None)
            prices = {o["name"].lower(): o.get("price") for o in (mk or {}).get("outcomes", [])}
            h, a = prices.get(g["home"]), prices.get(g["away"])
            if h is None or a is None:
                continue
            rows.append({"game_id": g["game_id"], "provider": bk.get("title", bk["key"])[:64],
                         "home_ml": int(h), "away_ml": int(a), "is_closing": False,
                         "fetched_at": datetime.now(timezone.utc)})
    return rows, unmatched


def sync_odds_api(engine: Engine, *, retries: int = 2, get=http_get, force: bool = False) -> dict:
    key = get_settings().odds_api_key
    if not key:
        return {"skipped": "ODDS_API_KEY not set"}
    with engine.connect() as conn:
        last = conn.execute(text("""
            SELECT max(r.started_at), (array_agg(r.stats ORDER BY r.started_at DESC))[1]
            FROM ingestion_runs r WHERE r.job = 'odds_api' AND r.status = 'succeeded'
              AND r.stats ? 'credits_remaining'""")).one()
    if not force and last[0] is not None and datetime.now(timezone.utc) - last[0] < MIN_GAP:
        return {"skipped": f"last call {last[0]:%H:%M} UTC (< {MIN_GAP.seconds // 60} min ago)"}
    remaining = (last[1] or {}).get("credits_remaining")
    if not force and remaining is not None and int(remaining) < RESERVE:
        return {"skipped": f"only {remaining} credits left this month"}

    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        events, quota = get(key)
        now = datetime.now(timezone.utc)
        with engine.connect() as conn:
            games = [dict(r._mapping) for r in conn.execute(text("""
                SELECT g.game_id, g.tipoff_utc, lower(h.full_name) home, lower(a.full_name) away
                FROM games g JOIN teams h ON h.team_id = g.home_team_id JOIN teams a ON a.team_id = g.away_team_id
                WHERE g.tipoff_utc BETWEEN :a AND :b"""), {"a": now - timedelta(hours=12), "b": now + timedelta(days=8)})]
        rows, unmatched = parse(events, games)
        if unmatched:
            issues.add("odds_api", None, "unmatched_event", "info", "skipped", count=unmatched)
        if rows:  # first sighting of a book's line for a game = its opening price here
            with engine.connect() as conn:
                seen = {(r.game_id, r.provider): (r.home_ml_open, r.away_ml_open, r.source_id) for r in conn.execute(text(
                    "SELECT game_id, provider, home_ml_open, away_ml_open, source_id FROM nba_odds_books "
                    "WHERE game_id = ANY(:g)"), {"g": list({r["game_id"] for r in rows})})}
            # a book ESPN already reports for that game (DraftKings, ...) keeps ESPN's fuller row (spread, total, close)
            rows = [r for r in rows if seen.get((r["game_id"], r["provider"]), (None, None, source_id))[2] == source_id]
            for r in rows:
                ho, ao, _ = seen.get((r["game_id"], r["provider"]), (None, None, None))
                r["home_ml_open"], r["away_ml_open"] = (ho, ao) if ho is not None else (r["home_ml"], r["away_ml"])
        df = pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()
        with engine.begin() as conn:
            res = load.upsert(conn, m.NbaOddsBook.__table__, df,
                              provenance={"source_id": source_id, "ingestion_run_id": run_id, "source_updated_at": now})
        return {"events": len(events), "lines": len(rows), "unmatched_events": unmatched,
                "pinnacle_games": len({r["game_id"] for r in rows if r["provider"] == "Pinnacle"}),
                "credits_remaining": quota.get("x-requests-remaining"), "credits_used": quota.get("x-requests-used"),
                **res}

    return tracked(engine, "odds_api", "odds_api", {}, run, retries=retries)

"""ESPN soccer betting lines -> soccer_odds (full-time result + total goals, median across sportsbooks).

ESPN's core API lists every book it tracks per event (soccer event id = our game_id). Coverage measured
2026-11-23 on random samples: top-5 leagues and the UEFA cups ~100% from 2019-20 on, Libertadores /
Sudamericana / LigaPro from 2022. Unofficial endpoints: failures are logged and leave a match without a line.

Skipped entries: provider 2000 ("Bet 365" bet-slip feed, which shows settled/in-play prices for past matches),
any "Live Odds" feed, books with an incomplete 1X2 or an implausible margin, and books whose headline price has
moved off their recorded closing price. A book's headline ``moneyLine`` is its *current* price: for some matches
ESPN captured it in play (Chelsea-Brighton: -5000 headline vs a -130 close). Home/away carry a separate
``close`` value but the draw doesn't, so such a book's whole 1X2 is dropped rather than half-repaired.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from statistics import median

import polars as pl
from sqlalchemy import Engine, text

from polypicker.etl import load
from polypicker.etl.issues import IssueLog
from polypicker.etl.sources.espn_odds import Getter, american_to_decimal, http_get
from polypicker.nfl.etl.runner import tracked
from polypicker.soccer import models as sm
from polypicker.soccer.leagues import League

CORE = "https://sports.core.api.espn.com/v2/sports/soccer/leagues/{comp}/events/{eid}/competitions/{eid}/odds"
SKIP_PROVIDER_IDS = {"2000"}
MIN_OVERROUND, MAX_OVERROUND = 0.99, 1.25   # outside this the prices are stale/in-play/garbage
WORKERS = 12

log = logging.getLogger(__name__)


def _dec(v) -> float | None:
    try:
        return american_to_decimal(float(v)) if v is not None else None
    except (TypeError, ValueError):
        return None


def _moved_off_close(item: dict) -> bool:
    """True if the headline home/away price differs from the book's recorded closing price (in-play capture)."""
    for k in ("homeTeamOdds", "awayTeamOdds"):
        side = item.get(k) or {}
        close = ((side.get("close") or {}).get("moneyLine") or {})
        c = close.get("american") or close.get("alternateDisplayValue")
        if c is None:
            continue
        try:
            close_dec, head_dec = _dec(float(str(c).replace("+", ""))), _dec(side.get("moneyLine"))
        except ValueError:
            continue
        if close_dec is None or head_dec is None or abs(1 / close_dec - 1 / head_dec) > 0.005:
            return True
    return False


def parse_book(item: dict) -> dict | None:
    """One sportsbook's pre-match 1X2 (+ total) with the margin removed, or None if unusable."""
    prov = item.get("provider") or {}
    name = prov.get("name") or ""
    if str(prov.get("id")) in SKIP_PROVIDER_IDS or "live" in name.lower() or name.lower() == "bloomberg":
        return None
    if _moved_off_close(item):
        return None
    d = [_dec((item.get(k) or {}).get("moneyLine")) for k in ("homeTeamOdds", "drawOdds", "awayTeamOdds")]
    if any(x is None or x <= 1.0 for x in d):
        return None
    inv = [1 / x for x in d]
    over = sum(inv)
    if not MIN_OVERROUND <= over <= MAX_OVERROUND:
        return None
    out = {"provider": name, "p_home": inv[0] / over, "p_draw": inv[1] / over, "p_away": inv[2] / over,
           "total_line": None, "p_over": None}
    line, o, u = item.get("overUnder"), _dec(item.get("overOdds")), _dec(item.get("underOdds"))
    if line is not None and o and u and o > 1 and u > 1:
        io, iu = 1 / o, 1 / u
        if MIN_OVERROUND <= io + iu <= MAX_OVERROUND:
            out["total_line"], out["p_over"] = float(line), io / (io + iu)
    return out


def consensus(items: list[dict]) -> dict | None:
    """Median of the usable books (renormalised); total from the books quoting the most common line."""
    books = [b for b in (parse_book(i) for i in items) if b]
    if not books:
        return None
    ph, pd_, pa = (median(b[k] for b in books) for k in ("p_home", "p_draw", "p_away"))
    s = ph + pd_ + pa
    out = {"providers": ",".join(sorted({b["provider"] for b in books}))[:200], "n_books": len(books),
           "p_home": ph / s, "p_draw": pd_ / s, "p_away": pa / s, "total_line": None, "p_over": None}
    lines = [b["total_line"] for b in books if b["total_line"] is not None]
    if lines:
        main = max(set(lines), key=lines.count)
        out["total_line"] = main
        out["p_over"] = median(b["p_over"] for b in books if b["total_line"] == main)
    return out


def sync_odds(engine: Engine, league: League, *, backfill_from_season: int | None = None, back_days: int = 3,
              ahead_days: int = 10, force: bool = False, retries: int = 2, get: Getter = http_get) -> dict:
    """Default: matches from ``back_days`` ago to ``ahead_days`` ahead (closing lines for recent matches, current
    lines for upcoming ones). backfill_from_season: every finished match from that season on without a closing line
    (``force``: re-fetch those that have one too). A re-checked match with no usable book loses its stored line."""
    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        now = datetime.now(timezone.utc)
        with engine.connect() as c:
            if backfill_from_season is not None:
                todo = c.execute(text("""
                    SELECT g.game_id, g.kickoff_utc FROM soccer_games g LEFT JOIN soccer_odds o USING (game_id)
                    WHERE g.competition = :c AND g.season >= :s AND g.status = 'final' AND (:f OR NOT coalesce(o.is_closing, false))
                    ORDER BY g.kickoff_utc"""), {"c": league.competition, "s": backfill_from_season, "f": force}).all()
            else:
                todo = c.execute(text("""
                    SELECT game_id, kickoff_utc FROM soccer_games
                    WHERE competition = :c AND status <> 'canceled' AND kickoff_utc BETWEEN :a AND :b"""),
                    {"c": league.competition, "a": now - timedelta(days=back_days), "b": now + timedelta(days=ahead_days)}).all()

        def job(r):
            try:
                return r, get(CORE.format(comp=league.competition, eid=r.game_id), None)
            except Exception as e:  # noqa: BLE001
                return r, e

        rows, failed, empty, empty_ids = [], 0, 0, []
        with ThreadPoolExecutor(WORKERS) as ex:
            for r, res in ex.map(job, todo):
                if isinstance(res, Exception):
                    failed += 1
                    if "404" not in str(res):
                        issues.add("soccer_odds", str(r.game_id), "fetch_failed", "warning", "skipped", error=str(res)[:200])
                    continue
                cons = consensus(res.get("items") or [])
                if cons is None:
                    empty += 1
                    empty_ids.append(r.game_id)
                    continue
                rows.append({"game_id": r.game_id, "competition": league.competition, **cons,
                             "is_closing": r.kickoff_utc <= now, "fetched_at": now})
        prov = {"source_id": source_id, "ingestion_run_id": run_id, "source_updated_at": now}
        df = pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()
        with engine.begin() as c:
            res = load.upsert(c, sm.SoccerOdds.__table__, df, provenance=prov)
            removed = c.execute(text("DELETE FROM soccer_odds WHERE game_id = ANY(:ids)"), {"ids": empty_ids}).rowcount \
                if empty_ids else 0
        return {"league": league.slug, "matches_checked": len(todo), "with_odds": len(rows), "no_odds": empty,
                "removed_stale": removed, "failed": failed, **res}

    return tracked(engine, "espn_soccer_odds", "soccer_odds",
                   {"league": league.slug, "backfill_from_season": backfill_from_season}, run, retries=retries)

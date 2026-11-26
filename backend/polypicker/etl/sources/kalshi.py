"""Kalshi NBA game-winner markets -> kalshi_nba.

* Series ``KXNBAGAME``: one event per game, ticker ``KXNBAGAME-26NOV23SASSAC`` (ET date + away + home
  team codes, the same codes as ours), with one binary market per team (``...-SAS``, ``...-SAC``).
* Open / recently settled markets come from ``/markets``; markets settled before Kalshi's historical
  cutoff (a couple of months back) only from ``/historical/markets`` (field names without ``_dollars``).
* Pregame price: the home market's 1-minute candles in the 6 hours before tip-off, last candle at or
  before tip -- mid of the closing bid/ask when the spread is tight, else the last trade.

Public read-only API, no key. Coverage: April 2025 onward (all of 2025-26).
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import polars as pl
from sqlalchemy import Engine, text

from polypicker.db import models as m
from polypicker.etl import load
from polypicker.etl.issues import IssueLog
from polypicker.etl.sources.polymarket import http_get
from polypicker.nfl.etl.runner import tracked

API = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXNBAGAME"
PREGAME_WINDOW = timedelta(hours=6)
MAX_SPREAD = 0.06          # use the bid/ask mid only when the book is this tight
TICKER = re.compile(r"^KXNBAGAME-(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})$")
MONTHS = {k: i + 1 for i, k in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}

log = logging.getLogger(__name__)
Getter = Callable[[str, dict | None], dict]


def parse_event(event_ticker: str) -> tuple[date, str, str] | None:
    """'KXNBAGAME-26NOV23SASSAC' -> (2026-11-23, away 'SAS', home 'SAC')."""
    x = TICKER.match(event_ticker)
    if not x or x[2] not in MONTHS:
        return None
    try:
        return date(2000 + int(x[1]), MONTHS[x[2]], int(x[3])), x[4], x[5]
    except ValueError:
        return None


def _num(d: dict | None, key: str) -> float | None:
    """Read a price field from either API generation ('close_dollars' or 'close')."""
    if not d:
        return None
    for k in (f"{key}_dollars", key):
        v = d.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


def mid_price(bid: float | None, ask: float | None, last: float | None) -> float | None:
    if bid is not None and ask is not None and 0 < bid <= ask < 1 and ask - bid <= MAX_SPREAD:
        return (bid + ask) / 2
    return last if last is not None and 0 < last < 1 else None


def list_markets(get: Getter, *, historical: bool, max_pages: int = 100, series: str = SERIES, **params) -> list[dict]:
    out, cursor = [], None
    path = "/historical/markets" if historical else "/markets"
    for _ in range(max_pages):
        p = {"series_ticker": series, "limit": 1000, **params}
        if cursor:
            p["cursor"] = cursor
        d = get(API + path, p)
        out += [dict(mk, _historical=historical) for mk in d.get("markets", [])]
        cursor = d.get("cursor")
        if not cursor or not d.get("markets"):
            break
    return out


def pregame_price(get: Getter, mk: dict, tipoff: datetime, series: str = SERIES) -> tuple[float | None, datetime | None]:
    t1 = int(tipoff.timestamp())
    path = (f"/historical/markets/{mk['ticker']}/candlesticks" if mk["_historical"]
            else f"/series/{series}/markets/{mk['ticker']}/candlesticks")
    d = get(API + path, {"start_ts": t1 - int(PREGAME_WINDOW.total_seconds()), "end_ts": t1, "period_interval": 1})
    for c in sorted(d.get("candlesticks", []), key=lambda c: -c.get("end_period_ts", 0)):
        if c.get("end_period_ts", t1 + 1) > t1:
            continue
        p = mid_price(_num(c.get("yes_bid"), "close"), _num(c.get("yes_ask"), "close"), _num(c.get("price"), "close"))
        if p is not None:
            return p, datetime.fromtimestamp(c["end_period_ts"], tz=timezone.utc)
    return None, None


def sync_kalshi(engine: Engine, *, backfill: bool = False, retries: int = 2, get: Getter = http_get,
                workers: int = 4) -> dict:
    """Default: open markets (live prices) + recently settled ones (pregame price). ``backfill``: also
    every market in Kalshi's historical archive (all NBA games since April 2025)."""
    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        now = datetime.now(timezone.utc)
        since = date(2025, 4, 1) if backfill else (now - timedelta(days=4)).date()
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT g.game_id, g.game_date_et, g.tipoff_utc, h.abbreviation home, a.abbreviation away,
                       k.p_home_pregame
                FROM games g JOIN teams h ON h.team_id = g.home_team_id JOIN teams a ON a.team_id = g.away_team_id
                LEFT JOIN kalshi_nba k USING (game_id)
                WHERE g.season_type IN ('regular', 'playoffs', 'play_in', 'cup_final') AND g.game_date_et >= :since"""),
                {"since": since}).all()
        games = {(r.game_date_et, r.away, r.home): dict(r._mapping) for r in rows}
        have_pregame = {g["game_id"] for g in games.values() if g["p_home_pregame"] is not None}

        markets = list_markets(get, historical=False, status="open")
        markets += list_markets(get, historical=False, status="settled",
                                min_close_ts=int(datetime.combine(since, datetime.min.time(), timezone.utc).timestamp()))
        if backfill:
            markets += list_markets(get, historical=True)

        recs, unmatched = {}, 0
        for mk in markets:
            ev = parse_event(mk.get("event_ticker", ""))
            g = games.get(ev) if ev else None
            if g is None:
                unmatched += 1
                continue
            if not mk["ticker"].endswith("-" + g["home"]):
                continue  # the away team's market is the mirror image
            settled = mk.get("result") in ("yes", "no") or mk.get("status") in ("settled", "finalized", "closed")
            live = None if settled else mid_price(_num(mk, "yes_bid"), _num(mk, "yes_ask"), _num(mk, "last_price"))
            recs[g["game_id"]] = {
                "game_id": g["game_id"], "event_ticker": mk["event_ticker"], "ticker_home": mk["ticker"],
                "p_home_live": live, "p_home_pregame": None, "pregame_at": None,
                "volume": _num(mk, "volume_fp") or _num(mk, "volume"),
                "closed": settled, "_tipoff": g["tipoff_utc"], "_mk": mk}

        need = [r for r in recs.values() if r["_tipoff"] <= now and r["game_id"] not in have_pregame]

        def job(r):
            try:
                return r["game_id"], pregame_price(get, r["_mk"], r["_tipoff"])
            except Exception as e:  # noqa: BLE001
                issues.add("kalshi", r["game_id"], "history_failed", "warning", "skipped", error=str(e)[:200])
                return r["game_id"], (None, None)

        with ThreadPoolExecutor(workers) as ex:
            for gid, (p, at) in ex.map(job, need):
                recs[gid].update(p_home_pregame=p, pregame_at=at)
        keep = [r for r in recs.values() if r["game_id"] in have_pregame]
        if keep:  # don't null pregame prices fetched on an earlier run
            with engine.connect() as conn:
                old = {r.game_id: (r.p_home_pregame, r.pregame_at) for r in conn.execute(text(
                    "SELECT game_id, p_home_pregame, pregame_at FROM kalshi_nba WHERE game_id = ANY(:ids)"),
                    {"ids": [r["game_id"] for r in keep]})}
            for r in keep:
                p_old, at_old = old.get(r["game_id"], (None, None))
                r["p_home_pregame"], r["pregame_at"] = p_old, at_old.astimezone(timezone.utc) if at_old else None
        df = pl.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in recs.values()],
                          infer_schema_length=None) if recs else pl.DataFrame()
        if df.height:
            df = df.with_columns(pl.lit(now).alias("fetched_at"))
        with engine.begin() as conn:
            res = load.upsert(conn, m.KalshiNba.__table__, df,
                              provenance={"source_id": source_id, "ingestion_run_id": run_id, "source_updated_at": now})
        return {"markets_seen": len(markets), "matched_games": len(recs), "unmatched_markets": unmatched,
                "pregame_prices_fetched": sum(1 for r in need if recs[r["game_id"]]["p_home_pregame"] is not None),
                **res}

    return tracked(engine, "kalshi", "kalshi_nba", {"backfill": backfill}, run, retries=retries)

"""Outside NFL win prices -> nfl_market_prices: Kalshi, Polymarket, and Pinnacle + books (The Odds API).

* Kalshi series ``KXNFLGAME`` (Aug 2025 on): event ``KXNFLGAME-25SEP07KCLAC`` = ET date + away + home codes,
  one market per team (``...-KC``). NFL codes are 2-3 letters, so the two teams come from the market
  suffixes and the order from the event ticker.
* Polymarket tag 450 (NFL) moneyline markets (2024 on), matched by team nicknames + kickoff.
* The Odds API ``americanfootball_nfl`` h2h (1 credit per call; NFL games cluster on Thu/Sun/Mon).

Same pregame-price conventions as the NBA feeds (polypicker/etl/sources/kalshi.py, polymarket.py).
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import polars as pl
from sqlalchemy import Engine, text

from polypicker.config import get_settings
from polypicker.etl import load
from polypicker.etl.issues import IssueLog
from polypicker.etl.sources import kalshi as K
from polypicker.etl.sources import polymarket as PM
from polypicker.etl.sources.espn_odds import no_vig_home
from polypicker.nfl import models as nm
from polypicker.nfl.etl.runner import tracked

KALSHI_SERIES = "KXNFLGAME"
POLYMARKET_TAG = 450
KALSHI_ALIASES = {"JAC": "JAX", "LAR": "LA"}
ODDS_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
ODDS_MIN_GAP = timedelta(minutes=45)
ODDS_RESERVE = 25
EVENT = re.compile(r"^KXNFLGAME-(\d{2})([A-Z]{3})(\d{2})([A-Z]+)$")

log = logging.getLogger(__name__)


def parse_kalshi_event(event_ticker: str, side_codes: set[str]) -> tuple[date, str, str] | None:
    """('KXNFLGAME-25SEP07KCLAC', {'KC', 'LAC'}) -> (2025-09-07, away 'KC', home 'LAC'), in our codes."""
    x = EVENT.match(event_ticker)
    if not x or x[2] not in K.MONTHS or len(side_codes) != 2:
        return None
    a, b = sorted(side_codes)
    away_home = (a, b) if x[4] == a + b else (b, a) if x[4] == b + a else None
    if away_home is None:
        return None
    try:
        d = date(2000 + int(x[1]), K.MONTHS[x[2]], int(x[3]))
    except ValueError:
        return None
    return d, KALSHI_ALIASES.get(away_home[0], away_home[0]), KALSHI_ALIASES.get(away_home[1], away_home[1])


def _games(engine: Engine, since: datetime) -> list[dict]:
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text("""
            SELECT g.game_id, g.game_date_et, g.kickoff_utc, h.abbreviation home, a.abbreviation away,
                   lower(h.name) home_name, lower(a.name) away_name, lower(h.full_name) home_full, lower(a.full_name) away_full
            FROM nfl_games g JOIN nfl_teams h ON h.team_id = g.home_team_id JOIN nfl_teams a ON a.team_id = g.away_team_id
            WHERE g.kickoff_utc >= :since"""), {"since": since})]


def _keep_old_pregame(engine: Engine, recs: dict, have: set) -> None:
    keep = [k for k in recs if k in have]
    if not keep:
        return
    with engine.connect() as conn:
        old = {(r.game_id, r.source): (r.p_home_pregame, r.pregame_at) for r in conn.execute(text(
            "SELECT game_id, source, p_home_pregame, pregame_at FROM nfl_market_prices WHERE game_id = ANY(:g)"),
            {"g": list({k[0] for k in keep})})}
    for k in keep:
        p, at = old.get(k, (None, None))
        recs[k]["p_home_pregame"], recs[k]["pregame_at"] = p, at.astimezone(timezone.utc) if at else None


def _upsert(engine: Engine, recs: dict, run_id: int, source_id: int, now: datetime) -> dict:
    df = pl.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in recs.values()],
                      infer_schema_length=None) if recs else pl.DataFrame()
    if df.height:
        df = df.with_columns(pl.lit(now).alias("fetched_at"))
    with engine.begin() as conn:
        return load.upsert(conn, nm.NflMarketPrice.__table__, df,
                           provenance={"source_id": source_id, "ingestion_run_id": run_id, "source_updated_at": now})


def _have_pregame(engine: Engine, source: str) -> set:
    with engine.connect() as conn:
        return {(r.game_id, source) for r in conn.execute(text(
            "SELECT game_id FROM nfl_market_prices WHERE source = :s AND p_home_pregame IS NOT NULL"), {"s": source})}


# ---------------------------------------------------------------- Kalshi

def sync_nfl_kalshi(engine: Engine, *, backfill: bool = False, retries: int = 2, get=PM.http_get, workers: int = 4) -> dict:
    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        now = datetime.now(timezone.utc)
        since = datetime(2025, 7, 1, tzinfo=timezone.utc) if backfill else now - timedelta(days=4)
        games = {(g["game_date_et"], g["away"], g["home"]): g for g in _games(engine, since - timedelta(days=1))}
        have = _have_pregame(engine, "kalshi")
        markets = K.list_markets(get, historical=False, series=KALSHI_SERIES, status="open")
        markets += K.list_markets(get, historical=False, series=KALSHI_SERIES, status="settled",
                                  min_close_ts=int(since.timestamp()))
        if backfill:
            markets += K.list_markets(get, historical=True, series=KALSHI_SERIES)
        by_event: dict[str, list[dict]] = {}
        for mk in markets:
            by_event.setdefault(mk.get("event_ticker", ""), []).append(mk)
        recs, unmatched = {}, 0
        for ev, mks in by_event.items():
            sides = {mk["ticker"].rsplit("-", 1)[-1]: mk for mk in mks}
            parsed = parse_kalshi_event(ev, set(sides))
            g = games.get(parsed) if parsed else None
            if g is None:
                unmatched += 1
                continue
            home_code = next((c for c in sides if KALSHI_ALIASES.get(c, c) == g["home"]), None)
            mk = sides[home_code]
            settled = mk.get("result") in ("yes", "no") or mk.get("status") in ("settled", "finalized", "closed")
            recs[(g["game_id"], "kalshi")] = {
                "game_id": g["game_id"], "source": "kalshi", "ref": mk["ticker"][:160],
                "p_home_live": None if settled else K.mid_price(K._num(mk, "yes_bid"), K._num(mk, "yes_ask"),
                                                                 K._num(mk, "last_price")),
                "p_home_pregame": None, "pregame_at": None, "volume": K._num(mk, "volume_fp") or K._num(mk, "volume"),
                "closed": settled, "_kick": g["kickoff_utc"], "_mk": mk}
        need = [r for k, r in recs.items() if r["_kick"] <= now and k not in have]

        def job(r):
            try:
                return (r["game_id"], "kalshi"), K.pregame_price(get, r["_mk"], r["_kick"], series=KALSHI_SERIES)
            except Exception as e:  # noqa: BLE001
                issues.add("nfl_kalshi", r["game_id"], "history_failed", "warning", "skipped", error=str(e)[:200])
                return (r["game_id"], "kalshi"), (None, None)

        with ThreadPoolExecutor(workers) as ex:
            for k, (p, at) in ex.map(job, need):
                recs[k].update(p_home_pregame=p, pregame_at=at)
        _keep_old_pregame(engine, recs, have)
        res = _upsert(engine, recs, run_id, source_id, now)
        return {"markets_seen": len(markets), "matched_games": len(recs), "unmatched_events": unmatched,
                "pregame_prices_fetched": sum(1 for r in need if r["p_home_pregame"] is not None), **res}

    return tracked(engine, "kalshi", "nfl_kalshi", {"backfill": backfill}, run, retries=retries)


# ---------------------------------------------------------------- Polymarket

def sync_nfl_polymarket(engine: Engine, *, backfill: bool = False, retries: int = 2, get=PM.http_get,
                        workers: int = 3) -> dict:
    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        now = datetime.now(timezone.utc)
        since = datetime(2024, 7, 1, tzinfo=timezone.utc) if backfill else now - timedelta(days=4)
        games = [{**g, "tipoff_utc": g["kickoff_utc"], "home_name": g["home_name"], "away_name": g["away_name"]}
                 for g in _games(engine, since - PM.MATCH_WINDOW)]
        have = _have_pregame(engine, "polymarket")
        markets = PM.list_markets(get, closed=False, tag=POLYMARKET_TAG)
        markets += PM.list_markets(get, closed=True, since=since, tag=POLYMARKET_TAG)
        recs, unmatched = {}, 0
        for mk in markets:
            hit = PM.match(mk, games)
            if not hit:
                unmatched += 1
                continue
            g, ih, ia = hit
            tokens, prices = PM._jl(mk.get("clobTokenIds")), PM._jl(mk.get("outcomePrices"))
            if len(tokens) != 2:
                continue
            closed = bool(mk.get("closed"))
            recs[(g["game_id"], "polymarket")] = {
                "game_id": g["game_id"], "source": "polymarket", "ref": (mk.get("slug") or str(mk["id"]))[:160],
                "p_home_live": None if closed or len(prices) != 2 else PM._f(prices[ih]),
                "p_home_pregame": None, "pregame_at": None, "volume": PM._f(mk.get("volume")), "closed": closed,
                "_kick": g["kickoff_utc"], "_token": tokens[ih]}
        need = [r for k, r in recs.items() if r["_kick"] <= now and k not in have]

        def job(r):
            try:
                return (r["game_id"], "polymarket"), PM.pregame_price(get, r["_token"], r["_kick"])
            except Exception as e:  # noqa: BLE001
                issues.add("nfl_polymarket", r["game_id"], "history_failed", "warning", "skipped", error=str(e)[:200])
                return (r["game_id"], "polymarket"), (None, None)

        with ThreadPoolExecutor(workers) as ex:
            for k, (p, at) in ex.map(job, need):
                recs[k].update(p_home_pregame=p, pregame_at=at)
        _keep_old_pregame(engine, recs, have)
        res = _upsert(engine, recs, run_id, source_id, now)
        return {"markets_seen": len(markets), "matched_games": len(recs), "unmatched_markets": unmatched,
                "pregame_prices_fetched": sum(1 for r in need if r["p_home_pregame"] is not None), **res}

    return tracked(engine, "polymarket", "nfl_polymarket", {"backfill": backfill}, run, retries=retries)


# ---------------------------------------------------------------- The Odds API (Pinnacle + books)

def _odds_get(key: str) -> tuple[list[dict], dict]:
    import requests

    from polypicker.etl.sources.odds_api import BOOKMAKERS
    r = requests.get(ODDS_URL, params={"apiKey": key, "markets": "h2h", "oddsFormat": "american",
                                       "bookmakers": ",".join(BOOKMAKERS)}, timeout=30)
    r.raise_for_status()
    return r.json(), {k: r.headers.get(k) for k in ("x-requests-remaining", "x-requests-used")}


def sync_nfl_odds_api(engine: Engine, *, force: bool = False, retries: int = 2, get=_odds_get) -> dict:
    key = get_settings().odds_api_key
    if not key:
        return {"skipped": "ODDS_API_KEY not set"}
    with engine.connect() as conn:  # throttle + credit reserve are shared with the NBA calls
        last_nfl = conn.execute(text("SELECT max(started_at) FROM ingestion_runs WHERE job = 'nfl_odds_api' "
                                     "AND status = 'succeeded'")).scalar()
        remaining = conn.execute(text("""SELECT stats->>'credits_remaining' FROM ingestion_runs
            WHERE job IN ('odds_api', 'nfl_odds_api') AND status = 'succeeded' AND stats ? 'credits_remaining'
            ORDER BY started_at DESC LIMIT 1""")).scalar()
    if not force and last_nfl and datetime.now(timezone.utc) - last_nfl < ODDS_MIN_GAP:
        return {"skipped": f"last call {last_nfl:%H:%M} UTC"}
    if not force and remaining is not None and int(remaining) < ODDS_RESERVE:
        return {"skipped": f"only {remaining} credits left this month"}

    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        events, quota = get(key)
        now = datetime.now(timezone.utc)
        games = _games(engine, now - timedelta(hours=12))
        recs, unmatched = {}, 0
        with engine.connect() as conn:
            opened = {(r.game_id, r.source): (r.home_ml_open, r.away_ml_open) for r in conn.execute(text(
                "SELECT game_id, source, home_ml_open, away_ml_open FROM nfl_market_prices WHERE home_ml_open IS NOT NULL"))}
        for ev in events:
            start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            g = next((g for g in games if g["home_full"] == ev["home_team"].lower()
                      and g["away_full"] == ev["away_team"].lower() and abs(g["kickoff_utc"] - start) <= timedelta(hours=6)), None)
            if g is None:
                unmatched += 1
                continue
            for bk in ev.get("bookmakers", []):
                mk = next((x for x in bk.get("markets", []) if x.get("key") == "h2h"), None)
                prices = {o["name"].lower(): o.get("price") for o in (mk or {}).get("outcomes", [])}
                h, a = prices.get(g["home_full"]), prices.get(g["away_full"])
                if h is None or a is None:
                    continue
                src = bk.get("title", bk["key"])[:32]
                ho, ao = opened.get((g["game_id"], src), (int(h), int(a)))
                recs[(g["game_id"], src)] = {"game_id": g["game_id"], "source": src, "ref": ev.get("id"),
                                             "home_ml": int(h), "away_ml": int(a), "home_ml_open": ho, "away_ml_open": ao,
                                             "p_home_live": no_vig_home(h, a), "closed": False}
        res = _upsert(engine, recs, run_id, source_id, now)
        return {"events": len(events), "lines": len(recs), "unmatched_events": unmatched,
                "pinnacle_games": sum(1 for k in recs if k[1] == "Pinnacle"),
                "credits_remaining": quota.get("x-requests-remaining"), **res}

    return tracked(engine, "odds_api", "nfl_odds_api", {}, run, retries=retries)

"""Polymarket NBA moneyline markets -> polymarket_nba.

* Discovery: Gamma ``/markets/keyset?tag_id=745&sports_market_types=moneyline`` (745 = the "nba" tag).
  Each game market has two outcomes (the team nicknames, e.g. ["Trail Blazers", "Knicks"]),
  ``outcomePrices``, ``clobTokenIds`` (same order) and ``gameStartTime``.
* Pregame price: CLOB ``/prices-history`` for the home token, last point at or before tip-off.

Markets are matched to our games by the two team nicknames + a tip-off within 6 hours. Public,
read-only APIs, no key. History reaches back to the 2024 playoffs.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable

import polars as pl
from sqlalchemy import Engine, text

from polypicker.db import models as m
from polypicker.etl import load
from polypicker.etl.issues import IssueLog
from polypicker.nfl.etl.runner import tracked

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
NBA_TAG = 745
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PolyPicker/1.0)", "Accept": "application/json"}
MATCH_WINDOW = timedelta(hours=6)

log = logging.getLogger(__name__)
Getter = Callable[[str, dict | None], dict]


def http_get(url: str, params: dict | None = None, attempts: int = 5) -> dict:
    """GET with polite backoff on 429 (honours Retry-After)."""
    import time

    import requests
    for k in range(attempts):
        r = requests.get(url, params=params, headers=HEADERS, timeout=30)
        if r.status_code == 429 and k < attempts - 1:
            try:
                wait = float(r.headers.get("Retry-After", ""))
            except ValueError:
                wait = 2.0 * (k + 1)
            time.sleep(min(max(wait, 1.0), 30.0))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("unreachable")


def _jl(v) -> list:
    if isinstance(v, list):
        return v
    try:
        return json.loads(v) if v else []
    except (TypeError, ValueError):
        return []


def _ts(v: str | None) -> datetime | None:
    if not v:
        return None
    s = v.strip().replace(" ", "T").replace("Z", "+00:00")
    if s.endswith("+00"):
        s += ":00"
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def list_markets(get: Getter, *, closed: bool, since: datetime | None = None, max_pages: int = 200) -> list[dict]:
    out, cursor = [], None
    for _ in range(max_pages):
        p = {"tag_id": NBA_TAG, "closed": "true" if closed else "false", "limit": 500,
             "sports_market_types": "moneyline"}
        if since:
            p["end_date_min"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")  # server-side narrowing (also filtered below)
        if cursor:
            p["after_cursor"] = cursor
        d = get(f"{GAMMA}/markets/keyset", p)
        for mk in d.get("markets", []):
            start = _ts(mk.get("gameStartTime"))
            if start is None or (since and start < since):
                continue
            out.append(mk)
        cursor = d.get("next_cursor")
        if not cursor:
            break
    return out


def match(mk: dict, games: list[dict]) -> tuple[dict, int, int] | None:
    """-> (game, home_index, away_index) into the market's outcome/token arrays."""
    names = [str(o).strip().lower() for o in _jl(mk.get("outcomes"))]
    start = _ts(mk.get("gameStartTime"))
    if len(names) != 2 or start is None:
        return None
    for g in games:
        if abs(g["tipoff_utc"] - start) > MATCH_WINDOW:
            continue
        if {g["home_name"], g["away_name"]} == set(names):
            return g, names.index(g["home_name"]), names.index(g["away_name"])
    return None


def pregame_price(get: Getter, token: str, tipoff: datetime) -> tuple[float | None, datetime | None]:
    t1 = int(tipoff.timestamp())
    d = get(f"{CLOB}/prices-history", {"market": token, "startTs": t1 - 12 * 3600, "endTs": t1, "fidelity": 5})
    pts = [x for x in d.get("history", []) if x.get("t", 0) <= t1 and x.get("p") is not None]
    if not pts:
        return None, None
    last = max(pts, key=lambda x: x["t"])
    return float(last["p"]), datetime.fromtimestamp(last["t"], tz=timezone.utc)


def sync_polymarket(engine: Engine, *, backfill_since: datetime | None = None, retries: int = 2,
                    get: Getter = http_get, workers: int = 3) -> dict:
    """Default: open markets (live prices) + recently closed ones (pregame price). ``backfill_since``:
    every closed NBA moneyline market whose game started after that date."""
    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        now = datetime.now(timezone.utc)
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT g.game_id, g.tipoff_utc, lower(h.name) home_name, lower(a.name) away_name, p.p_home_pregame
                FROM games g JOIN teams h ON h.team_id = g.home_team_id JOIN teams a ON a.team_id = g.away_team_id
                LEFT JOIN polymarket_nba p USING (game_id)
                WHERE g.season_type IN ('regular', 'playoffs', 'play_in', 'cup_final')
                  AND g.tipoff_utc >= :since""" ), {"since": (backfill_since or now - timedelta(days=4)) - MATCH_WINDOW}).all()
        games = [dict(r._mapping) for r in rows]
        have_pregame = {g["game_id"] for g in games if g["p_home_pregame"] is not None}
        markets = list_markets(get, closed=False)
        markets += list_markets(get, closed=True, since=backfill_since or now - timedelta(days=4))
        recs, unmatched = {}, 0
        for mk in markets:
            hit = match(mk, games)
            if not hit:
                unmatched += 1
                continue
            g, ih, ia = hit
            tokens, prices = _jl(mk.get("clobTokenIds")), _jl(mk.get("outcomePrices"))
            if len(tokens) != 2:
                continue
            ev = (mk.get("events") or [{}])[0]
            closed = bool(mk.get("closed"))
            recs[g["game_id"]] = {
                "game_id": g["game_id"], "market_id": str(mk["id"]), "slug": mk.get("slug", "")[:160],
                "event_slug": (ev.get("slug") or None), "condition_id": mk.get("conditionId"),
                "token_home": tokens[ih], "token_away": tokens[ia],
                "p_home_live": None if closed or len(prices) != 2 else _f(prices[ih]),
                "p_home_pregame": None, "pregame_at": None,
                "volume_usd": _f(mk.get("volume")), "liquidity_usd": _f(mk.get("liquidity")), "closed": closed,
                "_tipoff": g["tipoff_utc"]}

        need = [r for r in recs.values() if r["_tipoff"] <= now and r["game_id"] not in have_pregame]

        def job(r):
            try:
                return r["game_id"], pregame_price(get, r["token_home"], r["_tipoff"])
            except Exception as e:  # noqa: BLE001
                issues.add("polymarket", r["game_id"], "history_failed", "warning", "skipped", error=str(e)[:200])
                return r["game_id"], (None, None)

        with ThreadPoolExecutor(workers) as ex:
            for gid, (p, at) in ex.map(job, need):
                recs[gid].update(p_home_pregame=p, pregame_at=at)
        # keep pregame prices fetched earlier (rows we didn't re-fetch would otherwise be nulled)
        keep = [r for r in recs.values() if r["game_id"] in have_pregame]
        if keep:
            with engine.connect() as conn:
                old = dict((r.game_id, (r.p_home_pregame, r.pregame_at)) for r in conn.execute(text(
                    "SELECT game_id, p_home_pregame, pregame_at FROM polymarket_nba WHERE game_id = ANY(:ids)"),
                    {"ids": [r["game_id"] for r in keep]}))
            for r in keep:
                p_old, at_old = old.get(r["game_id"], (None, None))
                r["p_home_pregame"], r["pregame_at"] = p_old, at_old.astimezone(timezone.utc) if at_old else None
        df = pl.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in recs.values()],
                          infer_schema_length=None) if recs else pl.DataFrame()
        if df.height:
            df = df.with_columns(pl.lit(now).alias("fetched_at"))
        with engine.begin() as conn:
            res = load.upsert(conn, m.PolymarketNba.__table__, df,
                              provenance={"source_id": source_id, "ingestion_run_id": run_id, "source_updated_at": now})
        return {"markets_seen": len(markets), "matched_games": len(recs), "unmatched_markets": unmatched,
                "pregame_prices_fetched": sum(1 for r in need if recs[r["game_id"]]["p_home_pregame"] is not None),
                **res}

    return tracked(engine, "polymarket", "polymarket_nba", {"backfill_since": str(backfill_since)}, run,
                   retries=retries)

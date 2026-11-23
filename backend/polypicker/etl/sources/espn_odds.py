"""ESPN NBA betting lines -> nba_odds (one preferred sportsbook per game).

ESPN's scoreboard (per ET date) gives event ids; the core API's per-event odds list every book it
tracks, with opening and closing (or current) moneyline, spread and total. History reaches back
to at least 2023-24. Events are matched to our games by ET date + home/away team names.

Used as a benchmark for the model ("the market") and, if it earns its place, as an input.
Unofficial endpoints: failures are retried / logged and simply leave a game without a line.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import polars as pl
from sqlalchemy import Engine, text

from polypicker.db import models as m
from polypicker.etl import load
from polypicker.etl.issues import IssueLog
from polypicker.nfl.etl.runner import tracked

SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/{eid}/competitions/{eid}/odds"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PolyPicker/1.0)", "Accept": "application/json"}
TEAM_ALIASES = {"la clippers": "los angeles clippers"}
# Preferred books, in order; "Live Odds" feeds are in-game prices and never used.
PREFERRED = ["DraftKings", "ESPN BET", "FanDuel", "BetMGM", "MGM", "Caesars Sportsbook (New Jersey)",
             "Caesars Sportsbook (Colorado)", "Unibet", "PointsBet", "SugarHouse", "BetfairSportsbook"]

log = logging.getLogger(__name__)
Getter = Callable[[str, dict | None], dict]


def http_get(url: str, params: dict | None = None) -> dict:
    import requests
    r = requests.get(url, params=params, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.json()


def american_to_decimal(ml: float | int | None) -> float | None:
    if ml is None:
        return None
    ml = float(ml)
    if ml == 0:
        return None
    return 1 + ml / 100 if ml > 0 else 1 + 100 / abs(ml)


def no_vig_home(home_ml, away_ml) -> float | None:
    """Moneylines -> P(home wins) with the bookmaker margin removed (normalised implied probabilities)."""
    dh, da = american_to_decimal(home_ml), american_to_decimal(away_ml)
    if not dh or not da:
        return None
    ph, pa = 1 / dh, 1 / da
    return ph / (ph + pa)


def _ml(side: dict, which: str | None = None):
    if which is None:
        v = side.get("moneyLine")
    else:
        v = ((side.get(which) or {}).get("moneyLine") or {}).get("american")
    try:
        return int(float(str(v).replace("+", ""))) if v not in (None, "", "OFF", "EVEN") else (100 if v == "EVEN" else None)
    except ValueError:
        return None


def pick_book(items: list[dict]) -> dict | None:
    books = [i for i in items if "live" not in (i.get("provider", {}).get("name", "").lower())
             and (i.get("homeTeamOdds") or {}).get("moneyLine") is not None]
    if not books:
        return None
    rank = {n: k for k, n in enumerate(PREFERRED)}
    return min(books, key=lambda i: rank.get(i["provider"]["name"], len(PREFERRED)))


def parse_odds(item: dict) -> dict:
    h, a = item.get("homeTeamOdds") or {}, item.get("awayTeamOdds") or {}
    home_ml, away_ml = _ml(h), _ml(a)
    oh, oa = _ml(h, "open"), _ml(a, "open")
    spread = item.get("spread")
    # ESPN's `spread` is the favourite's margin as a positive number; express it as the home handicap.
    spread_home = None
    if spread is not None:
        spread_home = -abs(float(spread)) if h.get("favorite") else abs(float(spread))
    return {"provider": item["provider"]["name"][:64], "home_ml": home_ml, "away_ml": away_ml,
            "open_home_ml": oh, "open_away_ml": oa, "spread_home": spread_home,
            "total": float(item["overUnder"]) if item.get("overUnder") is not None else None,
            "p_home": no_vig_home(home_ml, away_ml), "p_home_open": no_vig_home(oh, oa)}


PROP_TYPES = {"Total Points": "points", "Total Rebounds": "rebounds", "Total Assists": "assists"}


def _num(v) -> float | None:
    try:
        return float(str(v).replace("+", ""))
    except (TypeError, ValueError):
        return None


def parse_book_full(item: dict) -> dict:
    """Every book: opening and closing/current moneyline, spread (home handicap) and total."""
    h, a = item.get("homeTeamOdds") or {}, item.get("awayTeamOdds") or {}
    spread = item.get("spread")
    spread_home = None if spread is None else (-abs(float(spread)) if h.get("favorite") else abs(float(spread)))
    ho = ((h.get("open") or {}).get("pointSpread") or {}).get("american")
    tot_open = (((item.get("open") or {}).get("total") or {}).get("american"))
    return {"provider": item["provider"]["name"][:64],
            "home_ml_open": _ml(h, "open"), "away_ml_open": _ml(a, "open"), "home_ml": _ml(h), "away_ml": _ml(a),
            "spread_home_open": _num(ho), "spread_home": spread_home,
            "total_open": _num(tot_open), "total": float(item["overUnder"]) if item.get("overUnder") is not None else None}


def parse_props(items: list[dict]) -> list[dict]:
    """Over/under pairs for the modelled stats. ESPN lists the over first (verified against 1,232 settled
    props: the first price's implied probability correlates +0.12 with the over hitting)."""
    from collections import defaultdict
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for i in items:
        stat = PROP_TYPES.get((i.get("type") or {}).get("name"))
        ref = (i.get("athlete") or {}).get("$ref")
        if stat and ref:
            groups[(ref.split("?")[0].rstrip("/").split("/")[-1], stat)].append(i)
    out = []
    for (aid, stat), its in groups.items():
        if len(its) != 2:
            continue
        o, u = its
        dec = lambda x, k: _num(((x.get("odds") or {}).get("decimal") or {}).get(k))  # noqa: E731
        tgt = lambda x, k: _num(((x.get(k) or {}).get("target") or {}).get("value"))  # noqa: E731
        out.append({"espn_athlete_id": aid[:16], "stat": stat, "line_open": tgt(o, "open"), "line": tgt(o, "current"),
                    "over_open": dec(o, "open"), "under_open": dec(u, "open"), "over": dec(o, "value"), "under": dec(u, "value")})
    return out


def _events(get: Getter, day: date) -> list[dict]:
    d = get(SITE, {"dates": day.strftime("%Y%m%d"), "limit": 50})
    out = []
    for e in d.get("events", []):
        comp = (e.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): (c.get("team") or {}).get("displayName", "").lower() for c in comp.get("competitors", [])}
        out.append({"eid": str(e["id"]), "home": teams.get("home"), "away": teams.get("away"),
                    "state": ((comp.get("status") or {}).get("type") or {}).get("state")})
    return out


def collect(get: Getter, days: list[date], games: dict[tuple[date, str, str], int], issues: IssueLog,
            workers: int = 6, with_props: bool = True) -> list[dict]:
    """games: (ET date, home full name lower, away full name lower) -> game_id."""
    def day_job(day: date):
        try:
            return day, _events(get, day)
        except Exception as e:  # noqa: BLE001
            issues.add("nba_odds", day.isoformat(), "scoreboard_failed", "warning", "skipped", error=str(e)[:200])
            return day, []

    jobs = []
    with ThreadPoolExecutor(workers) as ex:
        for day, evs in ex.map(day_job, days):
            for e in evs:
                home = TEAM_ALIASES.get(e["home"], e["home"])
                away = TEAM_ALIASES.get(e["away"], e["away"])
                gid = games.get((day, home, away))
                if gid is None:
                    continue  # preseason / exhibition / not in our schedule
                jobs.append((gid, e))

    def odds_job(job):
        gid, e = job
        try:
            items = get(CORE.format(eid=e["eid"]), {"limit": 50}).get("items", [])
        except Exception as ex_:  # noqa: BLE001
            issues.add("nba_odds", gid, "odds_failed", "warning", "skipped", error=str(ex_)[:200])
            return None
        book = pick_book(items)
        if not book:
            return None
        closing = e["state"] in ("in", "post")
        books = [{"game_id": gid, "is_closing": closing, **parse_book_full(i)} for i in items
                 if "live" not in i.get("provider", {}).get("name", "").lower()
                 and (i.get("homeTeamOdds") or {}).get("moneyLine") is not None]
        props, prop_book = [], None
        if with_props:
            rank = {n: k for k, n in enumerate(PREFERRED)}
            for i in sorted((b for b in items if "live" not in b.get("provider", {}).get("name", "").lower()),
                            key=lambda b: rank.get(b["provider"]["name"], len(PREFERRED))):
                try:
                    pitems = get(CORE.format(eid=e["eid"]) + f"/{i['provider']['id']}/propBets", {"limit": 1000}).get("items", [])
                except Exception:  # noqa: BLE001 -- 404 = this book has no props for the game
                    continue
                if pitems:
                    props, prop_book = parse_props(pitems), i["provider"]["name"][:64]
                    break
        return {"line": {"game_id": gid, "espn_event_id": e["eid"], "is_closing": closing, **parse_odds(book)},
                "books": books, "props": [{"game_id": gid, "provider": prop_book, "is_closing": closing, **p} for p in props]}

    with ThreadPoolExecutor(workers) as ex:
        res = [r for r in ex.map(odds_job, jobs) if r]
    return res


def _map_props(engine: Engine, get: Getter, props: list[dict], issues: IssueLog) -> list[dict]:
    """Attach our player_id to each prop via the ESPN athlete id (cached in espn_nba_athletes)."""
    from polypicker.etl.sources.espn_injuries import norm_name
    if not props:
        return []
    ids = sorted({p["espn_athlete_id"] for p in props})
    with engine.connect() as conn:
        known = {r.espn_athlete_id: r.player_id for r in conn.execute(text(
            "SELECT espn_athlete_id, player_id FROM espn_nba_athletes WHERE espn_athlete_id = ANY(:i)"), {"i": ids})}
        league: dict[str, list[int]] = {}
        for r in conn.execute(text("""SELECT DISTINCT pl.player_id, pl.full_name FROM players pl
                                      JOIN player_game_stats p USING (player_id)""")):
            league.setdefault(norm_name(r.full_name), []).append(r.player_id)
    missing = [i for i in ids if i not in known]

    def fetch(aid):
        try:
            a = get(f"https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/athletes/{aid}", None)
            return aid, a.get("displayName") or a.get("fullName") or ""
        except Exception:  # noqa: BLE001
            return aid, None

    new = []
    with ThreadPoolExecutor(6) as ex:
        for aid, name in ex.map(fetch, missing):
            if name is None:
                continue
            cands = league.get(norm_name(name), [])
            pid = cands[0] if len(cands) == 1 else None
            if pid is None:
                issues.add("nba_prop_odds", name, "unmatched_athlete", "info", "skipped", espn_athlete_id=aid)
            known[aid] = pid
            new.append({"espn_athlete_id": aid, "name": name[:128], "player_id": pid})
    if new:
        with engine.begin() as conn:
            load.upsert(conn, m.EspnNbaAthlete.__table__, pl.DataFrame(new, infer_schema_length=None))
    out = []
    for p in props:
        pid = known.get(p["espn_athlete_id"])
        if pid is not None:
            out.append({**p, "player_id": pid})
    seen, uniq = set(), []
    for p in out:
        k = (p["game_id"], p["player_id"], p["stat"])
        if k not in seen:
            seen.add(k); uniq.append(p)
    return uniq


def sync_odds(engine: Engine, *, days: list[date] | None = None, backfill_from_season: int | None = None,
              force: bool = False, retries: int = 2, get: Getter = http_get) -> dict:
    """Default: yesterday..+2 days (closing lines for last night, current lines for upcoming games).
    backfill_from_season: every game date from that season on that has no closing line yet."""
    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        with engine.connect() as conn:
            sched = conn.execute(text("""
                SELECT g.game_id, g.game_date_et, lower(h.full_name) home, lower(a.full_name) away,
                       o.is_closing
                FROM games g JOIN teams h ON h.team_id = g.home_team_id JOIN teams a ON a.team_id = g.away_team_id
                LEFT JOIN nba_odds o USING (game_id)
                WHERE g.season_type IN ('regular', 'playoffs', 'play_in', 'cup_final')""")).all()
        games = {(r.game_date_et, r.home, r.away): r.game_id for r in sched}
        if backfill_from_season is not None:
            with engine.connect() as conn:
                first = conn.execute(text("SELECT min(start_date) FROM seasons WHERE season >= :s"),
                                     {"s": backfill_from_season}).scalar()
            todo = sorted({r.game_date_et for r in sched if (force or not r.is_closing) and first and r.game_date_et >= first
                           and r.game_date_et <= datetime.now(timezone.utc).date() + timedelta(days=2)})
        else:
            today = datetime.now(ZoneInfo("America/New_York")).date()
            todo = days or [today + timedelta(days=k) for k in (-1, 0, 1, 2)]
        res_rows = collect(get, todo, games, issues)
        now = datetime.now(timezone.utc)
        prov = {"source_id": source_id, "ingestion_run_id": run_id, "source_updated_at": now}
        frame = lambda rs: (pl.DataFrame(rs, infer_schema_length=None).with_columns(pl.lit(now).alias("fetched_at"))  # noqa: E731
                            if rs else pl.DataFrame())
        df = frame([r["line"] for r in res_rows])
        books = frame([b for r in res_rows for b in r["books"]])
        if books.height:
            books = books.unique(["game_id", "provider"], keep="first")
        props = _map_props(engine, get, [p for r in res_rows for p in r["props"]], issues)
        with engine.begin() as conn:
            res = load.upsert(conn, m.NbaOdds.__table__, df, provenance=prov)
            res_b = load.upsert(conn, m.NbaOddsBook.__table__, books, provenance=prov)
            res_p = load.upsert(conn, m.NbaPropOdds.__table__, frame(props), provenance=prov)
        return {"dates": len(todo), "games_with_lines": df.height, **res, "book_lines": res_b, "props": res_p,
                "providers": dict(df.group_by("provider").len().iter_rows()) if df.height else {}}

    return tracked(engine, "espn_nba_odds", "nba_odds", {"backfill_from_season": backfill_from_season}, run,
                   retries=retries)

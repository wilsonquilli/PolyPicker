"""The Odds API (the-odds-api.com) -> soccer_odds_snapshots: 1X2 prices from Pinnacle, the Betfair exchange and
European books, kept as a time series (every fetch is a new row) to measure line movement before kickoff.

ESPN's soccer odds (soccer/etl/odds.py) are a consensus, often captured at settlement, so they can't show how a
line moved. One call per competition with ``bookmakers`` (<= 10) and the h2h market costs 1 credit and shares the
free plan's 500 credits a month with the NBA job (etl/sources/odds_api.py): calls are throttled per competition
(``MIN_GAP``) and stop when fewer than ``RESERVE`` credits remain. Needs ``ODDS_API_KEY``. Live only (historical
odds are a paid feature), so a line-movement signal can be tested only after a few weeks of snapshots.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import polars as pl
from sqlalchemy import Engine, text

from polypicker.config import get_settings
from polypicker.etl import load
from polypicker.etl.issues import IssueLog
from polypicker.nfl.etl.runner import tracked
from polypicker.soccer import models as sm
from polypicker.soccer.etl.crosscheck import norm
from polypicker.soccer.leagues import League

URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
SPORTS = {"epl": "soccer_epl", "laliga": "soccer_spain_la_liga", "bundesliga": "soccer_germany_bundesliga",
          "seriea": "soccer_italy_serie_a", "ligue1": "soccer_france_ligue_one", "ucl": "soccer_uefa_champs_league",
          "uel": "soccer_uefa_europa_league"}
DEFAULT_LEAGUES = ("epl",)          # what `soccer daily` snapshots; widen once the credit use is known
BOOKMAKERS = ("pinnacle", "betfair_ex_eu", "matchbook", "williamhill", "unibet_eu", "betsson", "marathonbet",
              "onexbet", "sport888", "nordicbet")
MIN_GAP = timedelta(hours=3)
RESERVE = 60                        # leave room for the NBA job
MATCH_WINDOW = timedelta(hours=6)
JOBS = ("odds_api", "soccer_odds_api")   # both spend the same key's credits

log = logging.getLogger(__name__)


def http_get(key: str, sport: str) -> tuple[list[dict], dict]:
    import requests
    r = requests.get(URL.format(sport=sport), params={"apiKey": key, "markets": "h2h", "oddsFormat": "decimal",
                                                      "bookmakers": ",".join(BOOKMAKERS)}, timeout=30)
    r.raise_for_status()
    return r.json(), {k: r.headers.get(k) for k in ("x-requests-remaining", "x-requests-used", "x-requests-last")}


def _ts(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None


def match_game(ev: dict, games: list[dict]) -> dict | None:
    """The scheduled match for an Odds API event: kickoff within MATCH_WINDOW and both clubs' normalised names agree
    (either side alone is enough when only one club is in the window, e.g. an unusual spelling)."""
    start, h, a = _ts(ev["commence_time"]), norm(ev["home_team"]), norm(ev["away_team"])
    near = [g for g in games if abs(g["kickoff_utc"] - start) <= MATCH_WINDOW]
    both = [g for g in near if h in g["home_names"] and a in g["away_names"]]
    if len(both) == 1:
        return both[0]
    one = [g for g in near if h in g["home_names"] or a in g["away_names"]]
    return one[0] if len(one) == 1 else None


def parse(events: list[dict], games: list[dict], competition: str, now: datetime) -> tuple[list[dict], int]:
    rows, unmatched = [], 0
    for ev in events:
        g = match_game(ev, games)
        if g is None:
            unmatched += 1
            continue
        mins = int((g["kickoff_utc"] - now).total_seconds() // 60)
        if mins < 0:              # in play: not a pre-match price
            continue
        for bk in ev.get("bookmakers", []):
            mk = next((x for x in bk.get("markets", []) if x.get("key") == "h2h"), None)
            prices = {o["name"]: o.get("price") for o in (mk or {}).get("outcomes", [])}
            hd, ad, dd = prices.get(ev["home_team"]), prices.get(ev["away_team"]), prices.get("Draw")
            if not all(isinstance(x, (int, float)) and x > 1 for x in (hd, dd, ad)):
                continue
            inv = [1 / hd, 1 / dd, 1 / ad]
            t = sum(inv)
            rows.append({"game_id": g["game_id"], "provider": bk.get("title", bk["key"])[:64], "fetched_at": now,
                         "competition": competition, "home_dec": float(hd), "draw_dec": float(dd), "away_dec": float(ad),
                         "p_home": inv[0] / t, "p_draw": inv[1] / t, "p_away": inv[2] / t,
                         "book_updated_at": _ts(mk.get("last_update") or bk.get("last_update")), "minutes_to_kickoff": mins})
    return rows, unmatched


def _budget(engine: Engine, job_params_league: str) -> tuple[datetime | None, int | None]:
    """Last successful snapshot of this competition, and the most recent credits-remaining across both jobs."""
    with engine.connect() as c:
        last = c.execute(text("""SELECT max(started_at) FROM ingestion_runs WHERE job = 'soccer_odds_api'
                                 AND status = 'succeeded' AND params->>'league' = :l AND stats ? 'credits_remaining'"""),
                         {"l": job_params_league}).scalar()
        rem = c.execute(text("""SELECT stats->>'credits_remaining' FROM ingestion_runs WHERE job = ANY(:j)
                                AND status = 'succeeded' AND stats ? 'credits_remaining'
                                ORDER BY started_at DESC LIMIT 1"""), {"j": list(JOBS)}).scalar()
    return last, (int(rem) if rem is not None else None)


def sync_soccer_odds_api(engine: Engine, league: League, *, retries: int = 2, get=http_get, force: bool = False) -> dict:
    key = get_settings().odds_api_key
    if not key:
        return {"league": league.slug, "skipped": "ODDS_API_KEY not set"}
    sport = SPORTS.get(league.slug)
    if sport is None:
        return {"league": league.slug, "skipped": "competition not covered by The Odds API"}
    last, remaining = _budget(engine, league.slug)
    if not force and last is not None and datetime.now(timezone.utc) - last < MIN_GAP:
        return {"league": league.slug, "skipped": f"last snapshot {last:%H:%M} UTC (< {MIN_GAP.seconds // 3600} h ago)"}
    if not force and remaining is not None and remaining < RESERVE:
        return {"league": league.slug, "skipped": f"only {remaining} credits left this month"}

    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        now = datetime.now(timezone.utc)
        with engine.connect() as c:
            upcoming = c.execute(text("""SELECT count(*) FROM soccer_games WHERE competition = :c AND status = 'scheduled'
                                         AND kickoff_utc BETWEEN :a AND :b"""),
                                 {"c": league.competition, "a": now, "b": now + timedelta(days=8)}).scalar()
        if not upcoming:   # international break / off-season: don't spend a credit
            return {"league": league.slug, "events": 0, "snapshots": 0, "skipped": "no matches in the next 8 days"}
        events, quota = get(key, sport)
        with engine.connect() as c:
            games = [dict(r._mapping) for r in c.execute(text("""
                SELECT g.game_id, g.kickoff_utc, h.name hn, h.short_name hs, a.name an, a.short_name as_
                FROM soccer_games g JOIN soccer_teams h ON h.team_id = g.home_team_id
                JOIN soccer_teams a ON a.team_id = g.away_team_id
                WHERE g.competition = :c AND g.kickoff_utc BETWEEN :a AND :b"""),
                {"c": league.competition, "a": now - timedelta(hours=12), "b": now + timedelta(days=15)})]
        for g in games:
            g["home_names"] = {norm(x) for x in (g.pop("hn"), g.pop("hs")) if x}
            g["away_names"] = {norm(x) for x in (g.pop("an"), g.pop("as_")) if x}
        rows, unmatched = parse(events, games, league.competition, now)
        if unmatched:
            issues.add("odds_api_soccer", None, "unmatched_event", "info", "skipped", count=unmatched)
        df = pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()
        with engine.begin() as c:
            res = load.upsert(c, sm.SoccerOddsSnapshot.__table__, df,
                              provenance={"source_id": source_id, "ingestion_run_id": run_id, "source_updated_at": now})
        return {"league": league.slug, "events": len(events), "snapshots": len(rows), "unmatched_events": unmatched,
                "games": len({r["game_id"] for r in rows}),
                "pinnacle_games": len({r["game_id"] for r in rows if r["provider"] == "Pinnacle"}),
                "credits_remaining": quota.get("x-requests-remaining"), "credits_used": quota.get("x-requests-used"), **res}

    return tracked(engine, "odds_api", "soccer_odds_api", {"league": league.slug}, run, retries=retries)

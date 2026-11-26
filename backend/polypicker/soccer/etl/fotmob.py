"""FotMob -> soccer_fotmob_stats: per-match expected goals (xG, non-penalty xG, xG on target), big chances and
touches in the opponent's box, for the xG features (soccer/ml/features.py).

Unofficial, undocumented endpoints (research use only): ``/api/data/leagues?id=&season=`` lists a season's
fixtures, ``/api/data/matchDetails?matchId=`` carries the stats. One request per finished match, ``PAUSE``
seconds apart; matches already stored are never re-fetched. Fixtures are matched to our ESPN games by kickoff
date (+-1 day) and normalised club names.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import polars as pl
from sqlalchemy import Engine, text

from polypicker.etl import load
from polypicker.etl.issues import IssueLog
from polypicker.nfl.etl.runner import tracked
from polypicker.soccer import models as sm
from polypicker.soccer.etl.crosscheck import norm
from polypicker.soccer.leagues import League

BASE = "https://www.fotmob.com/api/data"
LEAGUE_IDS = {"epl": 47, "laliga": 87, "bundesliga": 54, "seriea": 55, "ligue1": 53, "ucl": 42, "uel": 73}
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/130 Safari/537.36", "Accept": "application/json"}
PAUSE = 1.0
DAILY_LEAGUES = ("epl",)   # leagues `soccer daily` keeps current (backfill others with `soccer fotmob --first-season`)
STATS = {"expected_goals": "xg", "expected_goals_non_penalty": "npxg", "expected_goals_on_target": "xgot",
         "big_chance": "big_chances", "touches_opp_box": "box_touches"}

log = logging.getLogger(__name__)


def _get(path: str, params: dict) -> dict:
    import requests
    for attempt in range(4):
        r = requests.get(f"{BASE}/{path}", params=params, headers=HEADERS, timeout=30)
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return {}


def season_label(league: League, season: int) -> str:
    return str(season) if league.calendar_year else f"{season}/{season + 1}"


def parse_stats(details: dict) -> dict | None:
    """-> {"home_xg": ..., "away_xg": ..., ...} from matchDetails (stats are [home, away]); None if no stats block."""
    periods = (((details.get("content") or {}).get("stats") or {}).get("Periods") or {}).get("All")
    if not periods:
        return None
    out: dict = {}
    for grp in periods.get("stats", []):
        for s in grp.get("stats", []):
            col = STATS.get(s.get("key"))
            v = s.get("stats") or []
            if col and len(v) == 2 and v[0] is not None and f"home_{col}" not in out:
                cast = float if col.endswith(("xg", "xgot")) else int
                try:
                    out[f"home_{col}"], out[f"away_{col}"] = cast(float(v[0])), cast(float(v[1]))
                except (TypeError, ValueError):
                    continue
    return out


def match_fixtures(fixtures: list[dict], games: list[dict]) -> list[tuple[dict, dict, bool]]:
    """(fotmob fixture, our game, swapped) for finished fixtures; swapped = FotMob lists the clubs the other way round."""
    out = []
    for f in fixtures:
        st = f.get("status") or {}
        if not st.get("finished") or st.get("cancelled"):
            continue
        ko = datetime.fromisoformat(st["utcTime"].replace("Z", "+00:00"))
        h, a = norm(f["home"]["name"]), norm(f["away"]["name"])
        for g in games:
            if abs(g["kickoff_utc"] - ko) > timedelta(days=1):
                continue
            if h in g["home_names"] and a in g["away_names"]:
                out.append((f, g, False)); break
            if a in g["home_names"] and h in g["away_names"]:
                out.append((f, g, True)); break
    return out


def sync_fotmob(engine: Engine, league: League, seasons: list[int], *, get=_get, pause: float = PAUSE,
                limit: int | None = None) -> dict:
    lid = LEAGUE_IDS.get(league.slug)
    if lid is None:
        return {"league": league.slug, "skipped": "no FotMob league id"}

    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        tot = {"fixtures": 0, "matched": 0, "fetched": 0, "with_xg": 0, "unmatched": 0}
        for season in seasons:
            with engine.connect() as c:
                games = [dict(r._mapping) for r in c.execute(text("""
                    SELECT g.game_id, g.kickoff_utc, h.name hn, h.short_name hs, a.name an, a.short_name as_
                    FROM soccer_games g JOIN soccer_teams h ON h.team_id = g.home_team_id
                    JOIN soccer_teams a ON a.team_id = g.away_team_id
                    WHERE g.competition = :c AND g.season = :s AND g.status = 'final'
                      AND NOT EXISTS (SELECT 1 FROM soccer_fotmob_stats f WHERE f.game_id = g.game_id)"""),
                    {"c": league.competition, "s": season})]
            if not games:
                continue
            for g in games:
                g["home_names"] = {norm(x) for x in (g.pop("hn"), g.pop("hs")) if x}
                g["away_names"] = {norm(x) for x in (g.pop("an"), g.pop("as_")) if x}
            fx = (get("leagues", {"id": lid, "season": season_label(league, season)}).get("fixtures") or {}).get("allMatches") or []
            pairs = match_fixtures(fx, games)
            finished = sum(1 for f in fx if (f.get("status") or {}).get("finished"))
            tot["fixtures"] += finished; tot["matched"] += len(pairs)
            if finished - len(pairs) > 0:
                issues.add("fotmob", f"{league.slug}:{season}", "unmatched_fixture", "info", "skipped",
                           count=finished - len(pairs))
                tot["unmatched"] += finished - len(pairs)
            rows = []
            for f, g, swapped in pairs:
                if limit is not None and tot["fetched"] >= limit:
                    break
                time.sleep(pause)
                st = parse_stats(get("matchDetails", {"matchId": f["id"]})) or {}
                tot["fetched"] += 1
                if swapped:
                    st = {(k.replace("home_", "away_") if k.startswith("home_") else k.replace("away_", "home_")): v for k, v in st.items()}
                tot["with_xg"] += int(st.get("home_xg") is not None)
                rows.append({"game_id": g["game_id"], "fotmob_match_id": int(f["id"]), "competition": league.competition,
                             **{f"{s}_{c}": st.get(f"{s}_{c}") for s in ("home", "away") for c in STATS.values()},
                             "fetched_at": datetime.now(timezone.utc)})
                if len(rows) >= 50:   # save as we go: a long backfill can be stopped and resumed
                    _save(engine, rows, source_id, run_id); rows = []
            _save(engine, rows, source_id, run_id)
            log.info("fotmob %s %s: %s", league.slug, season, tot)
        return {"league": league.slug, "seasons": seasons, **tot}

    return tracked(engine, "fotmob", "fotmob", {"league": league.slug, "seasons": seasons}, run, retries=1)


def _save(engine: Engine, rows: list[dict], source_id: int, run_id: int) -> None:
    if not rows:
        return
    schema = {f"{s}_{c}": (pl.Float64 if c.endswith(("xg", "xgot")) else pl.Int64) for s in ("home", "away") for c in STATS.values()}
    with engine.begin() as c:
        load.upsert(c, sm.SoccerFotmobStats.__table__, pl.DataFrame(rows, schema_overrides=schema, infer_schema_length=None),
                    provenance={"source_id": source_id, "ingestion_run_id": run_id,
                                "source_updated_at": datetime.now(timezone.utc)})

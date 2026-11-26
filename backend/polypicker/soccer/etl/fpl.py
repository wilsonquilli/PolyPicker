"""Fantasy Premier League -> soccer_fpl_players + soccer_fpl_player_games (EPL only).

Current season from the live FPL API: ``bootstrap-static`` (players, clubs, availability: status, chance of playing,
news), ``fixtures`` and ``event/{gw}/live`` (every player's stats for a gameweek, split per fixture: 1 call per
gameweek). History (2019-20 on) from the community archive github.com/vaastav/Fantasy-Premier-League
(``players_raw.csv``, ``fixtures.csv``, ``gws/merged_gw.csv``).

FPL club ids change every season; they're mapped to ESPN ids by fixtures (who was at home on which date), and FPL
fixtures to our games by those clubs and kickoff date.
"""

from __future__ import annotations

import io
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import polars as pl
from sqlalchemy import Engine, text

from polypicker.etl import load
from polypicker.etl.issues import IssueLog
from polypicker.nfl.etl.runner import tracked
from polypicker.soccer import models as sm

API = "https://fantasy.premierleague.com/api"
ARCHIVE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/{season}/{file}"
FIRST_ARCHIVE_SEASON = 2019
POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
COMPETITION = "eng.1"

log = logging.getLogger(__name__)


def _get_json(url: str) -> dict | list:
    import requests
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.json()


def _get_csv(url: str) -> list[dict] | None:
    import requests
    r = requests.get(url, timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    df = pl.read_csv(io.BytesIO(r.content), infer_schema_length=0, encoding="utf8-lossy", truncate_ragged_lines=True,
                     ignore_errors=True)
    return list(df.iter_rows(named=True))


def _ts(v) -> datetime | None:
    if not v:
        return None
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _i(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def map_fixtures(fixtures: list[dict], games: list[dict]) -> tuple[dict[int, int], dict[int, int]]:
    """-> (FPL club id -> ESPN team id, FPL fixture id -> ESPN game id)."""
    by_day: dict = defaultdict(list)
    for g in games:
        by_day[g["game_date"]].append(g)
    votes: dict[int, Counter] = defaultdict(Counter)
    for f in fixtures:
        ko = _ts(f.get("kickoff_time"))
        if ko is None:
            continue
        for day in (ko.date(), ko.date() - timedelta(days=1), ko.date() + timedelta(days=1)):
            for g in by_day.get(day, []):
                votes[_i(f["team_h"])][g["home_team_id"]] += 1
                votes[_i(f["team_a"])][g["away_team_id"]] += 1
    teams, used = {}, set()
    for fid, c in sorted(votes.items(), key=lambda kv: -kv[1].most_common(1)[0][1]):
        for tid, n in c.most_common():
            if tid not in used and n >= 3:
                teams[fid] = tid
                used.add(tid)
                break
    idx = {(g["home_team_id"], g["away_team_id"]): g for g in games}
    fx = {}
    for f in fixtures:
        h, a, ko = teams.get(_i(f["team_h"])), teams.get(_i(f["team_a"])), _ts(f.get("kickoff_time"))
        g = idx.get((h, a))
        if g is not None and ko is not None and abs((g["game_date"] - ko.date()).days) <= 3:
            fx[_i(f["id"])] = g["game_id"]
        elif g is not None and ko is None:   # postponed fixture without a new date yet
            continue
    return teams, fx


def _games(engine: Engine, season: int) -> list[dict]:
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(
            "SELECT game_id, game_date, home_team_id, away_team_id FROM soccer_games WHERE competition = :c AND season = :s"),
            {"c": COMPETITION, "s": season})]


def _line(season: int, element: int, fixture: int, st: dict, fx_game: dict, fx_teams: dict, teams: dict) -> dict:
    """One player-match row; ``st`` uses FPL stat names."""
    home = str(st.get("was_home")).lower() in ("true", "1")
    h, a = fx_teams.get(fixture, (None, None))
    return {"season": season, "element_id": element, "fixture_id": fixture, "game_id": fx_game.get(fixture),
            "team_id": teams.get(h if home else a), "was_home": home, "minutes": _i(st.get("minutes")) or 0,
            "starts": _i(st.get("starts")), "goals": _i(st.get("goals_scored")) or 0, "assists": _i(st.get("assists")) or 0,
            "clean_sheets": _i(st.get("clean_sheets")), "goals_conceded": _i(st.get("goals_conceded")),
            "saves": _i(st.get("saves")), "yellow": _i(st.get("yellow_cards")), "red": _i(st.get("red_cards")),
            "bonus": _i(st.get("bonus")), "bps": _i(st.get("bps")), "xg": _f(st.get("expected_goals")),
            "xa": _f(st.get("expected_assists")), "xgc": _f(st.get("expected_goals_conceded")),
            "total_points": _i(st.get("total_points"))}


def archive_season(engine: Engine, season: int, get_csv=_get_csv) -> tuple[list[dict], list[dict], dict]:
    label = f"{season}-{(season + 1) % 100:02d}"
    players = get_csv(ARCHIVE.format(season=label, file="players_raw.csv"))
    fixtures = get_csv(ARCHIVE.format(season=label, file="fixtures.csv"))
    gws = get_csv(ARCHIVE.format(season=label, file="gws/merged_gw.csv"))
    if not (players and fixtures and gws):
        return [], [], {"season": season, "skipped": "archive files missing"}
    teams, fx_game = map_fixtures(fixtures, _games(engine, season))
    fx_teams = {_i(f["id"]): (_i(f["team_h"]), _i(f["team_a"])) for f in fixtures}
    prow = [{"season": season, "element_id": _i(p["id"]), "code": _i(p.get("code")),
             "name": f"{p.get('first_name') or ''} {p.get('second_name') or ''}".strip()[:80] or p["web_name"],
             "web_name": (p.get("web_name") or "")[:40], "position": POS.get(_i(p.get("element_type")), "?"),
             "team_id": teams.get(_i(p.get("team"))), "status": None, "chance_next": None, "news": None} for p in players]
    grow = {}
    for r in gws:
        el, fid = _i(r.get("element")), _i(r.get("fixture"))
        if el is not None and fid is not None:
            grow[(el, fid)] = _line(season, el, fid, r, fx_game, fx_teams, teams)
    return prow, list(grow.values()), {"season": season, "clubs_mapped": len(teams), "fixtures_mapped": len(fx_game),
                                       "fixtures": len(fixtures)}


def current_season(engine: Engine, season: int, get_json=_get_json) -> tuple[list[dict], list[dict], dict]:
    boot = get_json(f"{API}/bootstrap-static/")
    fixtures = get_json(f"{API}/fixtures/")
    teams, fx_game = map_fixtures(fixtures, _games(engine, season))
    fx_teams = {f["id"]: (f["team_h"], f["team_a"]) for f in fixtures}
    prow = [{"season": season, "element_id": e["id"], "code": e.get("code"),
             "name": f"{e.get('first_name') or ''} {e.get('second_name') or ''}".strip()[:80] or e["web_name"],
             "web_name": e["web_name"][:40], "position": POS.get(e["element_type"], "?"), "team_id": teams.get(e["team"]),
             "status": e.get("status"), "chance_next": e.get("chance_of_playing_next_round"),
             "news": (e.get("news") or None) and e["news"][:300]} for e in boot["elements"]]
    side = {}   # (element, fixture) -> was_home
    for f in fixtures:
        side[f["id"]] = f
    team_of = {e["id"]: e["team"] for e in boot["elements"]}
    grow = []
    for ev in boot["events"]:
        if not (ev.get("finished") or ev.get("is_current")):
            continue
        live = get_json(f"{API}/event/{ev['id']}/live/")
        for el in live.get("elements", []):
            for ex in el.get("explain", []):
                fid = ex["fixture"]
                f = side.get(fid)
                if f is None or not f.get("finished_provisional", f.get("finished")):
                    continue
                st = dict(el["stats"]) if len(el.get("explain", [])) == 1 else \
                    {s["identifier"]: s["value"] for s in ex.get("stats", [])}   # double gameweek: per-fixture values
                st["was_home"] = team_of.get(el["id"]) == f["team_h"]
                grow.append(_line(season, el["id"], fid, st, fx_game, fx_teams, teams))
    return prow, grow, {"season": season, "clubs_mapped": len(teams), "fixtures_mapped": len(fx_game),
                        "fixtures": len(fixtures)}


def sync_fpl(engine: Engine, seasons: list[int], current: int) -> dict:
    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        out = []
        for season in seasons:
            prow, grow, st = current_season(engine, season) if season == current else archive_season(engine, season)
            if st.get("clubs_mapped", 20) < 20:
                issues.add("fpl", str(season), "unmapped_clubs", "warning", "kept", mapped=st.get("clubs_mapped"))
            prov = {"source_id": source_id, "ingestion_run_id": run_id, "source_updated_at": datetime.now(timezone.utc)}
            with engine.begin() as c:
                load.upsert(c, sm.SoccerFplPlayer.__table__, pl.DataFrame(prow, infer_schema_length=None) if prow else pl.DataFrame(), provenance=prov)
                for i in range(0, len(grow), 5000):
                    load.upsert(c, sm.SoccerFplPlayerGame.__table__, pl.DataFrame(grow[i:i + 5000], infer_schema_length=None), provenance=prov)
            out.append({**st, "players": len(prow), "player_games": len(grow),
                        "player_games_linked": sum(1 for g in grow if g["game_id"] is not None)})
            log.info("fpl %s", out[-1])
        return {"seasons": out}

    return tracked(engine, "fpl", "fpl", {"seasons": seasons}, run, retries=2)

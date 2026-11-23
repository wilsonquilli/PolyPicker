"""NBA.com live-feed adapter: parsing, stat conventions, validation, and the load-side guards.
Uses a box score shaped like cdn.nba.com's liveData/boxscore JSON; never calls the network."""

from datetime import date, datetime, timezone

import polars as pl
import pytest

from polypicker.etl.issues import IssueLog
from polypicker.etl.load import drop_result_downgrades
from polypicker.etl.sources.base import (GAME_COLUMNS, PLAYER_COLUMNS, TEAM_COLUMNS, TEAM_GAME_COLUMNS,
                                         CanonicalData)
from polypicker.etl.sources.nba_live import (finish_player_rows, game_status, not_playing_comment,
                                             parse_boxscore, parse_iso_minutes)
from polypicker.etl.validate import validate

HOME, AWAY = 1610612738, 1610612753  # BOS, ORL
GID = 22600275


def _player(pid, first, last, *, starter="0", played="1", status="ACTIVE", reason=None, pos="", mins="PT20M30.00S",
            pts=0, fgm=0, fga=0, fg3m=0, fg3a=0, ftm=0, fta=0, oreb=0, dreb=0, ast=0, tov=0, pm=0.0):
    p = {"status": status, "order": 1, "personId": pid, "jerseyNum": "7", "position": pos, "starter": starter,
         "oncourt": "0", "played": played, "name": f"{first} {last}", "nameI": f"{first[0]}. {last}",
         "firstName": first, "familyName": last,
         "statistics": {"minutes": mins if played == "1" else "", "points": pts, "assists": ast,
                        "reboundsTotal": oreb + dreb, "reboundsOffensive": oreb, "reboundsDefensive": dreb,
                        "fieldGoalsMade": fgm, "fieldGoalsAttempted": fga, "threePointersMade": fg3m,
                        "threePointersAttempted": fg3a, "freeThrowsMade": ftm, "freeThrowsAttempted": fta,
                        "steals": 0, "blocks": 0, "turnovers": tov, "foulsPersonal": 1, "plusMinusPoints": pm}}
    if reason:
        p["notPlayingReason"] = reason
    return p


def _team(tid, score, periods, players, *, oreb, dreb, team_reb=4):
    pts = [p["statistics"] for p in players]
    s = lambda k: sum(x[k] for x in pts)  # noqa: E731
    return {"teamId": tid, "teamTricode": "XXX", "score": score,
            "periods": [{"period": i + 1, "periodType": "REGULAR" if i < 4 else "OVERTIME", "score": v}
                        for i, v in enumerate(periods)],
            "players": players,
            "statistics": {"minutes": "PT265M00.00S", "points": score, "fieldGoalsMade": s("fieldGoalsMade"),
                           "fieldGoalsAttempted": s("fieldGoalsAttempted"), "threePointersMade": s("threePointersMade"),
                           "threePointersAttempted": s("threePointersAttempted"), "freeThrowsMade": s("freeThrowsMade"),
                           "freeThrowsAttempted": s("freeThrowsAttempted"), "reboundsOffensive": oreb,
                           "reboundsDefensive": dreb, "reboundsPersonal": oreb + dreb, "reboundsTeam": team_reb,
                           "reboundsTotal": oreb + dreb + team_reb, "assists": s("assists"), "steals": 0, "blocks": 0,
                           "turnovers": s("turnovers"), "turnoversTeam": 1, "turnoversTotal": s("turnovers") + 1,
                           "foulsPersonal": len(players)}}


def boxscore(status=3, text="Final"):
    # Home 110 (OT), away 104. Points identity holds per player: 2*FGM + 3PM + FTM.
    home_players = [
        _player(1627759, "Jaylen", "Brown", starter="1", pos="SF", mins="PT38M12.00S", pts=60, fgm=24, fga=40,
                fg3m=6, fg3a=12, ftm=6, fta=7, oreb=2, dreb=8, ast=5, tov=3, pm=6.0),
        _player(1630000, "Rookie", "Callup", mins="PT27M48.00S", pts=50, fgm=20, fga=38, fg3m=4, fg3a=10,
                ftm=6, fta=8, oreb=3, dreb=7, ast=4, tov=2, pm=2.0),
        _player(1631111, "Bench", "Guy", played="0", reason="DNP_COACH"),
        _player(1632222, "Hurt", "Guy", played="0", status="INACTIVE", reason="INACTIVE_INJURY"),
    ]
    away_players = [
        _player(203516, "James", "Ennis", starter="1", pos="PG", mins="PT40M00.00S", pts=104, fgm=40, fga=80,
                fg3m=10, fg3a=25, ftm=14, fta=16, oreb=5, dreb=20, ast=10, tov=8, pm=-6.0),
    ]
    return {"meta": {"version": 1, "code": 200},
            "game": {"gameId": f"{GID:010d}", "gameTimeUTC": "2026-11-21T00:30:00Z", "gameStatus": status,
                     "gameStatusText": text, "period": 5, "attendance": 19156,
                     "arena": {"arenaName": "TD Garden", "arenaCity": "Boston", "arenaState": "MA"},
                     "homeTeam": _team(HOME, 110, [25, 25, 25, 25, 10], home_players, oreb=5, dreb=15),
                     "awayTeam": _team(AWAY, 104, [26, 24, 25, 25, 4], away_players, oreb=5, dreb=20)}}


def db_game(**over):
    g = {c: None for c in GAME_COLUMNS}
    g.update(game_id=GID, season=2026, season_type="regular", is_cup=False,
             tipoff_utc=datetime(2026, 11, 21, 0, 30, tzinfo=timezone.utc), game_date_et=date(2026, 11, 20),
             home_team_id=HOME, away_team_id=AWAY, status="unknown", arena_name="TD Garden", has_player_stats=False)
    g.update(over)
    return g


def test_iso_minutes():
    assert parse_iso_minutes("PT25M01.00S") == pytest.approx(25 + 1 / 60)
    assert parse_iso_minutes("PT240M00.00S") == 240
    assert parse_iso_minutes("PT00M00.00S") == 0
    assert parse_iso_minutes("") is None and parse_iso_minutes(None) is None and parse_iso_minutes("junk") is None


@pytest.mark.parametrize("status,played,reason,expected", [
    ("ACTIVE", "1", None, None),
    ("ACTIVE", "0", "DNP_COACH", "DNP - Coach's Decision"),
    ("INACTIVE", "0", "INACTIVE_INJURY", "Inactive - Injury/Illness"),
    ("INACTIVE", "0", "INACTIVE_GLEAGUE_TWOWAY", "Inactive - G League - Two-Way"),
    ("INACTIVE", "0", "NWT_PERSONAL", "NWT - Personal"),
    ("INACTIVE", "0", None, "Inactive"),
    ("ACTIVE", "0", None, "DNP - Coach's Decision"),
    ("ACTIVE", "0", "DND_INJURY", "DND - Injury/Illness"),
    ("ACTIVE", "0", "NWT_INJURY", "NWT - Injury/Illness"),
    ("INACTIVE", "0", "INACTIVE_REST", "Inactive - Rest"),
])
def test_not_playing_comment_matches_base_dataset_style(status, played, reason, expected):
    assert not_playing_comment(status, played, reason) == expected


@pytest.mark.parametrize("st,text,expected", [(3, "Final", "final"), (3, "Final/OT", "final"), (2, "Q3 5:12", "live"),
                                              (1, "7:30 pm ET", "scheduled"), (1, "PPD", "postponed"),
                                              (1, "Cancelled", "canceled")])
def test_game_status(st, text, expected):
    assert game_status({"gameStatus": st, "gameStatusText": text}) == expected


def test_final_boxscore_parses_with_base_dataset_conventions():
    game, teams, players, names = parse_boxscore(boxscore(), db_game())
    assert game["status"] == "final" and (game["home_score"], game["away_score"]) == (110, 104)
    assert game["winner_team_id"] == HOME and game["periods"] == 5 and game["attendance"] == 19156
    assert game["season_type"] == "regular"  # schedule fields kept from the DB row
    home = next(t for t in teams if t["is_home"])
    assert home["reb"] == 20                # OREB + DREB, team rebounds excluded (base dataset convention)
    assert home["tov"] == 5                 # player turnovers, not turnoversTotal
    assert home["plus_minus"] == 6 and home["win"] is True
    assert (home["q1_points"], home["q4_points"], home["ot_points"]) == (25, 25, 10)
    assert home["minutes"] == 265

    pgs = finish_player_rows(players)
    by = {r["player_id"]: r for r in pgs.to_dicts()}
    assert by[1627759]["participation"] == "played" and by[1627759]["starter"] is True
    assert by[1627759]["starting_position"] == "F" and by[1627759]["minutes"] == pytest.approx(38.2)
    assert by[1631111]["participation"] == "dnp" and by[1631111]["points"] is None   # NULL, never zeros
    assert by[1632222]["participation"] == "inactive" and by[1632222]["status_comment"] == "Inactive - Injury/Illness"
    assert by[203516]["starting_position"] == "G"
    assert {n["player_id"] for n in names} == set(by)


def test_parsed_rows_pass_validation_without_errors():
    game, teams, players, names = parse_boxscore(boxscore(), db_game())
    game["has_player_stats"] = True
    team_rows = [{"team_id": t, "abbreviation": a, "city": "C", "name": "N", "full_name": "C N", "conference": "East",
                  "division": "Atlantic", "is_active": True} for t, a in ((HOME, "BOS"), (AWAY, "ORL"))]
    player_rows = [{**{c: None for c in PLAYER_COLUMNS}, **n} for n in names]
    data = CanonicalData(teams=pl.DataFrame(team_rows, schema=TEAM_COLUMNS),
                         players=pl.DataFrame(player_rows, schema={**PLAYER_COLUMNS}),
                         games=pl.DataFrame([game], schema=GAME_COLUMNS),
                         team_game_stats=pl.DataFrame(teams, schema=TEAM_GAME_COLUMNS),
                         player_game_stats=finish_player_rows(players))
    log = IssueLog()
    out = validate(data, log)
    assert [i for i in log.issues if i.severity == "error"] == []
    assert not [i for i in log.issues if i.rule in ("points_identity", "rebound_identity", "player_points_vs_team",
                                                    "points_vs_game_score", "quarter_sum")]
    assert out.games.height == 1 and out.team_game_stats.height == 2 and out.player_game_stats.height == 5


def test_live_game_updates_score_only():
    game, teams, players, _ = parse_boxscore(boxscore(status=2, text="Q3 5:12"), db_game())
    assert game["status"] == "live" and game["home_score"] == 110 and game["winner_team_id"] is None
    assert teams == [] and players == []


def test_team_mismatch_is_rejected():
    with pytest.raises(ValueError):
        parse_boxscore(boxscore(), db_game(home_team_id=AWAY, away_team_id=HOME))


def test_final_results_are_never_downgraded():
    incoming = pl.DataFrame([db_game(game_id=1, status="unknown"), db_game(game_id=2, status="final"),
                             db_game(game_id=3, status="scheduled")], schema=GAME_COLUMNS)
    bad = drop_result_downgrades(incoming, final_ids={1, 2})
    assert bad["game_id"].to_list() == [1]        # 2 is final->final (fine), 3 has no stored result


class _FakeEngine:
    """Just enough of an Engine for NbaLiveSource.extract with a stubbed pending list."""


def _source(pending, fetch):
    from polypicker.etl.sources import nba_live
    src = nba_live.NbaLiveSource(_FakeEngine(), fetch=fetch, pause_seconds=0)
    src.pending_games = lambda first_season: pending
    src._rows = lambda sql, **k: []
    return src


def _fail(gid):
    raise RuntimeError("HTTP 403")


def test_a_few_stragglers_only_warn():
    log = IssueLog()
    data = _source([db_game(game_id=1), db_game(game_id=2)], _fail).extract(2023, log)
    assert data.games.height == 0
    assert sum(i.rule == "live_fetch_failed" for i in log.issues) == 2


def test_feed_down_fails_the_run():
    from polypicker.etl.sources.nba_live import FeedUnavailable
    with pytest.raises(FeedUnavailable):
        _source([db_game(game_id=i) for i in range(1, 4)], _fail).extract(2023, IssueLog())


def test_real_recorded_boxscore():
    """A real cdn.nba.com box score (CHA @ ATL, 2026-11-20), recorded to catch feed-format changes."""
    import json
    from pathlib import Path
    payload = json.loads((Path(__file__).parent / "fixtures" / "boxscore_0022600043.json").read_text())
    game, teams, players, _ = parse_boxscore(payload, db_game(game_id=22600043, home_team_id=1610612737,
                                                             away_team_id=1610612766))
    assert game["status"] == "final" and game["home_score"] == 111 and game["periods"] == 4
    assert game["attendance"] == 15488 and game["arena_name"] == "State Farm Arena"
    pgs = finish_player_rows(players)
    counts = dict(pgs.group_by("participation").len().iter_rows())
    assert counts["played"] == 20 and counts["inactive"] >= 10
    home = next(t for t in teams if t["is_home"])
    assert home["reb"] == home["oreb"] + home["dreb"] == 37
    played_pts = pgs.filter((pl.col("team_id") == 1610612737) & (pl.col("participation") == "played"))["points"].sum()
    assert played_pts == home["points"] == 111

"""Player-based lineup strength, availability, and the ESPN injury-report parser. No network, no DB."""

from datetime import date, datetime, timedelta, timezone

import polars as pl

from polypicker.etl.issues import IssueLog
from polypicker.etl.sources.espn_injuries import norm_name, parse, status_weight
from polypicker.ml.lineup_features import build_lineup_features, game_score

A, B = 1, 2
STAR, ROLE, BENCH, OPP1, OPP2 = 10, 11, 12, 20, 21


def _games(n_final: int, extra_scheduled: int = 0) -> pl.DataFrame:
    rows = []
    for i in range(n_final + extra_scheduled):
        d = date(2025, 11, 1) + timedelta(days=2 * i)
        final = i < n_final
        rows.append({"game_id": 100 + i, "season": 2025, "tipoff_utc": datetime(d.year, d.month, d.day, 23, tzinfo=timezone.utc),
                     "game_date_et": d, "home_team_id": A, "away_team_id": B, "status": "final" if final else "scheduled",
                     "home_score": 110 if final else None, "away_score": 100 if final else None})
    return pl.DataFrame(rows)


def _row(gid, pid, team, *, played=True, mins=30.0, pts=20, pm=8):
    return {"game_id": gid, "player_id": pid, "team_id": team, "participation": "played" if played else "inactive",
            "minutes": mins if played else None, "points": pts if played else None, "fgm": 8, "fga": 15, "ftm": 3, "fta": 4,
            "oreb": 1, "dreb": 4, "assists": 4, "steals": 1, "blocks": 0, "turnovers": 2, "fouls": 2,
            "plus_minus": pm if played else None}


def _box(n_final: int, star_out_in: set[int] = frozenset()) -> pl.DataFrame:
    rows = []
    for i in range(n_final):
        gid = 100 + i
        rows.append(_row(gid, STAR, A, played=i not in star_out_in, mins=36, pts=30, pm=12))
        rows.append(_row(gid, ROLE, A, mins=30, pts=12, pm=2))
        rows.append(_row(gid, BENCH, A, mins=14, pts=4, pm=-4))
        rows.append(_row(gid, OPP1, B, mins=34, pts=18, pm=-6))
        rows.append(_row(gid, OPP2, B, mins=30, pts=10, pm=-4))
    return pl.DataFrame(rows, schema_overrides={"minutes": pl.Float64})


def _team_row(df: pl.DataFrame, gid: int, team: int) -> dict:
    return df.filter((pl.col("game_id") == gid) & (pl.col("team_id") == team)).row(0, named=True)


def test_game_score_formula():
    b = {"points": 20, "fgm": 8, "fga": 15, "ftm": 3, "fta": 4, "oreb": 1, "dreb": 4, "steals": 1, "assists": 4,
         "blocks": 0, "fouls": 2, "turnovers": 2}
    assert abs(game_score(b) - (20 + 3.2 - 10.5 - 0.4 + 0.7 + 1.2 + 1 + 2.8 - 0.8 - 2)) < 1e-9


def test_missing_star_lowers_lineup_strength():
    games = _games(8)
    lf = build_lineup_features(games, _box(8, star_out_in={7}))
    with_star, without = _team_row(lf, 106, A), _team_row(lf, 107, A)
    assert with_star["missing_min"] == 0 and abs(with_star["missing_pm"]) < 1e-9
    assert without["missing_min"] > 0.3          # the star's ~36 of ~80 usual minutes
    assert without["missing_pm"] > 0.5           # the replacement lineup rates worse
    assert without["lineup_pm"] < with_star["lineup_pm"]


def test_features_are_point_in_time():
    """Changing a game's own box score must not change that game's pregame features."""
    games = _games(6)
    box = _box(6)
    alt = box.with_columns(pl.when(pl.col("game_id") == 105).then(pl.lit(-40)).otherwise(pl.col("plus_minus"))
                           .alias("plus_minus"))
    a, b = build_lineup_features(games, box), build_lineup_features(games, alt)
    cols = ["lineup_pm", "lineup_gs", "full_pm", "missing_pm", "missing_min"]
    assert all(abs(_team_row(a, 105, A)[c] - _team_row(b, 105, A)[c]) < 1e-12 for c in cols)
    assert _team_row(a, 104, A)["lineup_pm"] == _team_row(b, 104, A)["lineup_pm"]


def test_upcoming_game_uses_injury_report():
    games = _games(6, extra_scheduled=1)
    box = _box(6)
    healthy = build_lineup_features(games, box)
    injured = build_lineup_features(games, box, lambda gid, team, pid: 0.0 if pid == STAR else None)
    h, i = _team_row(healthy, 106, A), _team_row(injured, 106, A)
    assert h["missing_min"] == 0 and i["missing_min"] > 0.3
    assert i["lineup_pm"] < h["lineup_pm"]
    assert [p["player_id"] for p in i["out_players"]] == [STAR]
    assert i["out_players"][0]["reason"] == "injury report"


def test_player_inactive_last_two_games_is_assumed_out_without_report():
    games = _games(6, extra_scheduled=1)
    lf = build_lineup_features(games, _box(6, star_out_in={4, 5}))
    r = _team_row(lf, 106, A)
    assert r["missing_min"] > 0.3 and r["out_players"][0]["reason"] == "inactive the last 2 games"


# ---------------------------------------------------------------- ESPN injury report

def _payload():
    def inj(aid, name, status, detail=("Knee", "Sprain")):
        return {"id": f"9{aid}", "status": status, "date": "2026-11-20T22:15Z", "shortComment": f"{name} update",
                "details": {"type": detail[0], "detail": detail[1], "returnDate": "2026-11-25"},
                "athlete": {"displayName": name, "links": [{"href": f"https://www.espn.com/nba/player/_/id/{aid}/x"}]}}
    return {"injuries": [
        {"displayName": "LA Clippers", "injuries": [inj(1, "Kawhi Leonard", "Out"), inj(2, "Nobody Known", "Day-To-Day")]},
        {"displayName": "Dallas Mavericks", "injuries": [inj(3, "Dereck Lively II", "Questionable")]},
        {"displayName": "Nowhere Ninjas", "injuries": [inj(4, "X", "Out")]},
    ]}


def test_parse_injury_report_matches_players_and_teams():
    teams = {"los angeles clippers": 1610612746, "dallas mavericks": 1610612742}
    roster = {1610612746: {"kawhi leonard": 202695}}
    league = {"dereck lively": [1641726]}
    issues = IssueLog()
    df = parse(_payload(), teams, roster, league, datetime(2026, 11, 21, 18, tzinfo=timezone.utc), issues)
    rows = {r["espn_athlete_id"]: r for r in df.iter_rows(named=True)}
    assert set(rows) == {"1", "2", "3"}                      # unknown team skipped
    assert rows["1"]["player_id"] == 202695 and rows["1"]["team_id"] == 1610612746
    assert rows["1"]["detail"] == "Knee Sprain" and rows["1"]["return_date"] == date(2026, 11, 25)
    assert rows["2"]["player_id"] is None                    # kept, but not matched
    assert rows["3"]["player_id"] == 1641726                 # league-wide unique match, suffix stripped
    assert rows["1"]["report_date"] == date(2026, 11, 21)    # ET day
    assert {i.rule for i in issues.issues} == {"unknown_team", "unmatched_player"}


def test_name_normalisation_and_status_weights():
    assert norm_name("Luka Dončić") == "luka doncic"
    assert norm_name("Jaren Jackson Jr.") == "jaren jackson"
    assert norm_name("De'Aaron Fox") == "deaaron fox"
    assert status_weight("Out") == 0.0 and status_weight("Day-To-Day") == 0.6 and status_weight("Weird") is None

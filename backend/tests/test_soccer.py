"""Soccer: ESPN parsing (real recorded scoreboard + summary), team-name matching across sources, the
Dixon-Coles scoreline grid, ranked probability score and grading. No network, no database."""

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from polypicker.soccer.etl.crosscheck import norm
from polypicker.soccer.etl.espn import map_status, parse_scoreboard_event, parse_summary
from polypicker.soccer.ml.grade import grade_row
from polypicker.soccer.ml.model import outcome_probs, rps, score_grid

FIX = Path(__file__).parent / "fixtures"


def test_scoreboard_event_parses():
    board = json.loads((FIX / "espn_epl_scoreboard_20261121.json").read_text())
    ev = next(e for e in board["events"] if e["shortName"] == "LEE @ CHE")
    g, teams = parse_scoreboard_event(ev, "eng.1")
    assert g["status"] == "final" and g["season"] == 2026 and g["competition"] == "eng.1"
    assert (g["home_score"], g["away_score"], g["result"]) == (1, 4, "A")
    assert {t["abbreviation"] for t in teams} == {"CHE", "LEE"} and str(g["game_date"]) == "2026-11-21"


def test_summary_parses_half_time_and_stats():
    f = next(FIX.glob("espn_epl_summary_*.json"))
    gid = int(f.stem.rsplit("_", 1)[1])
    ht, rows = parse_summary(json.loads(f.read_text()), gid)
    assert ht["home_ht_score"] == 1 and ht["away_ht_score"] == 1
    assert ht["home_score_90"] is not None  # 1st + 2nd half line scores
    home = next(r for r in rows if r["is_home"])
    assert home["shots"] >= home["shots_on_target"] >= 0
    assert 0 < home["possession"] < 100 and 0 <= home["pass_pct"] <= 1
    assert sum(r["possession"] for r in rows) == pytest.approx(100, abs=0.5)


@pytest.mark.parametrize("state,name,completed,expected", [
    ("post", "STATUS_FULL_TIME", True, "final"), ("in", "STATUS_SECOND_HALF", False, "live"),
    ("pre", "STATUS_SCHEDULED", False, "scheduled"), ("post", "STATUS_POSTPONED", False, "postponed"),
    ("post", "STATUS_ABANDONED", False, "canceled")])
def test_status_mapping(state, name, completed, expected):
    assert map_status({"type": {"state": state, "name": name, "completed": completed}}) == expected


@pytest.mark.parametrize("a,b", [("Manchester City FC", "Manchester City"), ("Brighton & Hove Albion FC", "Brighton and Hove Albion"),
                                 ("AFC Bournemouth", "Bournemouth"), ("Wolverhampton Wanderers FC", "Wolves"),
                                 ("Tottenham Hotspur FC", "Tottenham Hotspur"), ("Nottingham Forest FC", "Nott'm Forest")])
def test_team_names_match_across_sources(a, b):
    assert norm(a) == norm(b)


def test_score_grid_and_dixon_coles():
    g0, g1 = score_grid(1.5, 1.1, 0.0), score_grid(1.5, 1.1, -0.1)
    assert g0.sum() == pytest.approx(1) and g1.sum() == pytest.approx(1)
    o0, o1 = outcome_probs(g0), outcome_probs(g1)
    assert o0["home"] + o0["draw"] + o0["away"] == pytest.approx(1)
    assert o1["draw"] > o0["draw"]                    # negative rho lifts 0-0 / 1-1
    assert o0["home"] > o0["away"]                    # stronger home attack
    assert o0["over_0.5"] > o0["over_2.5"] > o0["over_4.5"]
    assert o0["top_scores"][0]["p"] >= o0["top_scores"][-1]["p"]


def test_rps():
    perfect = np.array([[1.0, 0, 0]]); y = np.array([[1.0, 0, 0]])
    assert rps(perfect, y) == 0
    assert rps(np.array([[0, 0, 1.0]]), y) == pytest.approx(1.0)      # confidently wrong in the far direction
    assert rps(np.array([[0, 1.0, 0]]), y) == pytest.approx(0.5)      # off by one category


def _r(**kw):
    base = dict(id=1, market="match_result", side="home", threshold_kind=None, threshold=None, probability=0.5,
                projection=None, status="final", home_score=2, away_score=2)
    base.update(kw)
    return SimpleNamespace(**base)


def test_grading():
    assert grade_row(_r(side="draw"))["outcome"] == "hit"
    assert grade_row(_r(side="home"))["outcome"] == "miss"
    assert grade_row(_r(market="total_goals", threshold_kind="over", threshold=3.5, side="over"))["outcome"] == "hit"
    assert grade_row(_r(market="btts", side="yes", home_score=1, away_score=0))["outcome"] == "miss"
    tg = grade_row(_r(market="team_goals", side="away", projection=1.3, probability=None))
    assert tg["abs_error"] == pytest.approx(0.7)
    assert grade_row(_r(status="scheduled"))["graded_state"] == "pending"
    assert grade_row(_r(status="canceled"))["graded_state"] == "void"


def test_spanish_names_match_across_sources():
    # openfootball legal names vs ESPN short names
    pairs = [("RC Celta de Vigo", "Celta Vigo"), ("Club Atlético de Madrid", "Atlético Madrid"), ("Deportivo Alavés", "Alavés"),
             ("Real Betis Balompié", "Real Betis"), ("RCD Espanyol de Barcelona", "Espanyol"), ("Getafe CF", "Getafe"),
             ("Real Racing Club de Santander", "Racing Santander"), ("RC Deportivo La Coruña", "Deportivo"),
             ("Real Sociedad de Fútbol", "Real Sociedad"), ("CA Osasuna", "Osasuna"), ("UD Las Palmas", "Las Palmas")]
    for a, b in pairs:
        assert norm(a) == norm(b), (a, b)
    assert norm("Real Madrid CF") != norm("Atlético Madrid")


def test_league_registry():
    from polypicker.soccer.leagues import BY_COMPETITION, LEAGUES, get_league
    assert get_league("laliga").competition == "esp.1" and get_league("laliga").football_data == "PD"
    assert BY_COMPETITION["eng.1"].slug == "epl"
    assert len({lg.target for lg in LEAGUES.values()}) == len(LEAGUES)  # one model per league
    with pytest.raises(ValueError):
        get_league("mls")


def test_cup_status_and_extra_time():
    from polypicker.soccer.etl.espn import decided_by
    assert decided_by({"type": {"name": "STATUS_FINAL_AET"}}) == "aet"
    assert decided_by({"type": {"name": "STATUS_FINAL_PEN"}}) == "pen"
    assert decided_by({"type": {"name": "STATUS_FULL_TIME"}}) is None


def test_summary_90_minute_score_from_line_scores():
    # Juventus 3-2 Galatasaray after extra time: 3-0 after 90 minutes.
    s = {"header": {"competitions": [{"competitors": [
        {"homeAway": "home", "score": "3", "linescores": [{"displayValue": "1"}, {"displayValue": "2"}, {"displayValue": "0"}, {"displayValue": "0"}]},
        {"homeAway": "away", "score": "2", "linescores": [{"displayValue": "0"}, {"displayValue": "0"}, {"displayValue": "1"}, {"displayValue": "1"}]}]}]}}
    ht, _ = parse_summary(s, 1)
    assert (ht["home_score_90"], ht["away_score_90"], ht["home_ht_score"]) == (3, 0, 1)


def test_90_minute_score_prefers_goal_timeline_over_corrupt_line_scores():
    # PSG 1-1 Arsenal (PSG on penalties); ESPN line scores were 0,2,-1,0 / 1,1,-1,0.
    def comp(side, tid, score, ls):
        return {"homeAway": side, "team": {"id": tid}, "score": score, "linescores": [{"displayValue": v} for v in ls]}
    goal = lambda tid, period: {"scoringPlay": True, "shootout": False, "team": {"id": tid}, "period": {"number": period}}  # noqa: E731
    s = {"header": {"competitions": [{"competitors": [comp("home", "160", "1", ["0", "2", "-1", "0"]),
                                                      comp("away", "359", "1", ["1", "1", "-1", "0"])]}]},
         "keyEvents": [goal("359", 1), goal("160", 2), {"scoringPlay": True, "shootout": True, "team": {"id": "160"}}]}
    from polypicker.soccer.etl.espn import score_after_90
    assert score_after_90(s) == {"home": 1, "away": 1}
    s["keyEvents"] = []   # no timeline: corrupt line scores are rejected
    assert score_after_90(s) == {}


def test_cup_calendar_ranges_expand_to_days():
    from polypicker.soccer.etl.espn import calendar_dates
    cal = [{"entries": [{"startDate": "2027-03-08T08:00Z", "endDate": "2027-03-10T07:59Z"}]}]
    assert [d.isoformat() for d in calendar_dates(cal)] == ["2027-03-08", "2027-03-09", "2027-03-10"]
    assert [d.isoformat() for d in calendar_dates(["2026-08-15T07:00Z"])] == ["2026-08-15"]


def test_group_names_normalised():
    from polypicker.soccer.etl.espn import parse_groups
    p = {"children": [{"name": "2025 CONMEBOL Libertadores - Group A", "standings": {"entries": [{"team": {"id": "8"}}]}},
                      {"name": "UEFA EUROPA LEAGUE - GROUP B", "standings": {"entries": [{"team": {"id": "9"}}, {"team": {"id": "10"}}]}},
                      {"name": "League Phase", "standings": {"entries": [{"team": {"id": "11"}}]}}]}
    assert parse_groups(p) == [("Group A", 8, 1), ("Group B", 9, 1), ("Group B", 10, 2), ("League Phase", 11, 1)]


def test_calendar_year_seasons():
    from datetime import date
    from polypicker.soccer.leagues import get_league
    assert get_league("libertadores").current_season(date(2026, 3, 1)) == 2026
    assert get_league("ucl").current_season(date(2027, 3, 1)) == 2026
    assert get_league("ecuador").season_label(2026) == "2026" and get_league("epl").season_label(2026) == "2026-27"


def test_italian_german_french_names_match_across_sources():
    pairs = [("ACF Fiorentina", "Fiorentina"), ("Bologna FC 1909", "Bologna"), ("SS Lazio", "Lazio"), ("Inter", "Internazionale"),
             ("US Salernitana 1919", "Salernitana"), ("AS Roma", "Roma"), ("1. FSV Mainz 05", "Mainz"), ("1. FC Köln", "Cologne"),
             ("FC Bayern München", "Bayern Munich"), ("Bor. Mönchengladbach", "Borussia Mönchengladbach"),
             ("Olympique Lyonnais", "Lyon"), ("Stade Brestois 29", "Brest"), ("Racing Club de Lens", "Lens"), ("LOSC Lille", "Lille")]
    for a, b in pairs:
        assert norm(a) == norm(b), (a, b)
    assert norm("Paris FC") != norm("Paris Saint-Germain")
    assert norm("AC Milan") != norm("Internazionale")


def test_calendar_year_season_comes_from_kickoff():
    ev = {"id": "1", "date": "2015-04-15T23:00Z", "season": {"year": 2014, "slug": "group-stage"}, "status": {"type": {"state": "post", "completed": True, "name": "STATUS_FULL_TIME"}},
          "competitions": [{"competitors": [{"homeAway": "home", "score": "1", "team": {"id": "10", "displayName": "A"}},
                                            {"homeAway": "away", "score": "0", "team": {"id": "11", "displayName": "B"}}]}]}
    g, _ = parse_scoreboard_event(ev, "conmebol.libertadores")
    assert g["season"] == 2015
    g, _ = parse_scoreboard_event(ev, "eng.1")
    assert g["season"] == 2014

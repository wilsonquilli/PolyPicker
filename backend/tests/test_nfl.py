"""NFL pipeline: ESPN parsing (real recorded scoreboard), grading (ties, voids), availability rules,
prop eligibility and threshold distributions. No network, no database."""

import json
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from polypicker.domain.markets import ThresholdKind
from polypicker.nfl.etl.espn import parse_event
from polypicker.nfl.markets import player_label
from polypicker.nfl.ml.features import _Rosters, eligible_stats, elo_win_prob
from polypicker.nfl.ml.grade import grade_row
from polypicker.nfl.ml.player_model import threshold_prob

FIX = Path(__file__).parent / "fixtures"


def test_espn_scoreboard_parses_final_scores_and_quarters():
    board = json.loads((FIX / "espn_nfl_scoreboard_20261115.json").read_text())
    ev = next(e for e in board["events"] if e["id"] == "401873064")   # PIT @ CIN, 2026-11-15
    db = {"game_id": 401873064, "home_team_id": 920, "away_team_id": 3900}
    g, teams = parse_event(ev, db)
    assert g["status"] == "final" and (g["home_score"], g["away_score"]) == (38, 18)
    assert g["winner_team_id"] == 920 and g["overtime"] is False
    home = next(t for t in teams if t["is_home"])
    assert sum(home[f"q{i}_points"] for i in range(1, 5)) + (home["ot_points"] or 0) == home["points"] == g["home_score"]
    assert all(len(e["competitions"][0]["competitors"]) == 2 for e in board["events"])


def test_espn_scheduled_event_touches_status_only():
    ev = {"id": "1", "status": {"type": {"name": "STATUS_SCHEDULED"}},
          "competitions": [{"competitors": [{"homeAway": "home", "score": "0"}, {"homeAway": "away", "score": "0"}]}]}
    g, teams = parse_event(ev, {"game_id": 1, "home_team_id": 1, "away_team_id": 2})
    assert g == {"game_id": 1, "status": "scheduled"} and teams == []


def _row(**kw):
    base = dict(id=1, market="moneyline", side="home", threshold_kind=None, threshold=None, probability=0.6, projection=None,
                subject_id="10", status="final", home_score=20, away_score=20, has_player_stats=True, participation=None,
                passing_yards=None, passing_tds=None, rushing_yards=None, receptions=None, receiving_yards=None, scrimmage_tds=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_tie_settles_moneyline_as_push():
    out = grade_row(_row())
    assert out["graded_state"] == "final" and out["outcome"] == "push" and out["brier"] is None


def test_moneyline_hit_and_miss():
    assert grade_row(_row(home_score=24, away_score=17))["outcome"] == "hit"
    assert grade_row(_row(home_score=17, away_score=24, side="home"))["outcome"] == "miss"


def test_player_props_grade_void_and_unavailable():
    prop = dict(market="player_receiving_yards", subject_id="00-1", threshold_kind="at_least", threshold=50, probability=0.4)
    assert grade_row(_row(**prop, participation="played", receiving_yards=61))["outcome"] == "hit"
    assert grade_row(_row(**prop, participation="inactive"))["graded_state"] == "void"
    assert grade_row(_row(**prop, participation=None))["graded_state"] == "void"            # no snaps, no stats
    assert grade_row(_row(**prop, participation=None, has_player_stats=False))["graded_state"] == "unavailable"
    td = dict(market="player_anytime_td", subject_id="00-1", threshold_kind="at_least", threshold=1, probability=0.3)
    assert grade_row(_row(**td, participation="played", scrimmage_tds=0))["outcome"] == "miss"


def test_roster_status_uses_latest_pregame_entry():
    rw = pl.DataFrame({"season": [2026] * 4, "week": [9, 10, 10, 11], "team_id": [1, 1, 2, 2], "player_id": ["p"] * 4,
                       "status": ["ACT", "TRD", "ACT", "RES"]})
    r = _Rosters(rw)
    assert r.status(2026, 9, "p") == (1, "ACT")
    assert r.status(2026, 10, "p") == (2, "ACT")          # traded: the active entry on the new team wins
    assert r.status(2026, 12, "p") == (2, "RES")          # no week-12 roster yet -> latest known (injured reserve)
    assert r.status(2026, 8, "p") is None


@pytest.mark.parametrize("pos,f,expected", [
    ("QB", dict(pass_attempts_l3=33, carries_l3=4, targets_l3=0), {"passing_yards", "passing_tds", "rushing_yards"}),
    ("RB", dict(pass_attempts_l3=0, carries_l3=15, targets_l3=3), {"rushing_yards", "receptions", "receiving_yards", "scrimmage_tds"}),
    ("WR", dict(pass_attempts_l3=0, carries_l3=0, targets_l3=1), set()),
])
def test_prop_eligibility(pos, f, expected):
    assert set(eligible_stats(f, pos)) == expected


def test_threshold_distributions_are_consistent():
    nb = {"type": "negative_binomial", "mean": 55.0, "r0": 0.03, "gamma": 1.05}
    norm = {"type": "normal", "mean": 240.0, "sd": 80.0}
    for dist, lo, hi in ((nb, 20, 100), (norm, 150, 350)):
        assert threshold_prob(dist, ThresholdKind.AT_LEAST, lo).p_hit > threshold_prob(dist, ThresholdKind.AT_LEAST, hi).p_hit
    # "k+" equals "over k - 0.5" on integer stats
    a = threshold_prob(norm, ThresholdKind.AT_LEAST, 250).p_hit
    b = threshold_prob(norm, ThresholdKind.OVER, 249.5).p_hit
    assert a == pytest.approx(b, abs=1e-9)


def test_labels_and_elo():
    assert player_label("scrimmage_tds", ThresholdKind.AT_LEAST, 1) == "Scores a rushing or receiving TD"
    assert player_label("receiving_yards", ThresholdKind.OVER, 59.5) == "over 59.5 receiving yards (i.e. 60+)"
    assert elo_win_prob(0) == 0.5 and elo_win_prob(100) > 0.6

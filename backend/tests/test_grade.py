from types import SimpleNamespace

import pytest

from polypicker.ml.grade import grade_row


def row(**kw):
    base = dict(id=1, market="moneyline", side="home", threshold_kind=None, threshold=None, probability=0.7,
                projection=None, subject_id=None, status="final", home_score=110, away_score=100,
                has_player_stats=True, participation=None, points=None, assists=None, rebounds=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_moneyline_hit_scores_brier():
    g = grade_row(row())
    assert g["graded_state"] == "final" and g["outcome"] == "hit"
    assert g["brier"] == pytest.approx(0.09)


def test_pending_and_canceled():
    assert grade_row(row(status="scheduled"))["graded_state"] == "pending"
    assert grade_row(row(status="unknown"))["graded_state"] == "pending"
    assert grade_row(row(status="canceled"))["graded_state"] == "void"


def test_integer_total_line_push_has_no_brier():
    g = grade_row(row(market="total", side="over", threshold_kind="over", threshold=210, probability=0.5,
                      projection=212))
    assert g["outcome"] == "push" and g["brier"] is None and g["abs_error"] == 2


@pytest.mark.parametrize("side,handicap,expected", [
    ("home", -9.5, "hit"),    # home won by 10
    ("home", -10, "push"),
    ("home", -10.5, "miss"),
    ("away", 9.5, "miss"),    # away lost by 10
    ("away", 10.5, "hit"),
])
def test_spread_settlement(side, handicap, expected):
    g = grade_row(row(market="spread", side=side, threshold_kind="handicap", threshold=handicap, probability=0.6))
    assert g["outcome"] == expected


def test_player_dnp_is_void_not_a_loss():
    for part in ("dnp", "inactive", None):
        g = grade_row(row(market="player_points", threshold_kind="at_least", threshold=20, probability=0.4,
                          projection=18, participation=part))
        assert g["graded_state"] == "void" and g["outcome"] is None and g["brier"] is None


def test_player_threshold_and_projection():
    hit = grade_row(row(market="player_points", threshold_kind="at_least", threshold=20, probability=0.4,
                        projection=18, participation="played", points=20))
    assert hit["outcome"] == "hit" and hit["abs_error"] == 2 and hit["brier"] == pytest.approx(0.36)
    ev = grade_row(row(market="player_assists", probability=None, projection=6.5, participation="played", assists=4))
    assert ev["abs_error"] == pytest.approx(2.5) and ev["outcome"] is None


def test_missing_box_score_is_unavailable():
    g = grade_row(row(market="player_points", threshold_kind="at_least", threshold=20, probability=0.4,
                      projection=18, has_player_stats=False))
    assert g["graded_state"] == "unavailable"


def test_excluded_player_forecast():
    base = dict(market="player_points", probability=None, projection=None)
    assert grade_row(row(**base, participation="inactive"))["graded_state"] == "void"
    assert grade_row(row(**base, participation="played", points=12))["graded_state"] == "unevaluable"

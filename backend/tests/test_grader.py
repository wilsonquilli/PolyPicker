"""Combo grader: leg parsing, odds maths, grading rules."""

import pytest

from polypicker.grader import LegError, _letter, american_from_p, american_to_decimal, parse_american, parse_legs, score


def leg(key, p, mkt=None, result=None):
    return {"key": key, "p_model": p, "p_market": mkt, "result": result}


def test_parse_legs():
    assert parse_legs("nba-1-home, soccer-2-draw,nfl-3-away") == [("nba", 1, "home"), ("soccer", 2, "draw"), ("nfl", 3, "away")]
    for bad in ("", "nba-1", "mlb-1-home", "nba-x-home", "nba-1-draw", "nba-1-home,nba-1-away", ",".join(f"nba-{i}-home" for i in range(13))):
        with pytest.raises(LegError):
            parse_legs(bad)


def test_odds_conversions():
    assert parse_american("+650") == 650 and parse_american("-120") == -120 and parse_american(" ") is None
    for bad in ("abc", "+50", "-99"):
        with pytest.raises(LegError):
            parse_american(bad)
    assert american_to_decimal(650) == pytest.approx(7.5)
    assert american_to_decimal(-200) == pytest.approx(1.5)
    assert american_from_p(0.5) == -100 and american_from_p(0.2) == 400 and american_from_p(0.75) == -300
    assert american_from_p(0) is None and american_from_p(1) is None


def test_letters():
    assert _letter(0.75, None) == "A+" and _letter(0.52, None) == "A-" and _letter(0.42, None) == "B"
    assert _letter(0.05, None) == "F"
    assert _letter(0.42, 0.10).startswith("A")    # clear value: one step up
    assert _letter(0.42, -0.30).startswith("C")   # clearly bad price: one step down
    assert _letter(0.75, 0.50) == "A+"            # capped
    assert _letter(0.12, -0.5) == "F"


def test_score_chance_value_and_weakest():
    s = score([leg("a", 0.8, 0.75), leg("b", 0.6, 0.6), leg("c", 0.5, 0.45)], odds=None)
    assert s["chance"] == pytest.approx(0.24)
    assert s["p_market"] == pytest.approx(0.2025)
    assert s["value_basis"] == "market" and s["edge"] == pytest.approx(0.24 / 0.2025 - 1, abs=1e-4)
    assert s["fair_odds"] == round(100 * 0.76 / 0.24)
    assert s["weakest"]["key"] == "c" and s["weakest"]["chance_without"] == pytest.approx(0.48)
    with_odds = score([leg("a", 0.8), leg("b", 0.6), leg("c", 0.5)], odds=200)
    assert with_odds["value_basis"] == "odds" and with_odds["ev"] == pytest.approx(0.24 * 3 - 1)
    assert with_odds["verdict"] == "bad" and with_odds["payout"] == 3.0
    assert score([leg("a", 0.8)], None)["weakest"] is None   # nothing to drop from a single leg


def test_score_with_results():
    s = score([leg("a", 0.8, result="won"), leg("b", 0.5), leg("c", 0.6, result="void")], None)
    assert s["chance"] == pytest.approx(0.5) and s["chance_pregame"] == pytest.approx(0.4)
    assert s["weakest"]["key"] == "b"        # only unsettled legs can be dropped
    dead = score([leg("a", 0.8, result="lost"), leg("b", 0.5)], None)
    assert dead["dead"] and dead["chance"] == 0 and dead["grade"] == "F"

"""Combos: selection rules, leg grading and the /combos endpoint's invariants."""

import math

import pytest

from polypicker.combos import SAFE_FLOOR, SAFE_MIN, SIZES, _leg_result, _two_way, combo_status


def _g(gid, p_home, p_mkt):
    team = lambda n: {"team_id": n, "abbr": f"T{n}", "name": f"Team {n}", "short": f"T{n}"}  # noqa: E731
    return {"game_id": gid, "tipoff_utc": "2026-11-24T00:00:00Z", "home": team(gid * 10), "away": team(gid * 10 + 1),
            "p_home": p_home, "p_home_mkt": p_mkt, "market_source": "Polymarket" if p_mkt is not None else None}


SLATE = [_g(1, 0.85, 0.80), _g(2, 0.80, 0.82), _g(3, 0.70, 0.66), _g(4, 0.66, 0.64), _g(5, 0.64, 0.66),
         _g(6, 0.55, 0.45), _g(7, 0.40, 0.33), _g(8, 0.50, 0.46), _g(9, 0.48, 0.47), _g(10, 0.45, 0.40), _g(11, 0.50, 0.60)]


def test_safe_ladder_takes_strongest_favorites():
    out = _two_way("nba", SLATE)
    for n in SIZES:
        legs = out[("safe", n)]["legs"]
        assert len(legs) == n
        assert [x["game_id"] for x in legs] == [1, 2, 3, 4, 5][:n]
        assert all(x["p_model"] >= SAFE_FLOOR for x in legs)
    assert all(x["p_model"] >= SAFE_MIN["nba"] for x in out[("safe", 3)]["legs"])
    assert out[("safe", 3)]["p_model"] == pytest.approx(0.85 * 0.80 * 0.70, abs=1e-4)


def test_risky_ladder_needs_an_edge_and_avoids_the_safer_games():
    out = _two_way("nba", SLATE)
    for n in SIZES:
        c = out[("risky", n)]
        assert c and len(c["legs"]) == n
        assert len({x["game_id"] for x in c["legs"]}) == n                     # one leg per game
        assert all(x["p_model"] >= x["p_market"] for x in c["legs"])           # never a leg the market rates higher
        safe = {x["game_id"]: x["pick"] for x in out[("safe", n)]["legs"]}
        assert all(safe.get(x["game_id"], x["pick"]) == x["pick"] for x in c["legs"])  # never the other side of a safer leg
        assert c["payout"] == pytest.approx(1 / math.prod(x["p_market"] for x in c["legs"]), abs=0.01)


def test_no_combo_without_enough_legs():
    out = _two_way("nba", [_g(1, 0.9, 0.95), _g(2, 0.8, 0.8)])
    assert all(c is None for c in out.values())


def test_leg_grading():
    fin = lambda h, a, **kw: {"status": "final", "home_score": h, "away_score": a, **kw}  # noqa: E731
    assert _leg_result("nba", "home", fin(110, 100)) == "won"
    assert _leg_result("nba", "away", fin(110, 100)) == "lost"
    assert _leg_result("nfl", "home", fin(20, 20)) == "lost"                    # a tie loses a winner pick
    assert _leg_result("nba", "home", {"status": "live", "home_score": 50, "away_score": 40}) is None
    assert _leg_result("soccer", "away", {"status": "postponed", "home_score": None, "away_score": None}) == "void"
    # soccer is graded on 90 minutes: 1-1 after 90, won in extra time -> a draw pick wins
    assert _leg_result("soccer", "draw", fin(2, 1, home_score_90=1, away_score_90=1)) == "won"
    assert _leg_result("soccer", "home", fin(2, 1, home_score_90=None, away_score_90=None)) == "won"


def test_combo_status():
    assert combo_status([{"result": "won"}, {"result": None}], True) == ("live", False)
    assert combo_status([{"result": "lost"}, {"result": None}], True) == ("lost", False)   # lost before it's settled
    assert combo_status([{"result": "won"}, {"result": "won"}], True) == ("won", True)
    assert combo_status([{"result": "won"}, {"result": "void"}], True) == ("won", True)
    assert combo_status([{}, {}], False) == ("open", False)


@pytest.fixture(scope="module")
def client():
    try:
        from sqlalchemy import text
        from polypicker.db.session import api_engine
        with api_engine().connect() as c:
            c.execute(text("SELECT 1 FROM combos LIMIT 1"))
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"database unavailable: {e}")
    from fastapi.testclient import TestClient
    from polypicker.api.main import app
    return TestClient(app)


@pytest.mark.db
def test_combos_endpoint(client):
    data = client.get("/api/v1/combos").json()["data"]
    for sport in ("nba", "nfl", "soccer"):
        slots = data[sport]["combos"]
        assert [(c["kind"], c["n_legs"]) for c in slots] == [(k, n) for k in ("safe", "risky") for n in SIZES]
        for c in slots + data[sport]["recent"]:
            if c["status"] == "none":
                continue
            assert len(c["legs"]) == c["n_legs"]
            assert len({x["game_id"] for x in c["legs"]}) == len(c["legs"])     # one leg per game
            assert c["p_model"] == pytest.approx(math.prod(x["p_model"] for x in c["legs"]), abs=1e-3)

"""Combos: selection rules and the /combos endpoint's invariants."""

import math

import pytest

from polypicker.api.routers.combos import SAFE_MIN, _two_way


def _g(gid, p_home, p_mkt):
    team = lambda n: {"team_id": n, "abbr": f"T{n}", "name": f"Team {n}", "short": f"T{n}"}  # noqa: E731
    return {"game_id": gid, "tipoff_utc": "2026-11-24T00:00:00Z", "home": team(gid * 10), "away": team(gid * 10 + 1),
            "p_home": p_home, "p_home_mkt": p_mkt, "market_source": "Polymarket" if p_mkt is not None else None}


def test_safe_takes_strongest_favorites_and_risky_needs_an_edge():
    games = [_g(1, 0.85, 0.80), _g(2, 0.80, 0.82), _g(3, 0.70, 0.66), _g(4, 0.55, 0.45), _g(5, 0.40, 0.33), _g(6, 0.50, 0.60)]
    out = _two_way("nba", games)
    safe = out["safe"]["legs"]
    assert [x["game_id"] for x in safe] == [1, 2, 3]
    assert all(x["p_model"] >= SAFE_MIN["nba"] for x in safe)
    assert out["safe"]["p_model"] == pytest.approx(0.85 * 0.80 * 0.70, abs=1e-4)
    risky = out["risky"]["legs"]
    assert {x["game_id"] for x in risky}.isdisjoint({1, 2, 3})
    assert all(x["p_model"] >= x["p_market"] for x in risky)          # never a leg the market rates higher
    assert out["risky"]["payout"] == pytest.approx(1 / math.prod(x["p_market"] for x in risky), abs=0.01)


def test_no_combo_without_two_legs():
    out = _two_way("nba", [_g(1, 0.9, 0.95)])
    assert out["safe"] is None and out["risky"] is None


@pytest.fixture(scope="module")
def client():
    try:
        from sqlalchemy import text
        from polypicker.db.session import api_engine
        with api_engine().connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"database unavailable: {e}")
    from fastapi.testclient import TestClient
    from polypicker.api.main import app
    return TestClient(app)


@pytest.mark.db
def test_combos_endpoint(client):
    data = client.get("/api/v1/combos").json()["data"]
    for sport in ("nba", "nfl", "soccer"):
        for kind in ("safe", "risky"):
            c = data[sport][kind]
            if not c:
                continue
            assert len(c["legs"]) >= 2
            assert len({x["game_id"] for x in c["legs"]}) == len(c["legs"])     # one leg per game
            assert c["p_model"] == pytest.approx(math.prod(x["p_model"] for x in c["legs"]), abs=1e-3)

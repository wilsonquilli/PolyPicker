"""Championship forecasts: rating fits recover known strengths, and the API serves well-formed simulations."""

import numpy as np
import pytest

from polypicker.futures import _clinched, fit_goal_ratings, fit_margin_ratings


def test_margin_ratings_recover_truth():
    truth = {1: 6.0, 2: 2.0, 3: -3.0, 4: -5.0}
    games = [(h, a, 2.5 + truth[h] - truth[a]) for h in truth for a in truth if h != a for _ in range(3)]
    hfa, r = fit_margin_ratings(games, list(truth), ridge=0.0)
    assert hfa == pytest.approx(2.5, abs=1e-6)
    assert r == pytest.approx(np.array(list(truth.values())), abs=1e-6)


def test_goal_ratings_order_teams():
    games = [(1, 2, 2.4, 0.8, False), (2, 1, 1.0, 2.0, False), (1, 3, 2.0, 1.0, False), (3, 1, 1.2, 1.8, False),
             (2, 3, 1.2, 1.3, False), (3, 2, 1.5, 1.0, False)]
    gr = fit_goal_ratings(games, [1, 2, 3])
    strength = gr.att + gr.dfn
    assert strength[0] > strength[2] > strength[1]
    assert gr.hfa > 0


def test_clinched():
    assert _clinched({1: 84, 2: 70, 3: 60}, {1: 4, 2: 4, 3: 5}) == 1      # 70 + 12 = 82 < 84
    assert _clinched({1: 84, 2: 70}, {1: 4, 2: 5}) is None                 # 70 + 15 = 85


@pytest.fixture(scope="module")
def client():
    try:
        from sqlalchemy import text
        from polypicker.db.session import api_engine
        with api_engine().connect() as c:
            c.execute(text("SELECT 1 FROM futures_forecasts LIMIT 1"))
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"database unavailable: {e}")
    from fastapi.testclient import TestClient
    from polypicker.api.main import app
    return TestClient(app)


@pytest.mark.db
def test_futures_endpoint(client):
    data = client.get("/api/v1/futures").json()["data"]
    if not data:
        pytest.skip("run `polypicker futures` first")
    for f in data:
        ps = [t["p_title"] for t in f["teams"]]
        assert ps == sorted(ps, reverse=True), f["competition"]
        assert sum(ps) == pytest.approx(1.0, abs=0.01), f["competition"]
        assert f["reasons"], f["competition"]
        if f["status"] in ("decided", "clinched"):
            assert f["teams"][0]["team_id"] == f["champion_id"]

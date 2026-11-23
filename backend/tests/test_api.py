"""API + database guarantees. Needs the dev database (docker compose up) with data ingested;
skipped automatically otherwise. Read-only: never modifies data."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def client():
    try:
        from polypicker.db.session import api_engine
        with api_engine().connect() as c:
            if not c.execute(text("SELECT count(*) FROM games")).scalar():
                pytest.skip("no data ingested")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"database unavailable: {e}")
    from fastapi.testclient import TestClient
    from polypicker.api.main import app
    return TestClient(app)


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_write_verbs_rejected(client, method):
    r = getattr(client, method)("/api/v1/games")
    assert r.status_code == 405
    assert r.json()["error"]["code"] == "read_only"


def test_api_db_role_cannot_write(client):
    from polypicker.db.session import api_engine
    with api_engine().connect() as c:
        with pytest.raises(DBAPIError):
            c.execute(text("UPDATE teams SET city = city WHERE false"))


def test_predictions_are_immutable(client):
    from polypicker.db.session import owner_engine
    with owner_engine().connect() as c:
        pid = c.execute(text("SELECT id FROM predictions LIMIT 1")).scalar()
        if pid is None:
            pytest.skip("no predictions yet")
        with pytest.raises(DBAPIError, match="immutable"):
            c.execute(text("UPDATE predictions SET probability = probability WHERE id = :i"), {"i": pid})
        c.rollback()
        with pytest.raises(DBAPIError, match="immutable"):
            c.execute(text("DELETE FROM predictions WHERE id = :i"), {"i": pid})
        c.rollback()


def test_games_listing_and_detail(client):
    r = client.get("/api/v1/games", params={"season": 2025, "status": "final", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["attribution"] and body["meta"]["total"] > 1000
    g = body["data"][0]
    assert {"home", "away", "status", "tipoff_utc", "game_date"} <= g.keys()
    d = client.get(f"/api/v1/games/{g['game_id']}").json()["data"]
    assert d["line_score"] and "head_to_head" in d["context"]


def test_win_probabilities_sum_to_100(client):
    r = client.get("/api/v1/predictions/history", params={"category": "winner", "limit": 1}).json()
    if not r["data"]:
        pytest.skip("no predictions yet")
    gid = r["data"][0]["game_id"]
    w = client.get(f"/api/v1/games/{gid}/predictions", params={"category": "winner"}).json()["data"]["markets"]["winner"]
    assert w["home_pct"] + w["away_pct"] == pytest.approx(100.0)
    assert w["home"]["probability"] + w["away"]["probability"] == pytest.approx(1.0)


def test_history_one_per_game_has_no_duplicates(client):
    """The accuracy page's "Recently graded" list: one row per game (or player), final revision only."""
    for cat in ("winner", "spread", "total", "points"):
        rows = client.get("/api/v1/predictions/history", params={
            "category": cat, "state": "final", "probabilities_only": True, "one_per_game": True, "limit": 200}).json()["data"]
        keys = [(r["kind"], r["game_id"], r["subject_id"]) for r in rows]
        assert len(keys) == len(set(keys)), cat
        if cat == "winner":
            assert all(r["probability"] >= 0.5 for r in rows)


def test_dnp_rows_have_null_stats(client):
    from polypicker.db.session import api_engine
    with api_engine().connect() as c:
        bad = c.execute(text("""SELECT count(*) FROM player_game_stats
                                WHERE participation <> 'played' AND points IS NOT NULL""")).scalar()
    assert bad == 0


def test_validation_errors_use_consistent_format(client):
    r = client.get("/api/v1/games", params={"date": "not-a-date"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    r = client.get("/api/v1/games/1")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"

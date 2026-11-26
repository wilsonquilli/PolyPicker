"""Job-failure alerts: errors are collected per job run, retries that recover are ignored, one alert per run."""

import logging
import os

import pytest
from sqlalchemy import delete

from polypicker import alerts


@pytest.fixture
def run(monkeypatch):
    try:
        from polypicker.db.session import owner_engine
        with owner_engine().connect():
            pass
    except Exception:  # noqa: BLE001
        pytest.skip("database not available")
    monkeypatch.setattr(alerts, "_installed", None)
    sent = []
    monkeypatch.setattr(alerts, "send", lambda title, text: sent.append((title, text)) or ["test"])
    tag = alerts.start_job("pytest")
    yield tag, sent
    logging.getLogger().removeHandler(alerts._installed)
    os.environ.pop(alerts.ENV, None)
    with owner_engine().begin() as c:
        c.execute(delete(alerts.JobAlert).where(alerts.JobAlert.job_run == tag))


def test_errors_are_collected_and_retries_ignored(run):
    tag, sent = run
    log = logging.getLogger("polypicker.cli")
    log.error("ESPN soccer sync (%s) failed, continuing: %s", "uel", "timeout")
    logging.getLogger("polypicker.nfl.etl.runner").error("%s attempt %s failed", "nfl_ingest", 1)   # retried, not final
    log.warning("just a warning")
    assert alerts.errors(tag) == ["ESPN soccer sync (uel) failed, continuing: timeout"]
    res = alerts.finish_job(tag, "pytest", ["soccer"])
    assert res["title"] == "PolyPicker pytest failed" and "Failed steps: soccer" in res["text"] and "uel" in res["text"]
    assert len(sent) == 1


def test_clean_run_sends_nothing(run):
    tag, sent = run
    assert alerts.finish_job(tag, "pytest", []) is None and sent == []


def test_webhook_shapes(monkeypatch):
    calls = []

    class R:
        def raise_for_status(self):
            pass

    import requests
    monkeypatch.setattr(requests, "post", lambda url, **kw: calls.append((url, kw)) or R())
    monkeypatch.setattr(alerts.platform, "system", lambda: "Linux")
    from polypicker.config import get_settings
    for url in ("https://ntfy.sh/pp-test", "https://discord.com/api/webhooks/1/x", "https://hooks.slack.com/services/x"):
        monkeypatch.setattr(get_settings(), "alert_webhook_url", url)
        assert alerts.send("T", "body") == ["webhook"]
    assert calls[0][1]["data"] == b"body" and calls[0][1]["headers"]["Title"] == "T"
    assert calls[1][1]["json"] == {"content": "**T**\nbody"}
    assert calls[2][1]["json"] == {"text": "*T*\nbody"}

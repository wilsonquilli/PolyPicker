"""Alerts when a scheduled job fails.

Every ERROR log line from a job (a step that raised, a feed that "failed, continuing", a league whose forecasts
failed) is stored in ``job_alerts``, tagged with the job run. When the job ends, one alert summarises them:

* a desktop notification on macOS (while jobs run by hand on the Mac);
* a POST to ``ALERT_WEBHOOK_URL`` if set: ntfy.sh topic URLs get plain text (free phone push via the ntfy app),
  Discord / Slack webhooks get their JSON shape, anything else gets ``{"title", "text"}``.

Retry attempts that later succeed log "attempt N failed" and are ignored: only failures the job gave up on count.
Child processes (run-all runs one per sport) inherit the run tag through the ``PP_JOB_RUN`` environment variable.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func, insert, select
from sqlalchemy.orm import Mapped, mapped_column

from polypicker.db.models import Base

ENV = "PP_JOB_RUN"
IGNORE = ("attempt %s failed", "%s attempt %s failed", "ingestion attempt %s failed")
log = logging.getLogger(__name__)


class JobAlert(Base):
    __tablename__ = "job_alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_run: Mapped[str] = mapped_column(String(64), index=True)
    job: Mapped[str] = mapped_column(String(40))
    logger: Mapped[str] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class _Collector(logging.Handler):
    """Writes ERROR records of the current job run to job_alerts (never raises: alerting mustn't break a job)."""

    def __init__(self, job: str):
        super().__init__(logging.ERROR)
        self.job = job

    def emit(self, record: logging.LogRecord) -> None:
        run = os.environ.get(ENV)
        if not run or record.msg in IGNORE or record.name == __name__:
            return
        try:
            from polypicker.db.session import owner_engine
            msg = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                msg += f" ({type(record.exc_info[1]).__name__}: {record.exc_info[1]})"
            with owner_engine().begin() as c:
                c.execute(insert(JobAlert).values(job_run=run, job=self.job, logger=record.name[:120], message=msg[:4000]))
        except Exception:  # noqa: BLE001
            pass


_installed: _Collector | None = None


def install(job: str) -> None:
    """Collect this process's ERROR logs for the current job run (idempotent per process)."""
    global _installed
    if _installed is None:
        _installed = _Collector(job)
        logging.getLogger().addHandler(_installed)


def start_job(job: str) -> str:
    """Tag a new job run (inherited by child processes) and start collecting its errors."""
    run = f"{job}-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    os.environ[ENV] = run
    install(job)
    return run


def errors(run: str) -> list[str]:
    from polypicker.db.session import owner_engine
    with owner_engine().connect() as c:
        return [r[0] for r in c.execute(select(JobAlert.message).where(JobAlert.job_run == run).order_by(JobAlert.id))]


def finish_job(run: str, job: str, failed_steps: list[str] | None = None) -> dict | None:
    """Send one alert if the run logged errors or had failed steps. Returns what was sent (None when all good)."""
    errs = errors(run)
    failed_steps = failed_steps or []
    if not errs and not failed_steps:
        return None
    title = f"PolyPicker {job} failed" if failed_steps else f"PolyPicker {job}: {len(errs)} error{'s' * (len(errs) != 1)}"
    lines = ([f"Failed steps: {', '.join(failed_steps)}"] if failed_steps else []) + [f"- {e[:300]}" for e in errs[:8]]
    if len(errs) > 8:
        lines.append(f"... and {len(errs) - 8} more (polypicker alerts)")
    text = "\n".join(lines)
    sent = send(title, text)
    return {"title": title, "text": text, "sent": sent}


def send(title: str, text: str) -> list[str]:
    """Deliver an alert on every configured channel; returns the channels that worked."""
    from polypicker.config import get_settings
    sent = []
    if platform.system() == "Darwin" and get_settings().alert_desktop:
        try:
            esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
            body = esc(text.splitlines()[0] if text else "")[:200]
            subprocess.run(["osascript", "-e", f'display notification "{body}" with title "{esc(title)}" sound name "Basso"'],
                           check=True, timeout=10, capture_output=True)
            sent.append("desktop")
        except Exception as e:  # noqa: BLE001
            log.warning("desktop notification failed: %s", e)
    url = get_settings().alert_webhook_url
    if url:
        try:
            import requests
            if "ntfy" in url:
                r = requests.post(url, data=text.encode(), headers={"Title": title, "Priority": "high", "Tags": "warning"}, timeout=15)
            elif "discord.com/api/webhooks" in url:
                r = requests.post(url, json={"content": f"**{title}**\n{text}"[:1900]}, timeout=15)
            elif "hooks.slack.com" in url:
                r = requests.post(url, json={"text": f"*{title}*\n{text}"}, timeout=15)
            else:
                r = requests.post(url, json={"title": title, "text": text}, timeout=15)
            r.raise_for_status()
            sent.append("webhook")
        except Exception as e:  # noqa: BLE001
            log.warning("alert webhook failed: %s", e)
    return sent

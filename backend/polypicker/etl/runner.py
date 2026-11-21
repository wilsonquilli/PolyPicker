"""Ingestion job: extract -> validate -> load, with run tracking and retries (FR-10)."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import Engine, insert, select, update

from polypicker.db import models as m
from polypicker.etl import load
from polypicker.etl.issues import IssueLog
from polypicker.etl.sources.base import SourceAdapter
from polypicker.etl.validate import SchemaError, validate

log = logging.getLogger(__name__)


def _source_id(engine: Engine, code: str) -> int:
    with engine.connect() as conn:
        sid = conn.execute(select(m.DataSource.id).where(m.DataSource.code == code)).scalar()
    if sid is None:
        raise RuntimeError(f"data source {code!r} is not registered; run `polypicker db init`")
    return sid


def ingest_once(engine: Engine, adapter: SourceAdapter, first_season: int, run_id: int, source_id: int) -> dict:
    issues = IssueLog()
    t0 = time.monotonic()
    raw = adapter.extract(first_season, issues)
    data = validate(raw, issues)
    src_updated = adapter.source_updated_at()
    prov = {"source_id": source_id, "ingestion_run_id": run_id, "source_updated_at": src_updated}
    stats: dict = {"extract_validate_seconds": round(time.monotonic() - t0, 1)}
    # One transaction per attempt: a failure rolls back everything, so retries never duplicate.
    with engine.begin() as conn:
        stats["teams"] = load.upsert(conn, m.Team.__table__, data.teams, provenance=prov)
        stats["players"] = load.upsert(conn, m.Player.__table__, data.players, provenance=prov)
        stats["seasons"] = load.upsert_seasons(conn, data.games)
        stats["games"] = load.upsert(conn, m.Game.__table__, data.games, provenance=prov)
        stats["team_game_stats"] = load.upsert(conn, m.TeamGameStats.__table__, data.team_game_stats, provenance=prov)
        stats["player_game_stats"] = load.upsert(conn, m.PlayerGameStats.__table__, data.player_game_stats,
                                                 provenance=prov)
        load.refresh_player_team_history(conn)
        stats["id_mappings_added"] = load.upsert_id_mappings(conn, source_id, data)
        stats["marked_unknown"] = load.mark_stale_scheduled(conn, datetime.now(timezone.utc))
        if issues.issues:
            conn.execute(insert(m.DataQualityIssue), [
                {"ingestion_run_id": run_id, "entity": i.entity, "entity_key": i.entity_key, "rule": i.rule,
                 "severity": i.severity, "action": i.action, "detail": i.detail} for i in issues.issues])
    stats["issues"] = issues.summary()
    stats["source_updated_at"] = src_updated.isoformat() if src_updated else None
    return stats


def run_ingestion(engine: Engine, adapter: SourceAdapter, first_season: int, *, retries: int = 3,
                  backoff_seconds: float = 10.0) -> dict:
    source_id = _source_id(engine, adapter.code)
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        with engine.begin() as conn:
            run_id = conn.execute(insert(m.IngestionRun).values(
                source_id=source_id, job="ingest", status="running", attempt=attempt,
                params={"first_season": first_season, "adapter": adapter.code}).returning(m.IngestionRun.id)).scalar_one()
        try:
            stats = ingest_once(engine, adapter, first_season, run_id, source_id)
        except SchemaError:
            _finish(engine, run_id, "failed", error="schema error (not retried)")
            raise
        except Exception as e:  # transient or data error -> retry with backoff
            last_error = e
            log.exception("ingestion attempt %s failed", attempt)
            _finish(engine, run_id, "failed", error=f"{type(e).__name__}: {e}"[:4000])
            if attempt < retries:
                time.sleep(backoff_seconds * attempt)
            continue
        _finish(engine, run_id, "succeeded", stats=stats,
                source_updated_at=adapter.source_updated_at())
        return {"run_id": run_id, "attempt": attempt, **stats}
    raise RuntimeError(f"ingestion failed after {retries} attempts") from last_error


def _finish(engine: Engine, run_id: int, status: str, *, error: str | None = None, stats: dict | None = None,
            source_updated_at: datetime | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(update(m.IngestionRun).where(m.IngestionRun.id == run_id).values(
            status=status, error=error, stats=stats, source_updated_at=source_updated_at,
            finished_at=datetime.now(timezone.utc)))

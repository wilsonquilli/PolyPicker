"""NFL ingestion jobs with the same run tracking, retries and idempotent upserts as the NBA side."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable

import polars as pl
from sqlalchemy import Engine, insert, text

from polypicker.db import models as m
from polypicker.etl import load
from polypicker.etl.issues import IssueLog
from polypicker.etl.runner import _finish, _source_id
from polypicker.nfl import models as nm

log = logging.getLogger(__name__)


def tracked(engine: Engine, source_code: str, job: str, params: dict, fn: Callable[[int, int, IssueLog], dict], *,
            retries: int = 3, backoff_seconds: float = 10.0) -> dict:
    """Run ``fn(run_id, source_id, issues)`` with an ingestion_runs record per attempt; retry with backoff."""
    source_id = _source_id(engine, source_code)
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        with engine.begin() as conn:
            run_id = conn.execute(insert(m.IngestionRun).values(
                source_id=source_id, job=job, status="running", attempt=attempt, params=params)
                .returning(m.IngestionRun.id)).scalar_one()
        issues = IssueLog()
        try:
            stats = fn(run_id, source_id, issues)
        except Exception as e:  # noqa: BLE001
            last = e
            log.exception("%s attempt %s failed", job, attempt)
            _finish(engine, run_id, "failed", error=f"{type(e).__name__}: {e}"[:4000])
            if attempt < retries:
                time.sleep(backoff_seconds * attempt)
            continue
        if issues.issues:
            with engine.begin() as conn:
                conn.execute(insert(m.DataQualityIssue), [
                    {"ingestion_run_id": run_id, "entity": i.entity, "entity_key": i.entity_key, "rule": i.rule,
                     "severity": i.severity, "action": i.action, "detail": i.detail} for i in issues.issues])
        stats["issues"] = issues.summary()
        _finish(engine, run_id, "succeeded", stats=stats, source_updated_at=stats.pop("_source_updated_at", None))
        return {"run_id": run_id, "attempt": attempt, **stats}
    raise RuntimeError(f"{job} failed after {retries} attempts") from last


def mark_stale_scheduled(conn, now: datetime) -> int:
    """Kickoff passed without a result -> 'unknown' (never guessed as postponed)."""
    return conn.execute(text("""
        UPDATE nfl_games SET status = 'unknown', updated_at = now()
        WHERE status = 'scheduled' AND kickoff_utc < :now - interval '5 hours'"""), {"now": now}).rowcount


def ingest_nflverse(engine: Engine, source, *, retries: int = 3) -> dict:
    from polypicker.nfl.etl.nflverse import NflverseSource
    assert isinstance(source, NflverseSource) or hasattr(source, "extract")

    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        t0 = time.monotonic()
        data = source.extract(issues)
        prov = {"source_id": source_id, "ingestion_run_id": run_id, "source_updated_at": source.source_updated_at()}
        st: dict = {"extract_seconds": round(time.monotonic() - t0, 1), "seasons": source.seasons}
        with engine.begin() as conn:
            st["teams"] = load.upsert(conn, nm.NflTeam.__table__, data.teams, provenance=prov)
            st["players"] = load.upsert(conn, nm.NflPlayer.__table__, data.players, provenance=prov)
            games, st["final_results_protected"] = load.protect_final_results(conn, data.games, table="nfl_games")
            st["games"] = load.upsert(conn, nm.NflGame.__table__, games, provenance=prov)
            known = set(games["game_id"].to_list()) | _final_ids(conn, data.games)
            tgs = data.team_game_stats.filter(pl.col("game_id").is_in(list(known)))
            st["team_game_stats"] = load.upsert(conn, nm.NflTeamGameStats.__table__, tgs, provenance=prov)
            pgs = data.player_game_stats.filter(pl.col("game_id").is_in(list(known)))
            st["player_game_stats"] = load.upsert(conn, nm.NflPlayerGameStats.__table__, pgs, provenance=prov)
            st["injuries"] = load.upsert(conn, nm.NflInjury.__table__, data.injuries, provenance=prov)
            st["roster_weeks"] = load.upsert(conn, nm.NflRosterWeek.__table__, data.roster_weeks, provenance=prov)
            st["marked_unknown"] = mark_stale_scheduled(conn, datetime.now(timezone.utc))
        st["_source_updated_at"] = source.source_updated_at()
        return st

    return tracked(engine, source.code, "nfl_ingest", {"seasons": source.seasons}, run, retries=retries)


def _final_ids(conn, games: pl.DataFrame) -> set[int]:
    if games.is_empty():
        return set()
    return {r[0] for r in conn.execute(text("SELECT game_id FROM nfl_games WHERE game_id = ANY(:ids)"),
                                       {"ids": games["game_id"].to_list()})}

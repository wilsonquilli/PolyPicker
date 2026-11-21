"""Operator CLI. All writes happen here, never through the public API (FR-11.4)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import typer

from polypicker.config import get_settings
from polypicker.db.session import owner_engine

app = typer.Typer(help="PolyPicker backend operations", no_args_is_help=True)
db_app = typer.Typer(help="Database schema management", no_args_is_help=True)
app.add_typer(db_app, name="db")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _audit(action: str, detail: dict) -> None:
    from sqlalchemy import insert
    from polypicker.db import models as m
    with owner_engine().begin() as conn:
        conn.execute(insert(m.AdminAuditEvent).values(actor="cli", action=action, detail=detail))


def _dump(obj) -> None:
    typer.echo(json.dumps(obj, indent=2, default=str))


@db_app.command("init")
def db_init() -> None:
    """Create tables, triggers and seed data (safe to re-run)."""
    from polypicker.db.schema import create_all
    create_all(owner_engine())
    typer.echo("schema ready")


@db_app.command("reset")
def db_reset(yes: bool = typer.Option(False, "--yes", help="Confirm dropping ALL data")) -> None:
    """Drop and recreate every table (development only)."""
    if not yes:
        raise typer.BadParameter("refusing to drop data without --yes")
    from polypicker.db.schema import create_all, drop_all
    drop_all(owner_engine())
    create_all(owner_engine())
    typer.echo("schema reset")


@app.command()
def ingest(retries: int = 3, first_season: int | None = None) -> None:
    """Import / re-sync games, box scores, teams and players from the source files (FR-10.1)."""
    from polypicker.etl.runner import run_ingestion
    from polypicker.etl.sources.kaggle_csv import KaggleCsvSource
    s = get_settings()
    result = run_ingestion(owner_engine(), KaggleCsvSource(s.resolved_data_dir()),
                           first_season or s.first_season, retries=retries)
    _dump(result)


@app.command()
def train(validation_season: int = typer.Option(2025, help="Season (start year) held out for validation"),
          activate: bool = typer.Option(True, help="Make the new models the ones used for live forecasts")) -> None:
    """Train game + player models chronologically and register them (spec §9.3-9.4)."""
    from polypicker.ml.pipeline import build_features, train_models
    eng = owner_engine()
    ids = train_models(eng, build_features(eng), validation_season, activate=activate)
    _audit("train", {"validation_season": validation_season, "model_ids": ids, "activated": activate})
    _dump(ids)


@app.command()
def predict(days: int = typer.Option(2, help="Forecast scheduled games tipping off within N days")) -> None:
    """Publish pregame forecasts for upcoming games (FR-02, FR-03, FR-06.2)."""
    from polypicker.ml.pipeline import predict_upcoming
    _dump(predict_upcoming(owner_engine(), days=days))


@app.command()
def grade() -> None:
    """Grade predictions whose games have finished (FR-09)."""
    from polypicker.ml.grade import grade_all
    _dump(grade_all(owner_engine()))


@app.command()
def backtest(season: int = typer.Option(..., help="Season start year to backtest, e.g. 2026")) -> None:
    """Point-in-time forecasts for a season's completed games using only earlier seasons (kind='backtest')."""
    from polypicker.ml.grade import grade_all
    from polypicker.ml.pipeline import backtest_season
    result = backtest_season(owner_engine(), season)
    result["grading"] = grade_all(owner_engine())
    _audit("backtest", {"season": season, "models": result["models"]})
    _dump(result)


@app.command("daily")
def daily(days: int = 2, retries: int = 3) -> None:
    """The scheduled job (FR-06.2/6.3): sync data, grade finished games, publish new forecasts."""
    ingest(retries=retries, first_season=None)
    grade()
    predict(days=days)


if __name__ == "__main__":
    app()

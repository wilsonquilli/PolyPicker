"""PostgreSQL schema (spec §7.1).

Every ingested row carries provenance (source_id, ingestion_run_id, source_updated_at,
updated_at) for FR-10.4 / NFR-08.3. Predictions are append-only; a trigger installed in
``schema.py`` rejects UPDATE/DELETE on them (FR-02.7, FR-09.6).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer,
                        SmallInteger, String, Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ProvenanceMixin:
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"))
    ingestion_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id"))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 nullable=False)


# ---------------------------------------------------------------- ingestion bookkeeping (FR-10)

class DataSource(Base):
    __tablename__ = "data_sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(Text)
    license_note: Mapped[str | None] = mapped_column(Text)
    approved_for: Mapped[str] = mapped_column(String(32), default="research")  # research | commercial
    created_at: Mapped[datetime] = _now()


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"))
    job: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # running | succeeded | failed
    attempt: Mapped[int] = mapped_column(SmallInteger, default=1)
    params: Mapped[dict | None] = mapped_column(JSONB)
    stats: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DataQualityIssue(Base):
    __tablename__ = "data_quality_issues"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ingestion_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id"))
    entity: Mapped[str] = mapped_column(String(64))
    entity_key: Mapped[str | None] = mapped_column(String(128))
    rule: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16))  # error | warning | info
    action: Mapped[str] = mapped_column(String(16))    # dropped | fixed | flagged
    detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()
    __table_args__ = (Index("ix_dqi_run_rule", "ingestion_run_id", "rule"),)


class SourceIdMapping(Base):
    """Maps provider-specific IDs to canonical IDs (FR-10.3)."""
    __tablename__ = "source_id_mapping"
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), primary_key=True)  # team | player | game
    source_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = _now()


# ---------------------------------------------------------------- core basketball entities

class Season(Base):
    __tablename__ = "seasons"
    season: Mapped[int] = mapped_column(SmallInteger, primary_key=True)  # start year
    label: Mapped[str] = mapped_column(String(16))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)


class Team(ProvenanceMixin, Base):
    __tablename__ = "teams"
    team_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    abbreviation: Mapped[str] = mapped_column(String(8))
    city: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(128))
    conference: Mapped[str | None] = mapped_column(String(8))
    division: Mapped[str | None] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Player(ProvenanceMixin, Base):
    __tablename__ = "players"
    player_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    first_name: Mapped[str | None] = mapped_column(String(64))
    last_name: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(128))
    birth_date: Mapped[date | None] = mapped_column(Date)
    height_inches: Mapped[int | None] = mapped_column(SmallInteger)
    weight_lbs: Mapped[int | None] = mapped_column(SmallInteger)
    position: Mapped[str | None] = mapped_column(String(8))  # e.g. G, F, C, G-F
    jersey: Mapped[str | None] = mapped_column(String(8))
    country: Mapped[str | None] = mapped_column(String(64))
    school: Mapped[str | None] = mapped_column(String(128))
    draft_year: Mapped[int | None] = mapped_column(SmallInteger)
    draft_round: Mapped[int | None] = mapped_column(SmallInteger)
    draft_number: Mapped[int | None] = mapped_column(SmallInteger)
    __table_args__ = (Index("ix_players_full_name_lower", func.lower(full_name)),)


class PlayerTeamHistory(Base):
    """Which team a player appeared for, per season (derived from box scores)."""
    __tablename__ = "player_team_history"
    player_id: Mapped[int] = mapped_column(ForeignKey("players.player_id"), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"), primary_key=True)
    season: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    first_game_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_game_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    games: Mapped[int] = mapped_column(SmallInteger)


class Game(ProvenanceMixin, Base):
    __tablename__ = "games"
    game_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    season: Mapped[int] = mapped_column(ForeignKey("seasons.season"))
    season_type: Mapped[str] = mapped_column(String(16))
    is_cup: Mapped[bool] = mapped_column(Boolean, default=False)
    game_label: Mapped[str | None] = mapped_column(String(64))
    game_sub_label: Mapped[str | None] = mapped_column(String(64))
    series_game_number: Mapped[int | None] = mapped_column(SmallInteger)
    tipoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    game_date_et: Mapped[date] = mapped_column(Date)  # local US-Eastern date, used for "browse by day"
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"))
    status: Mapped[str] = mapped_column(String(16))
    home_score: Mapped[int | None] = mapped_column(SmallInteger)
    away_score: Mapped[int | None] = mapped_column(SmallInteger)
    winner_team_id: Mapped[int | None] = mapped_column(BigInteger)
    periods: Mapped[int | None] = mapped_column(SmallInteger)
    arena_name: Mapped[str | None] = mapped_column(String(128))
    arena_city: Mapped[str | None] = mapped_column(String(64))
    arena_state: Mapped[str | None] = mapped_column(String(8))
    attendance: Mapped[int | None] = mapped_column(Integer)
    has_player_stats: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        Index("ix_games_date", "game_date_et"),
        Index("ix_games_home", "home_team_id", "tipoff_utc"),
        Index("ix_games_away", "away_team_id", "tipoff_utc"),
        Index("ix_games_season", "season", "season_type"),
    )


class TeamGameStats(ProvenanceMixin, Base):
    __tablename__ = "team_game_stats"
    game_id: Mapped[int] = mapped_column(ForeignKey("games.game_id"), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"), primary_key=True)
    opponent_team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"))
    is_home: Mapped[bool] = mapped_column(Boolean)
    win: Mapped[bool] = mapped_column(Boolean)
    points: Mapped[int] = mapped_column(SmallInteger)
    minutes: Mapped[float | None] = mapped_column(Float)
    fgm: Mapped[int | None] = mapped_column(SmallInteger)
    fga: Mapped[int | None] = mapped_column(SmallInteger)
    fg3m: Mapped[int | None] = mapped_column(SmallInteger)
    fg3a: Mapped[int | None] = mapped_column(SmallInteger)
    ftm: Mapped[int | None] = mapped_column(SmallInteger)
    fta: Mapped[int | None] = mapped_column(SmallInteger)
    oreb: Mapped[int | None] = mapped_column(SmallInteger)
    dreb: Mapped[int | None] = mapped_column(SmallInteger)
    reb: Mapped[int | None] = mapped_column(SmallInteger)
    ast: Mapped[int | None] = mapped_column(SmallInteger)
    stl: Mapped[int | None] = mapped_column(SmallInteger)
    blk: Mapped[int | None] = mapped_column(SmallInteger)
    tov: Mapped[int | None] = mapped_column(SmallInteger)
    pf: Mapped[int | None] = mapped_column(SmallInteger)
    plus_minus: Mapped[int | None] = mapped_column(SmallInteger)
    q1_points: Mapped[int | None] = mapped_column(SmallInteger)
    q2_points: Mapped[int | None] = mapped_column(SmallInteger)
    q3_points: Mapped[int | None] = mapped_column(SmallInteger)
    q4_points: Mapped[int | None] = mapped_column(SmallInteger)
    ot_points: Mapped[int | None] = mapped_column(SmallInteger)
    __table_args__ = (Index("ix_tgs_team", "team_id"),)


class PlayerGameStats(ProvenanceMixin, Base):
    """One row per player listed for a game.

    ``participation`` separates a real zero-stat appearance from a DNP or an unknown
    (NFR-08.2): played | dnp | inactive | unknown.
    """
    __tablename__ = "player_game_stats"
    game_id: Mapped[int] = mapped_column(ForeignKey("games.game_id"), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.player_id"), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"))
    opponent_team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"))
    is_home: Mapped[bool] = mapped_column(Boolean)
    participation: Mapped[str] = mapped_column(String(16))
    status_comment: Mapped[str | None] = mapped_column(String(200))
    starter: Mapped[bool | None] = mapped_column(Boolean)
    starting_position: Mapped[str | None] = mapped_column(String(4))
    minutes: Mapped[float | None] = mapped_column(Float)
    points: Mapped[int | None] = mapped_column(SmallInteger)
    assists: Mapped[int | None] = mapped_column(SmallInteger)
    rebounds: Mapped[int | None] = mapped_column(SmallInteger)
    oreb: Mapped[int | None] = mapped_column(SmallInteger)
    dreb: Mapped[int | None] = mapped_column(SmallInteger)
    fgm: Mapped[int | None] = mapped_column(SmallInteger)
    fga: Mapped[int | None] = mapped_column(SmallInteger)
    fg3m: Mapped[int | None] = mapped_column(SmallInteger)
    fg3a: Mapped[int | None] = mapped_column(SmallInteger)
    ftm: Mapped[int | None] = mapped_column(SmallInteger)
    fta: Mapped[int | None] = mapped_column(SmallInteger)
    steals: Mapped[int | None] = mapped_column(SmallInteger)
    blocks: Mapped[int | None] = mapped_column(SmallInteger)
    turnovers: Mapped[int | None] = mapped_column(SmallInteger)
    fouls: Mapped[int | None] = mapped_column(SmallInteger)
    plus_minus: Mapped[int | None] = mapped_column(SmallInteger)
    __table_args__ = (
        Index("ix_pgs_player", "player_id"),
        Index("ix_pgs_team_game", "team_id", "game_id"),
    )


# ---------------------------------------------------------------- models & predictions

class Model(Base):
    """Model registry / lineage (NFR-09.4)."""
    __tablename__ = "models"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target: Mapped[str] = mapped_column(String(32))      # game | player_points | player_assists | player_rebounds
    version: Mapped[str] = mapped_column(String(64))
    algorithm: Mapped[str] = mapped_column(String(64))
    train_start: Mapped[date | None] = mapped_column(Date)
    train_end: Mapped[date | None] = mapped_column(Date)
    validation_start: Mapped[date | None] = mapped_column(Date)
    validation_end: Mapped[date | None] = mapped_column(Date)
    params: Mapped[dict | None] = mapped_column(JSONB)
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    artifact_path: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    trained_at: Mapped[datetime] = _now()
    __table_args__ = (UniqueConstraint("target", "version"),)


class PredictionRun(Base):
    __tablename__ = "prediction_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # live | backtest
    data_cutoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    stats: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Prediction(Base):
    """Immutable pregame forecast snapshot (FR-02.7, FR-06.4, FR-09.1, FR-09.6).

    A new run for the same game writes a new ``revision``; old rows are never edited.
    ``kind`` = 'live' for forecasts published before tipoff, 'backtest' for forecasts
    regenerated after the fact with point-in-time data (never presented as published).
    """
    __tablename__ = "predictions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("prediction_runs.id"))
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id"))
    kind: Mapped[str] = mapped_column(String(16))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.game_id"))
    revision: Mapped[int] = mapped_column(SmallInteger)
    market: Mapped[str] = mapped_column(String(32))
    subject_type: Mapped[str] = mapped_column(String(8))  # game | team | player
    subject_id: Mapped[int | None] = mapped_column(BigInteger)
    side: Mapped[str | None] = mapped_column(String(16))  # home | away | over | under
    threshold_kind: Mapped[str | None] = mapped_column(String(16))
    threshold: Mapped[float | None] = mapped_column(Float)
    probability: Mapped[float | None] = mapped_column(Float)
    push_probability: Mapped[float | None] = mapped_column(Float)
    projection: Mapped[float | None] = mapped_column(Float)
    label: Mapped[str | None] = mapped_column(String(200))
    details: Mapped[dict | None] = mapped_column(JSONB)
    data_cutoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime] = _now()
    __table_args__ = (
        Index("ix_pred_game_rev", "game_id", "kind", "revision"),
        Index("ix_pred_market", "market", "kind"),
    )


class PredictionOutcome(Base):
    """Grading lives in its own table so snapshots are never rewritten (FR-09.6)."""
    __tablename__ = "prediction_outcomes"
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"), primary_key=True)
    graded_state: Mapped[str] = mapped_column(String(16))
    outcome: Mapped[str | None] = mapped_column(String(8))  # hit | miss | push
    observed_value: Mapped[float | None] = mapped_column(Float)
    brier: Mapped[float | None] = mapped_column(Float)
    log_loss: Mapped[float | None] = mapped_column(Float)
    abs_error: Mapped[float | None] = mapped_column(Float)
    graded_at: Mapped[datetime] = _now()


class AdminAuditEvent(Base):
    __tablename__ = "admin_audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()

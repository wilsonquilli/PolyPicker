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


class NbaInjuryReport(ProvenanceMixin, Base):
    """ESPN injury report, one row per player per ET day (the day's latest status wins).

    Kept per day rather than overwritten so a forecast can be traced to the report it saw.
    ``player_id`` is matched by name within the team (NULL when no confident match).
    """
    __tablename__ = "nba_injury_reports"
    report_date: Mapped[date] = mapped_column(Date, primary_key=True)
    espn_athlete_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.player_id"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"))
    player_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))          # Out | Doubtful | Questionable | Day-To-Day | ...
    detail: Mapped[str | None] = mapped_column(String(128))  # e.g. "Back strain"
    short_comment: Mapped[str | None] = mapped_column(Text)
    return_date: Mapped[date | None] = mapped_column(Date)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_nba_injuries_team_date", "team_id", "report_date"),)


class NbaOdds(ProvenanceMixin, Base):
    """Sportsbook line per game (ESPN odds feed): one preferred book, closing line once the game starts.

    ``p_home`` is the moneyline converted to a probability with the bookmaker margin removed
    (normalised implied probabilities). For upcoming games the row tracks the current line.
    """
    __tablename__ = "nba_odds"
    game_id: Mapped[int] = mapped_column(ForeignKey("games.game_id"), primary_key=True)
    espn_event_id: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(64))
    home_ml: Mapped[int | None] = mapped_column(Integer)
    away_ml: Mapped[int | None] = mapped_column(Integer)
    open_home_ml: Mapped[int | None] = mapped_column(Integer)
    open_away_ml: Mapped[int | None] = mapped_column(Integer)
    spread_home: Mapped[float | None] = mapped_column(Float)   # home handicap, e.g. -5.5 = home favoured by 5.5
    total: Mapped[float | None] = mapped_column(Float)
    p_home: Mapped[float | None] = mapped_column(Float)
    p_home_open: Mapped[float | None] = mapped_column(Float)
    is_closing: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PolymarketNba(ProvenanceMixin, Base):
    """Polymarket moneyline market per NBA game (Gamma API + CLOB price history).

    ``p_home_live`` is the latest traded/mid price of the home outcome while the market is open;
    ``p_home_pregame`` the last price at or before tip-off (the comparison we grade).
    """
    __tablename__ = "polymarket_nba"
    game_id: Mapped[int] = mapped_column(ForeignKey("games.game_id"), primary_key=True)
    market_id: Mapped[str] = mapped_column(String(32))
    slug: Mapped[str] = mapped_column(String(160))
    event_slug: Mapped[str | None] = mapped_column(String(160))
    condition_id: Mapped[str | None] = mapped_column(String(80))
    token_home: Mapped[str] = mapped_column(String(100))
    token_away: Mapped[str] = mapped_column(String(100))
    p_home_live: Mapped[float | None] = mapped_column(Float)
    p_home_pregame: Mapped[float | None] = mapped_column(Float)
    pregame_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    volume_usd: Mapped[float | None] = mapped_column(Float)
    liquidity_usd: Mapped[float | None] = mapped_column(Float)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NbaOddsBook(ProvenanceMixin, Base):
    """Every sportsbook's game line (ESPN odds feed): opening and closing/current moneyline, spread, total.
    Feeds line shopping and closing-line value; ``nba_odds`` keeps the single preferred book."""
    __tablename__ = "nba_odds_books"
    game_id: Mapped[int] = mapped_column(ForeignKey("games.game_id"), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    home_ml_open: Mapped[int | None] = mapped_column(Integer)
    away_ml_open: Mapped[int | None] = mapped_column(Integer)
    home_ml: Mapped[int | None] = mapped_column(Integer)        # closing once the game starts, current before
    away_ml: Mapped[int | None] = mapped_column(Integer)
    spread_home_open: Mapped[float | None] = mapped_column(Float)
    spread_home: Mapped[float | None] = mapped_column(Float)
    total_open: Mapped[float | None] = mapped_column(Float)
    total: Mapped[float | None] = mapped_column(Float)
    is_closing: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NbaPropOdds(ProvenanceMixin, Base):
    """Player over/under prop prices (ESPN odds feed, one book per game): opening and closing/current line
    with decimal odds for over and under. Stats limited to the ones PolyPicker models."""
    __tablename__ = "nba_prop_odds"
    game_id: Mapped[int] = mapped_column(ForeignKey("games.game_id"), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.player_id"), primary_key=True)
    stat: Mapped[str] = mapped_column(String(16), primary_key=True)   # points | rebounds | assists
    provider: Mapped[str] = mapped_column(String(64))
    espn_athlete_id: Mapped[str] = mapped_column(String(16))
    line_open: Mapped[float | None] = mapped_column(Float)
    over_open: Mapped[float | None] = mapped_column(Float)
    under_open: Mapped[float | None] = mapped_column(Float)
    line: Mapped[float | None] = mapped_column(Float)
    over: Mapped[float | None] = mapped_column(Float)
    under: Mapped[float | None] = mapped_column(Float)
    is_closing: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EspnNbaAthlete(Base):
    """ESPN athlete id -> our player id (matched by name), cached so props don't re-fetch athletes."""
    __tablename__ = "espn_nba_athletes"
    espn_athlete_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.player_id"))


class ValuePick(Base):
    """A value bet flagged live: our probability vs the best price on offer, frozen the first time it
    qualified (for honest CLV), then settled after the game. ``last_*`` track later refreshes."""
    __tablename__ = "value_picks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.game_id"), index=True)
    market: Mapped[str] = mapped_column(String(32))            # moneyline | player_points | ...
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.player_id"))
    side: Mapped[str] = mapped_column(String(8))                # home | away | over | under
    line: Mapped[float | None] = mapped_column(Float)
    our_p: Mapped[float] = mapped_column(Float)
    push_p: Mapped[float] = mapped_column(Float, default=0.0)
    price_dec: Mapped[float] = mapped_column(Float)
    book: Mapped[str] = mapped_column(String(64))
    fair_p: Mapped[float | None] = mapped_column(Float)         # book's no-vig probability of our side when flagged
    ev: Mapped[float] = mapped_column(Float)
    kelly: Mapped[float] = mapped_column(Float)
    prediction_revision: Mapped[int | None] = mapped_column(Integer)
    flagged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_our_p: Mapped[float | None] = mapped_column(Float)
    last_price_dec: Mapped[float | None] = mapped_column(Float)
    last_book: Mapped[str | None] = mapped_column(String(64))
    last_ev: Mapped[float | None] = mapped_column(Float)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_price_dec: Mapped[float | None] = mapped_column(Float)
    close_line: Mapped[float | None] = mapped_column(Float)
    clv_prob: Mapped[float | None] = mapped_column(Float)
    beat_close: Mapped[bool | None] = mapped_column(Boolean)
    result: Mapped[str | None] = mapped_column(String(8))       # win | loss | push | void
    profit: Mapped[float | None] = mapped_column(Float)         # units, 1-unit stake
    graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("game_id", "market", "player_id", "side", name="uq_value_pick"),)


class ValueBacktest(Base):
    """Stored value-finder backtest reports (winners / props per season) for the Value page."""
    __tablename__ = "value_backtests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = _now()
    market: Mapped[str] = mapped_column(String(16))
    season: Mapped[int] = mapped_column(SmallInteger)
    report: Mapped[dict] = mapped_column(JSONB)


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


# ---------------------------------------------------------------- championship forecasts

class FuturesForecast(Base):
    """Season simulation for one competition: each team's chance to win it, plus the stats behind the pick.

    One row per run; readers take the latest row per competition. ``payload`` holds the team list
    (probabilities, current record, ratings) built by ``polypicker.futures``."""
    __tablename__ = "futures_forecasts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sport: Mapped[str] = mapped_column(String(16))            # nba | nfl | soccer
    competition: Mapped[str] = mapped_column(String(32))      # nba | nfl | soccer league slug (epl, ucl, ...)
    season: Mapped[int] = mapped_column(SmallInteger)
    simulations: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSONB)
    generated_at: Mapped[datetime] = _now()
    __table_args__ = (Index("ix_futures_comp_time", "competition", "generated_at"),)

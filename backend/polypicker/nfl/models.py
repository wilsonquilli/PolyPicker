"""NFL tables. Same conventions as the NBA schema (provenance on every ingested row, append-only
forecasts, grading in a separate table); shared infrastructure (data_sources, ingestion_runs,
models, prediction_runs) is reused.

IDs
* game_id   ESPN event id (numeric). nflverse carries it for every game since 1999, and the ESPN
            live feed uses it, so both sources update the same row. nflverse's own id is kept too.
* team_id   NFL franchise id from nflverse (e.g. 2520 Raiders, stable across relocations).
* player_id NFL GSIS id ("00-0033873"), nflverse's player key; ESPN / PFR ids kept for joins.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, SmallInteger,
                        String, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from polypicker.db.models import Base, ProvenanceMixin


class NflTeam(ProvenanceMixin, Base):
    __tablename__ = "nfl_teams"
    team_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    abbreviation: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(64))           # "Chiefs"
    full_name: Mapped[str] = mapped_column(String(128))     # "Kansas City Chiefs"
    conference: Mapped[str | None] = mapped_column(String(8))
    division: Mapped[str | None] = mapped_column(String(16))
    color: Mapped[str | None] = mapped_column(String(16))
    color2: Mapped[str | None] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class NflPlayer(ProvenanceMixin, Base):
    __tablename__ = "nfl_players"
    player_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    full_name: Mapped[str | None] = mapped_column(String(128))
    first_name: Mapped[str | None] = mapped_column(String(64))
    last_name: Mapped[str | None] = mapped_column(String(64))
    position: Mapped[str | None] = mapped_column(String(8))
    jersey: Mapped[str | None] = mapped_column(String(4))
    birth_date: Mapped[date | None] = mapped_column(Date)
    height_inches: Mapped[int | None] = mapped_column(SmallInteger)
    weight_lbs: Mapped[int | None] = mapped_column(SmallInteger)
    college: Mapped[str | None] = mapped_column(String(128))
    espn_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    pfr_id: Mapped[str | None] = mapped_column(String(16), index=True)
    headshot_url: Mapped[str | None] = mapped_column(String(300))


class NflGame(ProvenanceMixin, Base):
    __tablename__ = "nfl_games"
    game_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)        # ESPN event id
    nflverse_id: Mapped[str] = mapped_column(String(32), unique=True)         # "2026_11_IND_HOU"
    season: Mapped[int] = mapped_column(SmallInteger)
    season_type: Mapped[str] = mapped_column(String(16))                      # regular | playoffs
    game_type: Mapped[str] = mapped_column(String(8))                         # REG WC DIV CON SB
    week: Mapped[int] = mapped_column(SmallInteger)
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    game_date_et: Mapped[date] = mapped_column(Date)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("nfl_teams.team_id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("nfl_teams.team_id"))
    status: Mapped[str] = mapped_column(String(16))                           # scheduled live final postponed canceled unknown
    home_score: Mapped[int | None] = mapped_column(SmallInteger)
    away_score: Mapped[int | None] = mapped_column(SmallInteger)
    winner_team_id: Mapped[int | None] = mapped_column(Integer)               # NULL for a tie
    overtime: Mapped[bool | None] = mapped_column(Boolean)
    neutral_site: Mapped[bool] = mapped_column(Boolean, default=False)
    div_game: Mapped[bool] = mapped_column(Boolean, default=False)
    roof: Mapped[str | None] = mapped_column(String(16))
    surface: Mapped[str | None] = mapped_column(String(32))
    stadium: Mapped[str | None] = mapped_column(String(128))
    home_rest: Mapped[int | None] = mapped_column(SmallInteger)
    away_rest: Mapped[int | None] = mapped_column(SmallInteger)
    # Closing betting-market lines (nflverse): accuracy benchmark, and the market model's input when posted.
    market_spread_home: Mapped[float | None] = mapped_column(Float)          # + = home favored by N
    market_total: Mapped[float | None] = mapped_column(Float)
    has_player_stats: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        Index("ix_nfl_games_date", "game_date_et"),
        Index("ix_nfl_games_week", "season", "week"),
        Index("ix_nfl_games_home", "home_team_id", "kickoff_utc"),
        Index("ix_nfl_games_away", "away_team_id", "kickoff_utc"),
    )


class NflTeamGameStats(ProvenanceMixin, Base):
    __tablename__ = "nfl_team_game_stats"
    game_id: Mapped[int] = mapped_column(ForeignKey("nfl_games.game_id"), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("nfl_teams.team_id"), primary_key=True)
    opponent_team_id: Mapped[int] = mapped_column(ForeignKey("nfl_teams.team_id"))
    is_home: Mapped[bool] = mapped_column(Boolean)
    win: Mapped[bool | None] = mapped_column(Boolean)                          # NULL for a tie
    points: Mapped[int | None] = mapped_column(SmallInteger)
    plays: Mapped[int | None] = mapped_column(SmallInteger)                    # dropbacks + carries
    pass_attempts: Mapped[int | None] = mapped_column(SmallInteger)
    completions: Mapped[int | None] = mapped_column(SmallInteger)
    passing_yards: Mapped[int | None] = mapped_column(SmallInteger)
    passing_tds: Mapped[int | None] = mapped_column(SmallInteger)
    interceptions: Mapped[int | None] = mapped_column(SmallInteger)
    sacks: Mapped[int | None] = mapped_column(SmallInteger)
    carries: Mapped[int | None] = mapped_column(SmallInteger)
    rushing_yards: Mapped[int | None] = mapped_column(SmallInteger)
    rushing_tds: Mapped[int | None] = mapped_column(SmallInteger)
    fumbles_lost: Mapped[int | None] = mapped_column(SmallInteger)
    turnovers: Mapped[int | None] = mapped_column(SmallInteger)
    first_downs: Mapped[int | None] = mapped_column(SmallInteger)
    penalties: Mapped[int | None] = mapped_column(SmallInteger)
    penalty_yards: Mapped[int | None] = mapped_column(SmallInteger)
    passing_epa: Mapped[float | None] = mapped_column(Float)
    rushing_epa: Mapped[float | None] = mapped_column(Float)
    q1_points: Mapped[int | None] = mapped_column(SmallInteger)
    q2_points: Mapped[int | None] = mapped_column(SmallInteger)
    q3_points: Mapped[int | None] = mapped_column(SmallInteger)
    q4_points: Mapped[int | None] = mapped_column(SmallInteger)
    ot_points: Mapped[int | None] = mapped_column(SmallInteger)
    __table_args__ = (Index("ix_nfl_tgs_team", "team_id"),)


class NflPlayerGameStats(ProvenanceMixin, Base):
    """Offensive skill players per game. participation: played (offensive snaps or stats) | inactive."""
    __tablename__ = "nfl_player_game_stats"
    game_id: Mapped[int] = mapped_column(ForeignKey("nfl_games.game_id"), primary_key=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("nfl_players.player_id"), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("nfl_teams.team_id"))
    opponent_team_id: Mapped[int] = mapped_column(ForeignKey("nfl_teams.team_id"))
    is_home: Mapped[bool] = mapped_column(Boolean)
    position: Mapped[str | None] = mapped_column(String(8))
    participation: Mapped[str] = mapped_column(String(16))
    status_comment: Mapped[str | None] = mapped_column(String(120))
    offense_snaps: Mapped[int | None] = mapped_column(SmallInteger)
    offense_pct: Mapped[float | None] = mapped_column(Float)
    completions: Mapped[int | None] = mapped_column(SmallInteger)
    pass_attempts: Mapped[int | None] = mapped_column(SmallInteger)
    passing_yards: Mapped[int | None] = mapped_column(SmallInteger)
    passing_tds: Mapped[int | None] = mapped_column(SmallInteger)
    interceptions: Mapped[int | None] = mapped_column(SmallInteger)
    sacks: Mapped[int | None] = mapped_column(SmallInteger)
    carries: Mapped[int | None] = mapped_column(SmallInteger)
    rushing_yards: Mapped[int | None] = mapped_column(SmallInteger)
    rushing_tds: Mapped[int | None] = mapped_column(SmallInteger)
    targets: Mapped[int | None] = mapped_column(SmallInteger)
    receptions: Mapped[int | None] = mapped_column(SmallInteger)
    receiving_yards: Mapped[int | None] = mapped_column(SmallInteger)
    receiving_tds: Mapped[int | None] = mapped_column(SmallInteger)
    fumbles_lost: Mapped[int | None] = mapped_column(SmallInteger)
    scrimmage_tds: Mapped[int | None] = mapped_column(SmallInteger)            # rushing + receiving TDs
    passing_epa: Mapped[float | None] = mapped_column(Float)
    rushing_epa: Mapped[float | None] = mapped_column(Float)
    receiving_epa: Mapped[float | None] = mapped_column(Float)
    __table_args__ = (Index("ix_nfl_pgs_player", "player_id"), Index("ix_nfl_pgs_team_game", "team_id", "game_id"))


class NflInjury(ProvenanceMixin, Base):
    """Official weekly injury report (published before games, so usable as a forecast input)."""
    __tablename__ = "nfl_injuries"
    season: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    game_type: Mapped[str] = mapped_column(String(8), primary_key=True)
    week: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("nfl_teams.team_id"), primary_key=True)
    player_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    position: Mapped[str | None] = mapped_column(String(8))
    report_status: Mapped[str | None] = mapped_column(String(16))              # Out | Doubtful | Questionable
    practice_status: Mapped[str | None] = mapped_column(String(64))
    injury: Mapped[str | None] = mapped_column(String(120))


class NflRosterWeek(ProvenanceMixin, Base):
    """Weekly roster status for offensive skill players: ACT (active), INA (game-day inactive), RES (injured
    reserve / PUP / other reserve list), CUT, TRD, DEV (practice squad), ... Moves other than the game-day
    INA designation are made before kickoff, so they can inform forecasts."""
    __tablename__ = "nfl_roster_weeks"
    season: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    week: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("nfl_teams.team_id"), primary_key=True)
    player_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    position: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(8))


class NflPrediction(Base):
    """Immutable pregame forecast snapshot -- same semantics as ``predictions`` (NBA)."""
    __tablename__ = "nfl_predictions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("prediction_runs.id"))
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id"))
    kind: Mapped[str] = mapped_column(String(16))
    game_id: Mapped[int] = mapped_column(ForeignKey("nfl_games.game_id"))
    revision: Mapped[int] = mapped_column(SmallInteger)
    market: Mapped[str] = mapped_column(String(40))
    subject_type: Mapped[str] = mapped_column(String(8))                       # game | team | player
    subject_id: Mapped[str | None] = mapped_column(String(16))                 # team id or GSIS player id
    side: Mapped[str | None] = mapped_column(String(16))
    threshold_kind: Mapped[str | None] = mapped_column(String(16))
    threshold: Mapped[float | None] = mapped_column(Float)
    probability: Mapped[float | None] = mapped_column(Float)
    push_probability: Mapped[float | None] = mapped_column(Float)
    projection: Mapped[float | None] = mapped_column(Float)
    label: Mapped[str | None] = mapped_column(String(200))
    details: Mapped[dict | None] = mapped_column(JSONB)
    data_cutoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (Index("ix_nfl_pred_game_rev", "game_id", "kind", "revision"),
                      Index("ix_nfl_pred_market", "market", "kind"))


class NflPredictionOutcome(Base):
    __tablename__ = "nfl_prediction_outcomes"
    prediction_id: Mapped[int] = mapped_column(ForeignKey("nfl_predictions.id"), primary_key=True)
    graded_state: Mapped[str] = mapped_column(String(16))
    outcome: Mapped[str | None] = mapped_column(String(8))
    observed_value: Mapped[float | None] = mapped_column(Float)
    brier: Mapped[float | None] = mapped_column(Float)
    log_loss: Mapped[float | None] = mapped_column(Float)
    abs_error: Mapped[float | None] = mapped_column(Float)
    graded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

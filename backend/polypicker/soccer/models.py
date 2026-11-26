"""Soccer tables. Every row carries a ``competition`` (ESPN code; see ``polypicker.soccer.leagues``).

IDs: ESPN team and event ids (ESPN is the primary source and live feed). Other sources map onto them:
openfootball (CC0 history, used to cross-check results) and football-data.org (official API backup,
its match id is stored as ``fd_match_id``). Same conventions as the NBA/NFL schema: provenance on every
ingested row, append-only forecasts, grading in a separate table.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from polypicker.db.models import Base, ProvenanceMixin


class SoccerTeam(ProvenanceMixin, Base):
    __tablename__ = "soccer_teams"
    team_id: Mapped[int] = mapped_column(Integer, primary_key=True)          # ESPN team id
    name: Mapped[str] = mapped_column(String(64))                             # "Manchester City"
    short_name: Mapped[str | None] = mapped_column(String(40))                # "Man City"
    abbreviation: Mapped[str] = mapped_column(String(16))                     # "MNC" (some South American clubs use longer codes)
    color: Mapped[str | None] = mapped_column(String(8))                      # hex without '#'
    alt_color: Mapped[str | None] = mapped_column(String(8))


class SoccerGame(ProvenanceMixin, Base):
    __tablename__ = "soccer_games"
    game_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)        # ESPN event id
    competition: Mapped[str] = mapped_column(String(32))                      # "eng.1", "conmebol.libertadores"
    season: Mapped[int] = mapped_column(SmallInteger)                         # start year: 2026 = 2026-27
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    game_date: Mapped[date] = mapped_column(Date)                             # US-Eastern date, like the rest of the site
    home_team_id: Mapped[int] = mapped_column(ForeignKey("soccer_teams.team_id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("soccer_teams.team_id"))
    status: Mapped[str] = mapped_column(String(16))                           # scheduled live final postponed canceled unknown
    home_score: Mapped[int | None] = mapped_column(SmallInteger)
    away_score: Mapped[int | None] = mapped_column(SmallInteger)
    home_ht_score: Mapped[int | None] = mapped_column(SmallInteger)
    away_ht_score: Mapped[int | None] = mapped_column(SmallInteger)
    result: Mapped[str | None] = mapped_column(String(1))                     # H D A
    venue: Mapped[str | None] = mapped_column(String(128))
    attendance: Mapped[int | None] = mapped_column(Integer)
    has_team_stats: Mapped[bool] = mapped_column(Boolean, default=False)
    fd_match_id: Mapped[int | None] = mapped_column(Integer)                  # football-data.org id
    of_verified: Mapped[bool | None] = mapped_column(Boolean)                 # result matches openfootball (None = not checked)
    # Cups (and split-season leagues). ``result`` is always the 90-minute result, which is what we forecast and grade;
    # home_score / away_score are the official final score (after extra time when ``decided`` is set).
    stage: Mapped[str | None] = mapped_column(String(64))                     # ESPN stage slug: group-stage, round-of-16, final
    leg: Mapped[int | None] = mapped_column(SmallInteger)                     # 1 / 2 for two-legged ties
    neutral: Mapped[bool | None] = mapped_column(Boolean)                     # neutral venue (finals): no home advantage
    decided: Mapped[str | None] = mapped_column(String(4))                    # 'aet' (extra time) | 'pen' (penalties)
    home_score_90: Mapped[int | None] = mapped_column(SmallInteger)           # score after 90 minutes, set when decided
    away_score_90: Mapped[int | None] = mapped_column(SmallInteger)
    home_shootout: Mapped[int | None] = mapped_column(SmallInteger)
    away_shootout: Mapped[int | None] = mapped_column(SmallInteger)
    note: Mapped[str | None] = mapped_column(String(200))                     # "2nd Leg - X advance 5-3 on aggregate"
    __table_args__ = (Index("ix_soccer_games_date", "competition", "game_date"),
                      Index("ix_soccer_games_season", "competition", "season"),
                      Index("ix_soccer_games_home", "home_team_id", "kickoff_utc"),
                      Index("ix_soccer_games_away", "away_team_id", "kickoff_utc"))


class SoccerGroup(ProvenanceMixin, Base):
    """Group membership for cup group stages / league phases and split-season leagues (ESPN standings)."""
    __tablename__ = "soccer_groups"
    competition: Mapped[str] = mapped_column(String(32), primary_key=True)
    season: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("soccer_teams.team_id"), primary_key=True)
    group_name: Mapped[str] = mapped_column(String(64))                       # "Group A", "League Phase"
    position: Mapped[int | None] = mapped_column(SmallInteger)                # ESPN order within the group


class SoccerTeamGameStats(ProvenanceMixin, Base):
    __tablename__ = "soccer_team_game_stats"
    game_id: Mapped[int] = mapped_column(ForeignKey("soccer_games.game_id"), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("soccer_teams.team_id"), primary_key=True)
    is_home: Mapped[bool] = mapped_column(Boolean)
    goals: Mapped[int | None] = mapped_column(SmallInteger)
    shots: Mapped[int | None] = mapped_column(SmallInteger)
    shots_on_target: Mapped[int | None] = mapped_column(SmallInteger)
    possession: Mapped[float | None] = mapped_column(Float)
    corners: Mapped[int | None] = mapped_column(SmallInteger)
    fouls: Mapped[int | None] = mapped_column(SmallInteger)
    yellow_cards: Mapped[int | None] = mapped_column(SmallInteger)
    red_cards: Mapped[int | None] = mapped_column(SmallInteger)
    offsides: Mapped[int | None] = mapped_column(SmallInteger)
    saves: Mapped[int | None] = mapped_column(SmallInteger)
    passes: Mapped[int | None] = mapped_column(SmallInteger)
    pass_pct: Mapped[float | None] = mapped_column(Float)


class SoccerPrediction(Base):
    """Immutable pre-match forecast snapshot (same semantics as the NBA/NFL tables)."""
    __tablename__ = "soccer_predictions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("prediction_runs.id"))
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id"))
    kind: Mapped[str] = mapped_column(String(16))
    game_id: Mapped[int] = mapped_column(ForeignKey("soccer_games.game_id"))
    revision: Mapped[int] = mapped_column(SmallInteger)
    market: Mapped[str] = mapped_column(String(32))          # match_result | team_goals | total_goals | btts
    subject_type: Mapped[str] = mapped_column(String(8))     # game | team
    subject_id: Mapped[int | None] = mapped_column(Integer)
    side: Mapped[str | None] = mapped_column(String(16))     # home | draw | away | over | yes
    threshold_kind: Mapped[str | None] = mapped_column(String(16))
    threshold: Mapped[float | None] = mapped_column(Float)
    probability: Mapped[float | None] = mapped_column(Float)
    push_probability: Mapped[float | None] = mapped_column(Float)
    projection: Mapped[float | None] = mapped_column(Float)
    label: Mapped[str | None] = mapped_column(String(200))
    details: Mapped[dict | None] = mapped_column(JSONB)
    data_cutoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (Index("ix_soccer_pred_game_rev", "game_id", "kind", "revision"),
                      Index("ix_soccer_pred_market", "market", "kind"))


class SoccerPredictionOutcome(Base):
    __tablename__ = "soccer_prediction_outcomes"
    prediction_id: Mapped[int] = mapped_column(ForeignKey("soccer_predictions.id"), primary_key=True)
    graded_state: Mapped[str] = mapped_column(String(16))
    outcome: Mapped[str | None] = mapped_column(String(8))
    observed_value: Mapped[float | None] = mapped_column(Float)
    brier: Mapped[float | None] = mapped_column(Float)
    log_loss: Mapped[float | None] = mapped_column(Float)
    abs_error: Mapped[float | None] = mapped_column(Float)
    graded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SoccerFotmobStats(ProvenanceMixin, Base):
    """Per-match advanced stats from FotMob (Opta-derived): expected goals and chance quality, home / away.
    Unofficial endpoints: research use only. NULL when FotMob has no xG for the match (older seasons)."""
    __tablename__ = "soccer_fotmob_stats"
    game_id: Mapped[int] = mapped_column(ForeignKey("soccer_games.game_id"), primary_key=True)
    fotmob_match_id: Mapped[int] = mapped_column(BigInteger, index=True)
    competition: Mapped[str] = mapped_column(String(32))
    home_xg: Mapped[float | None] = mapped_column(Float)
    away_xg: Mapped[float | None] = mapped_column(Float)
    home_npxg: Mapped[float | None] = mapped_column(Float)
    away_npxg: Mapped[float | None] = mapped_column(Float)
    home_xgot: Mapped[float | None] = mapped_column(Float)          # xG on target
    away_xgot: Mapped[float | None] = mapped_column(Float)
    home_big_chances: Mapped[int | None] = mapped_column(SmallInteger)
    away_big_chances: Mapped[int | None] = mapped_column(SmallInteger)
    home_box_touches: Mapped[int | None] = mapped_column(SmallInteger)
    away_box_touches: Mapped[int | None] = mapped_column(SmallInteger)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SoccerFdcukMatch(ProvenanceMixin, Base):
    """football-data.co.uk per-match file: match stats (shots, corners, fouls, cards, referee), xG where present, and
    bookmaker 1X2 / over-under 2.5 prices. ``p_*`` = margin-free 1X2 from the best available price set
    (``odds_source``: Pinnacle closing > market-average closing > Pinnacle > market average)."""
    __tablename__ = "soccer_fdcuk_matches"
    game_id: Mapped[int] = mapped_column(ForeignKey("soccer_games.game_id"), primary_key=True)
    competition: Mapped[str] = mapped_column(String(32))
    referee: Mapped[str | None] = mapped_column(String(64))
    home_shots: Mapped[int | None] = mapped_column(SmallInteger)
    away_shots: Mapped[int | None] = mapped_column(SmallInteger)
    home_sot: Mapped[int | None] = mapped_column(SmallInteger)
    away_sot: Mapped[int | None] = mapped_column(SmallInteger)
    home_corners: Mapped[int | None] = mapped_column(SmallInteger)
    away_corners: Mapped[int | None] = mapped_column(SmallInteger)
    home_fouls: Mapped[int | None] = mapped_column(SmallInteger)
    away_fouls: Mapped[int | None] = mapped_column(SmallInteger)
    home_yellow: Mapped[int | None] = mapped_column(SmallInteger)
    away_yellow: Mapped[int | None] = mapped_column(SmallInteger)
    home_red: Mapped[int | None] = mapped_column(SmallInteger)
    away_red: Mapped[int | None] = mapped_column(SmallInteger)
    home_xg: Mapped[float | None] = mapped_column(Float)
    away_xg: Mapped[float | None] = mapped_column(Float)
    odds_source: Mapped[str | None] = mapped_column(String(24))
    p_home: Mapped[float | None] = mapped_column(Float)
    p_draw: Mapped[float | None] = mapped_column(Float)
    p_away: Mapped[float | None] = mapped_column(Float)
    p_home_open: Mapped[float | None] = mapped_column(Float)     # market average before the close (line movement)
    p_draw_open: Mapped[float | None] = mapped_column(Float)
    p_away_open: Mapped[float | None] = mapped_column(Float)
    p_over25: Mapped[float | None] = mapped_column(Float)


class SoccerFplPlayer(ProvenanceMixin, Base):
    """Fantasy Premier League player per season: position, club (ESPN id) and -- for the current season -- the
    official availability flag and chance of playing next round."""
    __tablename__ = "soccer_fpl_players"
    season: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    element_id: Mapped[int] = mapped_column(Integer, primary_key=True)     # FPL player id (per season)
    code: Mapped[int | None] = mapped_column(Integer, index=True)          # FPL code: stable across seasons
    name: Mapped[str] = mapped_column(String(80))
    web_name: Mapped[str] = mapped_column(String(40))
    position: Mapped[str] = mapped_column(String(3))                        # GK / DEF / MID / FWD
    team_id: Mapped[int | None] = mapped_column(Integer)                   # ESPN team id
    status: Mapped[str | None] = mapped_column(String(2))                  # a available, d doubtful, i injured, s suspended, u unavailable
    chance_next: Mapped[int | None] = mapped_column(SmallInteger)          # % chance of playing next round (NULL = no news)
    news: Mapped[str | None] = mapped_column(String(300))


class SoccerFplPlayerGame(ProvenanceMixin, Base):
    """One player's FPL line for one Premier League match (history from 2019-20, xG from 2022-23)."""
    __tablename__ = "soccer_fpl_player_games"
    season: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    element_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(Integer, primary_key=True)     # FPL fixture id (per season)
    game_id: Mapped[int | None] = mapped_column(ForeignKey("soccer_games.game_id"), index=True)
    team_id: Mapped[int | None] = mapped_column(Integer)
    was_home: Mapped[bool] = mapped_column(Boolean)
    minutes: Mapped[int] = mapped_column(SmallInteger)
    starts: Mapped[int | None] = mapped_column(SmallInteger)
    goals: Mapped[int] = mapped_column(SmallInteger)
    assists: Mapped[int] = mapped_column(SmallInteger)
    clean_sheets: Mapped[int | None] = mapped_column(SmallInteger)
    goals_conceded: Mapped[int | None] = mapped_column(SmallInteger)
    saves: Mapped[int | None] = mapped_column(SmallInteger)
    yellow: Mapped[int | None] = mapped_column(SmallInteger)
    red: Mapped[int | None] = mapped_column(SmallInteger)
    bonus: Mapped[int | None] = mapped_column(SmallInteger)
    bps: Mapped[int | None] = mapped_column(SmallInteger)
    xg: Mapped[float | None] = mapped_column(Float)
    xa: Mapped[float | None] = mapped_column(Float)
    xgc: Mapped[float | None] = mapped_column(Float)                        # expected goals conceded while on the pitch
    total_points: Mapped[int | None] = mapped_column(SmallInteger)


class SoccerClubElo(ProvenanceMixin, Base):
    """Club Elo (clubelo.com) rating history: one row per club per rating interval [from_date, to_date].
    ``team_id`` = the ESPN club it maps to (NULL for clubs we don't cover). European clubs only."""
    __tablename__ = "soccer_clubelo"
    club: Mapped[str] = mapped_column(String(40), primary_key=True)
    from_date: Mapped[date] = mapped_column(Date, primary_key=True)
    to_date: Mapped[date] = mapped_column(Date)
    team_id: Mapped[int | None] = mapped_column(Integer, index=True)
    country: Mapped[str | None] = mapped_column(String(3))
    level: Mapped[int | None] = mapped_column(SmallInteger)
    elo: Mapped[float] = mapped_column(Float)


class SoccerOddsSnapshot(ProvenanceMixin, Base):
    """Append-only 1X2 price history from The Odds API (Pinnacle, Betfair exchange, European books): one row per
    match x book x fetch, so line movement between opening and kickoff can be measured. Probabilities have the
    book's margin removed (proportional)."""
    __tablename__ = "soccer_odds_snapshots"
    game_id: Mapped[int] = mapped_column(ForeignKey("soccer_games.game_id"), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    competition: Mapped[str] = mapped_column(String(32))
    home_dec: Mapped[float] = mapped_column(Float)        # decimal odds
    draw_dec: Mapped[float] = mapped_column(Float)
    away_dec: Mapped[float] = mapped_column(Float)
    p_home: Mapped[float] = mapped_column(Float)
    p_draw: Mapped[float] = mapped_column(Float)
    p_away: Mapped[float] = mapped_column(Float)
    book_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    minutes_to_kickoff: Mapped[int] = mapped_column(Integer)


class SoccerOdds(ProvenanceMixin, Base):
    """Full-time result (1X2) and total-goals line per match from ESPN's odds feed: the median across every
    pre-match sportsbook ESPN lists (in-play "Live Odds" feeds and settled prices are skipped). Probabilities have
    the bookmaker margin removed. Closing line once the match kicks off; current line before."""
    __tablename__ = "soccer_odds"
    game_id: Mapped[int] = mapped_column(ForeignKey("soccer_games.game_id"), primary_key=True)
    competition: Mapped[str] = mapped_column(String(32))
    providers: Mapped[str] = mapped_column(String(200))          # books used, comma-separated
    n_books: Mapped[int] = mapped_column(SmallInteger)
    p_home: Mapped[float] = mapped_column(Float)
    p_draw: Mapped[float] = mapped_column(Float)
    p_away: Mapped[float] = mapped_column(Float)
    total_line: Mapped[float | None] = mapped_column(Float)      # usually 2.5
    p_over: Mapped[float | None] = mapped_column(Float)          # P(total > total_line), margin removed
    is_closing: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


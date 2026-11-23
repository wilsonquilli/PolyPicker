"""Load NFL modeling inputs from PostgreSQL into Polars."""

from __future__ import annotations

import polars as pl
from sqlalchemy import Engine

from polypicker.ml.data import _read

STAT_COLS = ["pass_attempts", "completions", "passing_yards", "passing_tds", "interceptions", "carries",
             "rushing_yards", "rushing_tds", "targets", "receptions", "receiving_yards", "receiving_tds",
             "scrimmage_tds"]


def load_games(engine: Engine) -> pl.DataFrame:
    return _read(engine, """
        SELECT game_id, season, season_type, game_type, week, kickoff_utc, game_date_et, home_team_id, away_team_id,
               status, home_score, away_score, neutral_site, div_game, roof, home_rest, away_rest,
               market_spread_home, market_total
        FROM nfl_games WHERE status <> 'canceled'
        ORDER BY kickoff_utc, game_id""",
                 schema_overrides={"market_spread_home": pl.Float64, "market_total": pl.Float64}).with_columns(
        pl.col("kickoff_utc").dt.convert_time_zone("UTC"))


def load_team_box(engine: Engine) -> pl.DataFrame:
    return _read(engine, """
        SELECT t.game_id, t.team_id, t.points, t.plays, t.pass_attempts, t.carries, t.passing_yards, t.rushing_yards,
               t.passing_epa, t.rushing_epa, t.turnovers
        FROM nfl_team_game_stats t JOIN nfl_games g USING (game_id)
        WHERE g.status = 'final' AND t.plays IS NOT NULL""",
                 schema_overrides={"passing_epa": pl.Float64, "rushing_epa": pl.Float64})


def load_player_box(engine: Engine) -> pl.DataFrame:
    cols = ", ".join(f"p.{c}" for c in STAT_COLS)
    return _read(engine, f"""
        SELECT p.game_id, p.player_id, p.team_id, p.position, p.participation, p.offense_pct, {cols}
        FROM nfl_player_game_stats p JOIN nfl_games g USING (game_id)
        WHERE g.status = 'final'""", schema_overrides={"offense_pct": pl.Float64, **{c: pl.Int64 for c in STAT_COLS}})


def load_injuries(engine: Engine) -> pl.DataFrame:
    return _read(engine, """SELECT season, game_type, week, team_id, player_id, position, report_status, injury
                            FROM nfl_injuries""")


def load_roster_weeks(engine: Engine) -> pl.DataFrame:
    return _read(engine, "SELECT season, week, team_id, player_id, status FROM nfl_roster_weeks")

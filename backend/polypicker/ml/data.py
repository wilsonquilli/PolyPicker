"""Load modeling inputs from PostgreSQL into Polars."""

from __future__ import annotations

import polars as pl
from sqlalchemy import Engine, text

COMPETITIVE = ("regular", "playoffs", "play_in", "cup_final")


def _read(engine: Engine, sql: str, schema_overrides: dict | None = None) -> pl.DataFrame:
    with engine.connect() as conn:
        res = conn.execute(text(sql))
        cols = list(res.keys())
        rows = res.fetchall()
    return pl.DataFrame(rows, schema=cols, orient="row", schema_overrides=schema_overrides, infer_schema_length=None)


def load_games(engine: Engine) -> pl.DataFrame:
    """Competitive games (final + upcoming); canceled games excluded."""
    return _read(engine, f"""
        SELECT game_id, season, season_type, tipoff_utc, game_date_et, home_team_id, away_team_id,
               status, home_score, away_score
        FROM games
        WHERE season_type IN {COMPETITIVE} AND status <> 'canceled'
        ORDER BY tipoff_utc, game_id""").with_columns(
        pl.col("tipoff_utc").dt.convert_time_zone("UTC"))


def load_team_box(engine: Engine) -> pl.DataFrame:
    return _read(engine, f"""
        SELECT t.game_id, t.team_id, t.points, t.fga, t.oreb, t.tov, t.fta
        FROM team_game_stats t JOIN games g USING (game_id)
        WHERE g.season_type IN {COMPETITIVE} AND g.status = 'final'""")


def load_player_box(engine: Engine) -> pl.DataFrame:
    return _read(engine, f"""
        SELECT p.game_id, p.player_id, p.team_id, p.participation, p.status_comment, p.minutes,
               p.points, p.assists, p.rebounds, p.starter
        FROM player_game_stats p JOIN games g USING (game_id)
        WHERE g.season_type IN {COMPETITIVE} AND g.status = 'final'""",
                 schema_overrides={"minutes": pl.Float64, "points": pl.Int64, "assists": pl.Int64,
                                   "rebounds": pl.Int64})

"""SportsDataverse NBA possessions -> nba_lineup_possessions (possession-level RAPM input).

Release ``nba_stats_possessions`` of github.com/sportsdataverse/sportsdataverse-data: one parquet per season
(their label = the year the season ends, so 2026 = our 2025), one row per possession with the offence's and
defence's five players and the points scored. Garbage time and rows that don't count as a possession are dropped,
then possessions are summed per game x offensive unit x defensive unit. Published after each season, so the
current season isn't in it (game-level APM keeps covering that).
"""

from __future__ import annotations

import io
import logging

import polars as pl
from sqlalchemy import Engine, text

from polypicker.db import models as m
from polypicker.etl import load

URL = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/nba_stats_possessions/nba_possessions_{year}.parquet"
log = logging.getLogger(__name__)


def http_get(url: str) -> bytes | None:
    import requests
    r = requests.get(url, timeout=120)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.content


def aggregate(p: pl.DataFrame, season: int) -> pl.DataFrame:
    off = [f"off_player_{i}" for i in range(1, 6)]
    dfn = [f"def_player_{i}" for i in range(1, 6)]
    p = p.filter(pl.col("count_as_possession").fill_null(True) & ~pl.col("is_garbage_time").fill_null(False)
                 & pl.all_horizontal([pl.col(c).is_not_null() for c in off + dfn])
                 & pl.col("game_id").str.slice(0, 3).is_in(["002", "004"]))     # regular season + playoffs
    return (p.with_columns(pl.concat_list(off).list.sort().cast(pl.List(pl.Int64)).alias("off_ids"),
                           pl.concat_list(dfn).list.sort().cast(pl.List(pl.Int64)).alias("def_ids"),
                           pl.col("game_id").cast(pl.Int64),
                           pl.col("offense_team_id").cast(pl.Int64).alias("off_team_id"),
                           pl.col("defense_team_id").cast(pl.Int64).alias("def_team_id"))
            .group_by("game_id", "off_team_id", "def_team_id", "off_ids", "def_ids")
            .agg(pl.len().alias("possessions"), pl.col("points").sum().alias("points"))
            .with_columns(pl.lit(season).alias("season")))


def sync_possessions(engine: Engine, seasons: list[int], *, get=http_get) -> dict:
    out = {}
    for season in seasons:
        raw = get(URL.format(year=season + 1))
        if raw is None:
            out[season] = "not published"
            continue
        df = aggregate(pl.read_parquet(io.BytesIO(raw)), season)
        with engine.begin() as c:
            c.execute(text("DELETE FROM nba_lineup_possessions WHERE season = :s"), {"s": season})
            res = load.upsert(c, m.NbaLineupPossessions.__table__, df)
        out[season] = {"rows": df.height, "possessions": int(df["possessions"].sum()), **res}
        log.info("possessions %s: %s", season, out[season])
    return out

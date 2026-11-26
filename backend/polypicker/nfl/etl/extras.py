"""nflverse extras (via nflreadpy) -> nfl_depth_charts, nfl_ngs_weekly, nfl_ftn_team_games, nfl_game_officials.

* Depth charts: weekly charts to 2024 (``club_code``/``week``/``depth_team``); from 2025 nflverse publishes daily
  snapshots (``dt``/``pos_abb``/``pos_rank``) -- each team-week keeps the last snapshot before kickoff, and the next
  scheduled game gets the latest one. Offensive skill positions only.
* Next Gen Stats: passing / receiving / rushing per player-week (2016 on).
* FTN charting (2022 on): play-level flags joined to play-by-play for the offence / defence, rolled up per team-game.
* Officials: the crew per game (nflverse ``old_game_id`` -> our ESPN game id via the schedule).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import polars as pl
from sqlalchemy import Engine, text

from polypicker.etl import load
from polypicker.etl.issues import IssueLog
from polypicker.nfl import models as nm
from polypicker.nfl.etl.runner import tracked

SKILL = ("QB", "RB", "FB", "WR", "TE")
NGS_TYPES = ("passing", "receiving", "rushing")
NGS_FIRST, FTN_FIRST, DEPTH_FIRST = 2016, 2022, 2018

log = logging.getLogger(__name__)


def _abbr_map(nfl) -> dict[str, int]:
    t = nfl.load_teams()
    return dict(zip(t["team_abbr"].to_list(), t["team_id"].cast(pl.Int64).to_list()))


def depth_weekly(d: pl.DataFrame, abbr: dict[str, int]) -> pl.DataFrame:
    """2024-and-earlier format."""
    pos = pl.coalesce(pl.col("depth_position"), pl.col("position"))
    return (d.filter(pl.col("formation") == "Offense").with_columns(pos.alias("pos"))
            .filter(pl.col("pos").is_in(SKILL) & pl.col("gsis_id").is_not_null() & pl.col("week").is_not_null())
            .select(pl.col("season").cast(pl.Int64), pl.col("week").cast(pl.Int64),
                    pl.col("club_code").replace_strict(abbr, default=None).alias("team_id"),
                    pl.col("gsis_id").alias("player_id"), pl.col("pos").alias("position"),
                    pl.col("depth_team").cast(pl.Int64, strict=False).alias("depth_rank"),
                    pl.lit(None, pl.Datetime("us", "UTC")).alias("snapshot_at"))
            .filter(pl.col("team_id").is_not_null() & pl.col("depth_rank").is_not_null())
            .sort("depth_rank").unique(["season", "week", "team_id", "player_id", "position"], keep="first"))


def depth_snapshots(d: pl.DataFrame, abbr: dict[str, int], games: pl.DataFrame) -> pl.DataFrame:
    """2025+ format: daily snapshots -> the last one before each team's game (games: season, week, team_id, kickoff_utc)."""
    d = (d.filter(pl.col("pos_abb").is_in(SKILL) & pl.col("gsis_id").is_not_null())
         .with_columns(pl.col("dt").str.to_datetime(time_zone="UTC", strict=False).cast(pl.Datetime("us", "UTC")).alias("snap"),
                       pl.col("team").replace_strict(abbr, default=None).alias("team_id"))
         .filter(pl.col("team_id").is_not_null() & pl.col("snap").is_not_null()))
    snaps = d.select("team_id", "snap").unique().sort("snap")
    g = games.with_columns(pl.col("kickoff_utc").cast(pl.Datetime("us", "UTC"))).sort("kickoff_utc")
    pick = g.join_asof(snaps, left_on="kickoff_utc", right_on="snap", by="team_id", strategy="backward").filter(pl.col("snap").is_not_null())
    # each snapshot belongs to one team-week: if two weeks map to the same snapshot (no update), both keep it
    out = pick.join(d, on=["team_id", "snap"]).select(
        pl.col("season").cast(pl.Int64), pl.col("week").cast(pl.Int64), "team_id", pl.col("gsis_id").alias("player_id"),
        pl.col("pos_abb").alias("position"), pl.col("pos_rank").cast(pl.Int64).alias("depth_rank"), pl.col("snap").alias("snapshot_at"))
    return out.sort("depth_rank").unique(["season", "week", "team_id", "player_id", "position"], keep="first")


def ngs(nfl, seasons: list[int], abbr: dict[str, int]) -> pl.DataFrame:
    frames = []
    keys = {"season", "season_type", "week", "player_gsis_id", "team_abbr", "player_display_name", "player_position",
            "player_first_name", "player_last_name", "player_jersey_number", "player_short_name"}
    for t in NGS_TYPES:
        df = nfl.load_nextgen_stats([s for s in seasons if s >= NGS_FIRST], t)
        if df.is_empty():
            continue
        metric_cols = [c for c in df.columns if c not in keys]
        frames.append(df.filter(pl.col("player_gsis_id").is_not_null()).select(
            pl.col("season").cast(pl.Int64), pl.col("season_type").str.slice(0, 4), pl.col("week").cast(pl.Int64),
            pl.lit(t).alias("stat_type"), pl.col("player_gsis_id").alias("player_id"),
            pl.col("team_abbr").replace_strict(abbr, default=None).alias("team_id"),
            pl.struct([pl.col(c).cast(pl.Float64, strict=False) for c in metric_cols]).alias("metrics")))
    if not frames:
        return pl.DataFrame()
    # one row per player-week; metrics differ per stat type, so they're stored as JSON
    rows = [{**r, "metrics": {k: v for k, v in r["metrics"].items() if v is not None}}
            for f in frames for r in f.to_dicts()]
    return pl.DataFrame(rows, schema_overrides={"metrics": pl.Object}).unique(
        ["season", "season_type", "week", "stat_type", "player_id"], keep="last") if rows else pl.DataFrame()


def ftn_team_games(nfl, seasons: list[int], gid: dict[str, int], abbr: dict[str, int]) -> pl.DataFrame:
    ss = [s for s in seasons if s >= FTN_FIRST]
    if not ss:
        return pl.DataFrame()
    f = nfl.load_ftn_charting(ss)
    p = nfl.load_pbp(ss).select("game_id", pl.col("play_id").cast(pl.Int64), "posteam", "defteam", "pass", "rush")
    f = f.with_columns(pl.col("nflverse_play_id").cast(pl.Int64))
    j = f.join(p, left_on=["nflverse_game_id", "nflverse_play_id"], right_on=["game_id", "play_id"]).filter(
        pl.col("posteam").is_not_null() & ((pl.col("pass") == 1) | (pl.col("rush") == 1)))
    rate = lambda c: pl.col(c).cast(pl.Float64).mean()  # noqa: E731
    off = j.group_by("nflverse_game_id", "posteam").agg(
        pl.len().alias("plays"), rate("is_play_action").alias("play_action_rate"), rate("is_motion").alias("motion_rate"),
        rate("is_no_huddle").alias("no_huddle_rate"), rate("is_screen_pass").alias("screen_rate"), rate("is_rpo").alias("rpo_rate"),
        rate("is_qb_out_of_pocket").alias("out_of_pocket_rate"),
        (pl.col("is_drop").cast(pl.Int64).sum() / pl.col("is_catchable_ball").cast(pl.Int64).sum().clip(1)).alias("drop_rate"),
        pl.col("n_defense_box").cast(pl.Float64).filter(pl.col("n_defense_box") > 0).mean().alias("avg_box"))
    dfn = j.filter(pl.col("pass") == 1).group_by("nflverse_game_id", "defteam").agg(
        pl.len().alias("def_plays"), (pl.col("n_blitzers").cast(pl.Int64) > 0).cast(pl.Float64).mean().alias("def_blitz_rate"),
        pl.col("n_pass_rushers").cast(pl.Float64).filter(pl.col("n_pass_rushers") > 0).mean().alias("def_avg_rushers"))
    out = off.join(dfn, left_on=["nflverse_game_id", "posteam"], right_on=["nflverse_game_id", "defteam"], how="left")
    return (out.with_columns(pl.col("nflverse_game_id").replace_strict(gid, default=None).alias("game_id"),
                             pl.col("posteam").replace_strict(abbr, default=None).alias("team_id"))
            .filter(pl.col("game_id").is_not_null() & pl.col("team_id").is_not_null())
            .drop("nflverse_game_id", "posteam"))


def officials(nfl, seasons: list[int], old_to_game: dict[str, int]) -> pl.DataFrame:
    o = nfl.load_officials(seasons)
    return (o.with_columns(pl.col("game_id").cast(pl.Utf8).replace_strict(old_to_game, default=None).alias("gid"))
            .filter(pl.col("gid").is_not_null() & pl.col("official_id").is_not_null())
            .select(pl.col("gid").alias("game_id"), pl.col("official_id").cast(pl.Utf8), pl.col("official_name").str.slice(0, 80).alias("name"),
                    pl.col("position").str.slice(0, 24))
            .unique(["game_id", "official_id"]))


def sync_nfl_extras(engine: Engine, seasons: list[int], *, loader=None) -> dict:
    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        import nflreadpy
        nfl = loader or nflreadpy
        abbr = _abbr_map(nfl)
        sched = nfl.load_schedules(seasons)
        old_to_game = {str(o): int(e) for o, e in zip(sched["old_game_id"].to_list(), sched["espn"].to_list()) if o and e}
        with engine.connect() as c:
            gid = dict(c.execute(text("SELECT nflverse_id, game_id FROM nfl_games WHERE season = ANY(:s)"), {"s": seasons}).all())
            tg = pl.DataFrame([dict(r._mapping) for r in c.execute(text("""
                SELECT season, week, home_team_id AS team_id, kickoff_utc FROM nfl_games WHERE season = ANY(:s)
                UNION ALL SELECT season, week, away_team_id, kickoff_utc FROM nfl_games WHERE season = ANY(:s)"""),
                {"s": seasons})])
        prov = {"source_id": source_id, "ingestion_run_id": run_id, "source_updated_at": datetime.now(timezone.utc)}
        stats = {}
        # depth charts
        frames = []
        for s in [x for x in seasons if x >= DEPTH_FIRST]:
            d = nfl.load_depth_charts([s])
            frames.append(depth_snapshots(d, abbr, tg.filter(pl.col("season") == s)) if "dt" in d.columns else depth_weekly(d, abbr))
        depth = pl.concat([f for f in frames if not f.is_empty()]) if frames else pl.DataFrame()
        ng = ngs(nfl, seasons, abbr)
        ftn = ftn_team_games(nfl, seasons, gid, abbr)
        off = officials(nfl, seasons, old_to_game)
        with engine.begin() as c:
            stats["depth_charts"] = load.upsert(c, nm.NflDepthChart.__table__, depth, provenance=prov)
            stats["ngs"] = load.upsert(c, nm.NflNgsWeekly.__table__, ng, provenance=prov)
            stats["ftn_team_games"] = load.upsert(c, nm.NflFtnTeamGame.__table__, ftn, provenance=prov)
            stats["officials"] = load.upsert(c, nm.NflGameOfficial.__table__, off, provenance=prov)
        return {"seasons": [seasons[0], seasons[-1]], **stats}

    return tracked(engine, "nflverse_extras", "nfl_extras", {"seasons": seasons}, run, retries=2)

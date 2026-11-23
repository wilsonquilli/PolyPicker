"""nflverse adapter (via nflreadpy): schedules, team/player game stats, snap counts, rosters, injuries.

Normalization
* game_id = ESPN event id (nflverse ``espn`` column); nflverse's id kept as ``nflverse_id``.
* team_id = NFL franchise id, so relocated teams (OAK -> LV) stay one team; the abbreviation shown is
  the franchise's most recent one.
* kickoff: nflverse ``gameday`` + ``gametime`` are US-Eastern wall clock -> UTC.
* player game rows (offensive skill positions): stats rows + offensive snap counts (so a player who
  played but recorded nothing gets real zeros) + weekly-roster inactives (participation 'inactive',
  stats NULL -- never zeros).
* Only columns this source actually knows are emitted, so it never blanks values written by the
  ESPN live feed (quarter scores, in-progress status).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import polars as pl

from polypicker.etl.issues import IssueLog

log = logging.getLogger(__name__)

SKILL = ["QB", "RB", "FB", "WR", "TE"]
POSTSEASON = {"WC", "DIV", "CON", "SB"}


@dataclass
class NflData:
    teams: pl.DataFrame
    players: pl.DataFrame
    games: pl.DataFrame
    team_game_stats: pl.DataFrame
    player_game_stats: pl.DataFrame
    injuries: pl.DataFrame
    roster_weeks: pl.DataFrame


def _i(c: str) -> pl.Expr:
    return pl.col(c).cast(pl.Float64, strict=False).round(0).cast(pl.Int64)


def _sum(*cols: str) -> pl.Expr:
    return pl.sum_horizontal([pl.col(c).fill_null(0) for c in cols]).cast(pl.Int64)


class NflverseSource:
    code = "nflverse"

    def __init__(self, first_season: int, last_season: int | None = None, now: datetime | None = None,
                 loader=None):
        self.now = now or datetime.now(timezone.utc)
        self.first = first_season
        self.last = last_season or (self.now.year if self.now.month >= 8 else self.now.year - 1)
        self.seasons = list(range(self.first, self.last + 1))
        self.nfl = loader  # injectable for tests; defaults to nflreadpy
        self.fetched_at: datetime | None = None

    def _lib(self):
        if self.nfl is None:
            import nflreadpy
            self.nfl = nflreadpy
        return self.nfl

    def source_updated_at(self) -> datetime | None:
        return self.fetched_at

    # ------------------------------------------------------------------ extract
    def extract(self, issues: IssueLog) -> NflData:
        nfl = self._lib()
        sched = nfl.load_schedules(self.seasons)
        teams_raw = nfl.load_teams()
        self.fetched_at = datetime.now(timezone.utc)
        teams, abbr_to_id = self._teams(teams_raw, sched)
        games = self._games(sched, abbr_to_id, issues)
        gid = games.select("nflverse_id", "game_id", "season", "week", "game_type", "home_team_id", "away_team_id",
                           "home_score", "away_score", "status")
        tgs = self._team_stats(nfl.load_team_stats(self.seasons), gid, abbr_to_id, issues)
        rosters = nfl.load_rosters_weekly(self.seasons)
        players_meta = nfl.load_rosters(self.seasons)
        pgs = self._player_stats(nfl.load_player_stats(self.seasons), nfl.load_snap_counts(self.seasons),
                                 rosters, players_meta, gid, abbr_to_id, issues)
        injuries = self._injuries(nfl.load_injuries(self.seasons), abbr_to_id, issues)
        players = self._players(players_meta, pgs, injuries, issues)
        games = games.with_columns(pl.col("game_id").is_in(
            pgs.filter(pl.col("participation") == "played")["game_id"].unique().implode()).alias("has_player_stats"))
        roster_weeks = (rosters.filter(pl.col("position").is_in(SKILL) & pl.col("gsis_id").is_not_null()
                                       & pl.col("status").is_not_null())
                        .with_columns(pl.col("team").replace_strict(abbr_to_id, default=None).alias("team_id"))
                        .filter(pl.col("team_id").is_not_null())
                        .select(pl.col("season").cast(pl.Int64), pl.col("week").cast(pl.Int64), "team_id",
                                pl.col("gsis_id").alias("player_id"), pl.col("position"), pl.col("status").str.slice(0, 8))
                        .unique(["season", "week", "team_id", "player_id"], keep="last"))
        return NflData(teams, players, games, tgs, pgs, injuries, roster_weeks)

    # ------------------------------------------------------------------ teams
    def _teams(self, t: pl.DataFrame, sched: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, int]]:
        t = t.with_columns(pl.col("team_id").cast(pl.Int64))
        abbr_to_id = dict(zip(t["team_abbr"].to_list(), t["team_id"].to_list()))
        latest = sched.filter(pl.col("season") == sched["season"].max())
        current_abbrs = set(latest["home_team"].to_list()) | set(latest["away_team"].to_list())
        cur = t.filter(pl.col("team_abbr").is_in(list(current_abbrs)))
        out = cur.select(
            "team_id", pl.col("team_abbr").alias("abbreviation"), pl.col("team_nick").alias("name"),
            pl.col("team_name").alias("full_name"), pl.col("team_conf").alias("conference"),
            pl.col("team_division").alias("division"), pl.col("team_color").alias("color"),
            pl.col("team_color2").alias("color2"), pl.lit(True).alias("is_active"))
        return out.unique("team_id", keep="first"), abbr_to_id

    # ------------------------------------------------------------------ games
    def _games(self, s: pl.DataFrame, abbr_to_id: dict[str, int], issues: IssueLog) -> pl.DataFrame:
        missing = s.filter(pl.col("espn").is_null())
        for gid in missing["game_id"].to_list():
            issues.add("nfl_game", gid, "missing_espn_id", "error", "dropped")
        s = s.filter(pl.col("espn").is_not_null())
        kickoff = (pl.concat_str([pl.col("gameday"), pl.col("gametime").fill_null("13:00")], separator=" ")
                   .str.to_datetime("%Y-%m-%d %H:%M", strict=False)
                   .dt.replace_time_zone("America/New_York", ambiguous="earliest", non_existent="null")
                   .dt.convert_time_zone("UTC").cast(pl.Datetime("us", "UTC")))
        g = s.with_columns(
            pl.col("espn").cast(pl.Int64).alias("game_id"),
            pl.col("game_id").alias("nflverse_id"),
            kickoff.alias("kickoff_utc"),
            pl.col("gameday").str.to_date("%Y-%m-%d").alias("game_date_et"),
            pl.col("home_team").replace_strict(abbr_to_id, default=None).alias("home_team_id"),
            pl.col("away_team").replace_strict(abbr_to_id, default=None).alias("away_team_id"),
            _i("home_score").alias("home_score"), _i("away_score").alias("away_score"),
        )
        unknown = g.filter(pl.col("home_team_id").is_null() | pl.col("away_team_id").is_null())
        for gid in unknown["nflverse_id"].to_list():
            issues.add("nfl_game", gid, "unknown_team", "error", "dropped")
        g = g.filter(pl.col("home_team_id").is_not_null() & pl.col("away_team_id").is_not_null())
        now = pl.lit(self.now).cast(pl.Datetime("us", "UTC"))
        g = g.with_columns(
            pl.when(pl.col("home_score").is_not_null() & pl.col("away_score").is_not_null()).then(pl.lit("final"))
            .when(pl.col("kickoff_utc") > now).then(pl.lit("scheduled")).otherwise(pl.lit("unknown")).alias("status"),
            pl.when(pl.col("game_type") == "REG").then(pl.lit("regular")).otherwise(pl.lit("playoffs")).alias("season_type"),
        ).with_columns(
            pl.when(pl.col("status") != "final").then(None)
            .when(pl.col("home_score") > pl.col("away_score")).then(pl.col("home_team_id"))
            .when(pl.col("away_score") > pl.col("home_score")).then(pl.col("away_team_id"))
            .otherwise(None).alias("winner_team_id"),
            pl.when(pl.col("status") == "final").then(_i("overtime") == 1).otherwise(None).alias("overtime"),
            (pl.col("location") == "Neutral").alias("neutral_site"),
            (_i("div_game") == 1).fill_null(False).alias("div_game"),
            _i("home_rest").alias("home_rest"), _i("away_rest").alias("away_rest"),
            pl.col("spread_line").cast(pl.Float64).alias("market_spread_home"),
            pl.col("total_line").cast(pl.Float64).alias("market_total"),
        )
        dup = g.filter(pl.col("game_id").is_duplicated())
        for gid in dup["game_id"].unique().to_list():
            issues.add("nfl_game", gid, "duplicate_espn_id", "error", "dropped", note="kept first")
        g = g.unique("game_id", keep="first", maintain_order=True)
        return g.select("game_id", "nflverse_id", "season", pl.col("season_type"), "game_type",
                        pl.col("week").cast(pl.Int64), "kickoff_utc", "game_date_et", "home_team_id", "away_team_id",
                        "status", "home_score", "away_score", "winner_team_id", "overtime", "neutral_site",
                        "div_game", "roof", "surface", "stadium", "home_rest", "away_rest", "market_spread_home",
                        "market_total")

    # ------------------------------------------------------------------ team game stats
    def _team_stats(self, ts: pl.DataFrame, gid: pl.DataFrame, abbr_to_id: dict[str, int],
                    issues: IssueLog) -> pl.DataFrame:
        t = ts.join(gid.select("nflverse_id", "game_id", "home_team_id", "away_team_id", "home_score", "away_score",
                               "status"), left_on="game_id", right_on="nflverse_id", how="inner", suffix="_g")
        t = t.with_columns(pl.col("team").replace_strict(abbr_to_id, default=None).alias("team_id"),
                           pl.col("opponent_team").replace_strict(abbr_to_id, default=None).alias("opponent_team_id"))
        bad = t.filter(pl.col("team_id").is_null() | pl.col("opponent_team_id").is_null())
        for k in bad["game_id"].to_list():
            issues.add("nfl_team_game_stats", k, "unknown_team", "error", "dropped")
        t = t.filter(pl.col("team_id").is_not_null() & pl.col("opponent_team_id").is_not_null())
        is_home = pl.col("team_id") == pl.col("home_team_id")
        pts = pl.when(is_home).then("home_score").otherwise("away_score")
        opp = pl.when(is_home).then("away_score").otherwise("home_score")
        fl = _sum("sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost")
        return t.select(
            pl.col("game_id_g").alias("game_id"), "team_id", "opponent_team_id", is_home.alias("is_home"),
            pl.when(pl.col("status") != "final").then(None).when(pts > opp).then(True).when(pts < opp).then(False)
            .otherwise(None).alias("win"),
            pts.cast(pl.Int64).alias("points"),
            _sum("attempts", "sacks_suffered", "carries").alias("plays"),
            _i("attempts").alias("pass_attempts"), _i("completions").alias("completions"),
            _i("passing_yards").alias("passing_yards"), _i("passing_tds").alias("passing_tds"),
            _i("passing_interceptions").alias("interceptions"), _i("sacks_suffered").alias("sacks"),
            _i("carries").alias("carries"), _i("rushing_yards").alias("rushing_yards"),
            _i("rushing_tds").alias("rushing_tds"), fl.alias("fumbles_lost"),
            (fl + pl.col("passing_interceptions").fill_null(0).cast(pl.Int64)).alias("turnovers"),
            _sum("passing_first_downs", "rushing_first_downs").alias("first_downs"),
            _i("penalties").alias("penalties"), _i("penalty_yards").alias("penalty_yards"),
            pl.col("passing_epa").cast(pl.Float64), pl.col("rushing_epa").cast(pl.Float64),
        ).unique(["game_id", "team_id"], keep="first")

    # ------------------------------------------------------------------ player game stats
    def _player_stats(self, ps: pl.DataFrame, snaps: pl.DataFrame, rosters_w: pl.DataFrame,
                      rosters: pl.DataFrame, gid: pl.DataFrame, abbr_to_id: dict[str, int],
                      issues: IssueLog) -> pl.DataFrame:
        gmap = gid.select("nflverse_id", "game_id", "home_team_id", "away_team_id", "status")
        team = lambda c: pl.col(c).replace_strict(abbr_to_id, default=None)  # noqa: E731

        # 1) stat rows (skill positions or any offensive production)
        stat_cols = {"completions": "completions", "attempts": "pass_attempts", "passing_yards": "passing_yards",
                     "passing_tds": "passing_tds", "passing_interceptions": "interceptions",
                     "sacks_suffered": "sacks", "carries": "carries", "rushing_yards": "rushing_yards",
                     "rushing_tds": "rushing_tds", "targets": "targets", "receptions": "receptions",
                     "receiving_yards": "receiving_yards", "receiving_tds": "receiving_tds"}
        off = (pl.col("attempts").fill_null(0) + pl.col("carries").fill_null(0) + pl.col("targets").fill_null(0)) > 0
        s = (ps.filter(pl.col("position").is_in(SKILL) | off)
             .join(gmap, left_on="game_id", right_on="nflverse_id", how="inner", suffix="_g")
             .with_columns(team("team").alias("team_id"), team("opponent_team").alias("opponent_team_id")))
        s = s.select(
            pl.col("game_id_g").alias("game_id"), "player_id", "team_id", "opponent_team_id",
            (pl.col("team_id") == pl.col("home_team_id")).alias("is_home"), "position",
            *[_i(src).alias(dst) for src, dst in stat_cols.items()],
            _sum("sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost").alias("fumbles_lost"),
            pl.col("passing_epa").cast(pl.Float64), pl.col("rushing_epa").cast(pl.Float64),
            pl.col("receiving_epa").cast(pl.Float64),
        )

        # 2) offensive snaps (PFR ids -> GSIS via rosters)
        pfr = (rosters.filter(pl.col("pfr_id").is_not_null() & pl.col("gsis_id").is_not_null())
               .select("pfr_id", "gsis_id").unique("pfr_id"))
        sn = (snaps.filter(pl.col("position").is_in(SKILL) & (pl.col("offense_snaps").fill_null(0) > 0))
              .join(pfr, left_on="pfr_player_id", right_on="pfr_id", how="left"))
        unmapped = sn.filter(pl.col("gsis_id").is_null())
        if unmapped.height:
            issues.add("nfl_player_game_stats", None, "snap_player_unmapped", "info", "dropped", rows=unmapped.height)
        sn = (sn.filter(pl.col("gsis_id").is_not_null())
              .join(gmap, left_on="game_id", right_on="nflverse_id", how="inner", suffix="_g")
              .with_columns(team("team").alias("team_id"), team("opponent").alias("opponent_team_id"))
              .select(pl.col("game_id_g").alias("game_id"), pl.col("gsis_id").alias("player_id"),
                      pl.col("team_id").alias("s_team"), pl.col("opponent_team_id").alias("s_opp"),
                      (pl.col("team_id") == pl.col("home_team_id")).alias("s_home"),
                      pl.col("position").alias("s_pos"), _i("offense_snaps").alias("offense_snaps"),
                      (pl.col("offense_pct").cast(pl.Float64)).alias("offense_pct"))
              .unique(["game_id", "player_id"], keep="first"))

        played = s.join(sn, on=["game_id", "player_id"], how="full", coalesce=True).with_columns(
            pl.coalesce("team_id", "s_team").alias("team_id"), pl.coalesce("opponent_team_id", "s_opp").alias("opponent_team_id"),
            pl.coalesce("is_home", "s_home").alias("is_home"), pl.coalesce("position", "s_pos").alias("position"),
        ).drop("s_team", "s_opp", "s_home", "s_pos")
        zero = list(stat_cols.values()) + ["fumbles_lost"]
        played = played.with_columns([pl.col(c).fill_null(0) for c in zero]).with_columns(
            pl.lit("played").alias("participation"), pl.lit(None, dtype=pl.String).alias("status_comment"),
            (pl.col("rushing_tds") + pl.col("receiving_tds")).alias("scrimmage_tds"))

        # 3) weekly-roster inactives for that team's game
        tw = pl.concat([
            gid.select("game_id", "season", "week", "game_type", pl.col("home_team_id").alias("team_id"),
                       pl.col("away_team_id").alias("opponent_team_id"), pl.lit(True).alias("is_home")),
            gid.select("game_id", "season", "week", "game_type", pl.col("away_team_id").alias("team_id"),
                       pl.col("home_team_id").alias("opponent_team_id"), pl.lit(False).alias("is_home"))])
        ina = (rosters_w.filter((pl.col("status") == "INA") & pl.col("position").is_in(SKILL) & pl.col("gsis_id").is_not_null())
               .with_columns(team("team").alias("team_id"), pl.col("week").cast(pl.Int64))
               .join(tw, on=["season", "week", "team_id"], how="inner")
               .select("game_id", pl.col("gsis_id").alias("player_id"), "team_id", "opponent_team_id", "is_home",
                       "position", pl.lit("inactive").alias("participation"),
                       pl.lit("Inactive").alias("status_comment"))
               .unique(["game_id", "player_id"], keep="first"))
        ina = ina.join(played.select("game_id", "player_id"), on=["game_id", "player_id"], how="anti")
        out = pl.concat([played, ina], how="diagonal_relaxed")
        nulls = out.filter(pl.col("team_id").is_null() | pl.col("opponent_team_id").is_null() | pl.col("player_id").is_null())
        if nulls.height:
            issues.add("nfl_player_game_stats", None, "unknown_team_or_player", "error", "dropped", rows=nulls.height)
        return out.filter(pl.col("team_id").is_not_null() & pl.col("opponent_team_id").is_not_null()
                          & pl.col("player_id").is_not_null()).unique(["game_id", "player_id"], keep="first")

    # ------------------------------------------------------------------ injuries
    def _injuries(self, inj: pl.DataFrame, abbr_to_id: dict[str, int], issues: IssueLog) -> pl.DataFrame:
        i = inj.filter(pl.col("gsis_id").is_not_null()).with_columns(
            pl.col("team").replace_strict(abbr_to_id, default=None).alias("team_id"))
        i = i.filter(pl.col("team_id").is_not_null())
        return i.select(pl.col("season").cast(pl.Int64), pl.col("game_type"), pl.col("week").cast(pl.Int64), "team_id",
                        pl.col("gsis_id").alias("player_id"), pl.col("position").str.slice(0, 8),
                        pl.col("report_status").str.slice(0, 16), pl.col("practice_status").str.slice(0, 64),
                        pl.col("report_primary_injury").str.slice(0, 120).alias("injury")
                        ).unique(["season", "game_type", "week", "team_id", "player_id"], keep="last")

    # ------------------------------------------------------------------ players
    def _players(self, rosters: pl.DataFrame, pgs: pl.DataFrame, inj: pl.DataFrame, issues: IssueLog) -> pl.DataFrame:
        def height(c):
            v = pl.col(c).cast(pl.String)
            ft_in = v.str.extract_groups(r"^(\d)-(\d{1,2})$")
            return (pl.when(v.str.contains("-"))
                    .then(ft_in.struct.field("1").cast(pl.Int64) * 12 + ft_in.struct.field("2").cast(pl.Int64))
                    .otherwise(v.cast(pl.Float64, strict=False).round(0).cast(pl.Int64)))
        r = (rosters.filter(pl.col("gsis_id").is_not_null()).sort("season")
             .group_by("gsis_id").last()
             .select(pl.col("gsis_id").alias("player_id"), "full_name", "first_name", "last_name", "position",
                     pl.col("jersey_number").cast(pl.String).alias("jersey"),
                     pl.col("birth_date").cast(pl.String).str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False).alias("birth_date"),
                     height("height").alias("height_inches"), _i("weight").alias("weight_lbs"),
                     pl.col("college"), pl.col("espn_id").cast(pl.Int64, strict=False).alias("espn_id"),
                     pl.col("pfr_id"), pl.col("headshot_url")))
        needed = set(pgs["player_id"].to_list())
        have = set(r["player_id"].to_list())
        missing = sorted(needed - have)
        if missing:
            issues.add("nfl_player", None, "missing_from_rosters", "warning", "fixed", rows=len(missing),
                       note="created with id only; name filled when a roster lists them")
            r = pl.concat([r, pl.DataFrame({"player_id": missing})], how="diagonal_relaxed")
        return r

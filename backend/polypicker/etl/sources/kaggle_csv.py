"""Adapter for the Eoin Moore historical NBA dataset (CSV + Parquet files in DATA_DIR).

Normalization rules come from the data profiling done on 2026-11-20:
* game type / season derived from the game ID, not the inconsistent ``gameType`` text
* tipoff times are US-Eastern wall clock (the 2024-25 schedule mislabels them "+00:00")
* only NBA-vs-NBA games are kept (exhibitions vs. overseas clubs, All-Star teams are skipped)
* 0-0 "final" scores are recovered from play-by-play
* TeamStatistics rows with team_id 0 are dropped
* player team comes from the home/away flag (playerteamId is often null)
* minutes come as decimals, "mm:ss", negatives or blanks -> one float column
* DNP / inactive / unknown participation kept distinct from a zero-stat appearance
* placeholder values (draft -1/-22, birth 1900-01-01, school "-") become NULL
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from polypicker.domain.games import GameStatus
from polypicker.etl.issues import IssueLog
from polypicker.etl.sources.base import (GAME_COLUMNS, PLAYER_COLUMNS, PLAYER_GAME_COLUMNS,
                                         TEAM_COLUMNS, TEAM_GAME_COLUMNS, CanonicalData,
                                         SourceAdapter)

# Conference / division for the 30 current franchises (static league structure).
TEAM_META: dict[int, tuple[str, str, str]] = {
    1610612738: ("BOS", "East", "Atlantic"), 1610612751: ("BKN", "East", "Atlantic"),
    1610612752: ("NYK", "East", "Atlantic"), 1610612755: ("PHI", "East", "Atlantic"),
    1610612761: ("TOR", "East", "Atlantic"), 1610612741: ("CHI", "East", "Central"),
    1610612739: ("CLE", "East", "Central"), 1610612765: ("DET", "East", "Central"),
    1610612754: ("IND", "East", "Central"), 1610612749: ("MIL", "East", "Central"),
    1610612737: ("ATL", "East", "Southeast"), 1610612766: ("CHA", "East", "Southeast"),
    1610612748: ("MIA", "East", "Southeast"), 1610612753: ("ORL", "East", "Southeast"),
    1610612764: ("WAS", "East", "Southeast"), 1610612743: ("DEN", "West", "Northwest"),
    1610612750: ("MIN", "West", "Northwest"), 1610612760: ("OKC", "West", "Northwest"),
    1610612757: ("POR", "West", "Northwest"), 1610612762: ("UTA", "West", "Northwest"),
    1610612744: ("GSW", "West", "Pacific"), 1610612746: ("LAC", "West", "Pacific"),
    1610612747: ("LAL", "West", "Pacific"), 1610612756: ("PHX", "West", "Pacific"),
    1610612758: ("SAC", "West", "Pacific"), 1610612742: ("DAL", "West", "Southwest"),
    1610612745: ("HOU", "West", "Southwest"), 1610612763: ("MEM", "West", "Southwest"),
    1610612740: ("NOP", "West", "Southwest"), 1610612759: ("SAS", "West", "Southwest"),
}
NBA_TEAM_IDS = list(TEAM_META)

_SEASON_TYPES = {"1": "preseason", "2": "regular", "3": "all_star", "4": "playoffs",
                 "5": "play_in", "6": "cup_final"}


# ----------------------------------------------------------------------------- helpers

def _int(c: str) -> pl.Expr:
    return pl.col(c).cast(pl.Float64, strict=False).round(0).cast(pl.Int64)


def _float(c: str) -> pl.Expr:
    return pl.col(c).cast(pl.Float64, strict=False)


def _str(c: str) -> pl.Expr:
    s = pl.col(c).str.strip_chars()
    return pl.when(s == "").then(None).otherwise(s)


def _gid_season() -> pl.Expr:
    yy = pl.col("game_id").cast(pl.String).str.zfill(8).str.slice(1, 2).cast(pl.Int64)
    return pl.when(yy >= 46).then(1900 + yy).otherwise(2000 + yy)


def _gid_type() -> pl.Expr:
    return (pl.col("game_id").cast(pl.String).str.zfill(8).str.slice(0, 1)
            .replace_strict(_SEASON_TYPES, default=None))


def _et_to_utc(c: str) -> pl.Expr:
    """'YYYY-MM-DD HH:MM:SS[+..]' US-Eastern wall clock -> UTC timestamp."""
    return (pl.col(c).str.slice(0, 19).str.to_datetime("%Y-%m-%d %H:%M:%S", strict=False)
            .dt.replace_time_zone("America/New_York", ambiguous="earliest", non_existent="null")
            .dt.convert_time_zone("UTC").cast(pl.Datetime("us", "UTC")))


def _et_date(c: str) -> pl.Expr:
    return pl.col(c).str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False)


def parse_minutes(col: str = "numMinutes") -> pl.Expr:
    """Decimal minutes ('34.5'), 'mm:ss' ('34:30') or junk -> float minutes; negatives -> null."""
    raw = pl.col(col).str.strip_chars()
    mmss = raw.str.extract_groups(r"^(\d+):(\d{1,2})$")
    from_mmss = (mmss.struct.field("1").cast(pl.Float64)
                 + mmss.struct.field("2").cast(pl.Float64) / 60.0)
    val = pl.when(raw.str.contains(":")).then(from_mmss).otherwise(raw.cast(pl.Float64, strict=False))
    return pl.when(val < 0).then(None).otherwise(val)


def classify_participation(comment: pl.Expr, minutes: pl.Expr, any_stat: pl.Expr) -> pl.Expr:
    """played | dnp | inactive | unknown (NFR-08.2)."""
    c = comment.str.to_uppercase()
    return (pl.when(minutes.fill_null(0) > 0).then(pl.lit("played"))
            .when(c.str.starts_with("DNP")).then(pl.lit("dnp"))
            .when(c.str.starts_with("DND") | c.str.starts_with("NWT") | c.str.starts_with("INACTIVE"))
            .then(pl.lit("inactive"))
            .when(any_stat).then(pl.lit("played"))
            .when(minutes == 0).then(pl.lit("dnp"))
            .otherwise(pl.lit("unknown")))


def _conform(df: pl.DataFrame, schema: dict) -> pl.DataFrame:
    return df.select([pl.col(c).cast(t) if c in df.columns else pl.lit(None, dtype=t).alias(c)
                      for c, t in schema.items()])


# ----------------------------------------------------------------------------- adapter

class KaggleCsvSource(SourceAdapter):
    code = "kaggle_eoin_moore"

    def __init__(self, data_dir: Path, now: datetime | None = None):
        self.dir = Path(data_dir)
        self.now = now or datetime.now(timezone.utc)

    def _p(self, name: str) -> Path:
        p = self.dir / name
        if not p.exists():
            raise FileNotFoundError(f"Expected source file missing: {p}")
        return p

    def _csv(self, name: str) -> pl.LazyFrame:
        return pl.scan_csv(self._p(name), infer_schema=False)

    def source_updated_at(self) -> datetime | None:
        files = [f for f in self.dir.glob("*") if f.suffix in (".csv", ".parquet")]
        if not files:
            return None
        return datetime.fromtimestamp(max(f.stat().st_mtime for f in files), tz=timezone.utc)

    # ------------------------------------------------------------------ extract
    def extract(self, first_season: int, issues: IssueLog) -> CanonicalData:
        teams = self._teams()
        games_results = self._game_results(first_season, issues)
        tgs = self._team_game_stats(games_results, issues)
        games_results = self._fix_zero_scores(games_results, issues)
        tgs = self._apply_score_fixes(tgs, games_results)
        schedule = self._schedule(first_season, issues)
        games = self._merge_games(games_results, schedule, tgs)
        pgs = self._player_game_stats(games, issues)
        games = games.with_columns(
            pl.col("game_id").is_in(pgs.filter(pl.col("participation") == "played")["game_id"].unique().implode())
            .alias("has_player_stats"))
        players = self._players(pgs, issues)
        return CanonicalData(
            teams=_conform(teams, TEAM_COLUMNS),
            players=_conform(players, PLAYER_COLUMNS),
            games=_conform(games, GAME_COLUMNS),
            team_game_stats=_conform(tgs, TEAM_GAME_COLUMNS),
            player_game_stats=_conform(pgs, PLAYER_GAME_COLUMNS),
        )

    # ------------------------------------------------------------------ teams
    def _teams(self) -> pl.DataFrame:
        th = (self._csv("TeamHistories.csv")
              .with_columns(_int("teamId").alias("team_id"), _int("seasonFounded").alias("founded"))
              .filter(pl.col("team_id").is_in(NBA_TEAM_IDS))
              .sort("founded").group_by("team_id").last().collect())
        meta = pl.DataFrame(
            {"team_id": list(TEAM_META), "abbreviation": [v[0] for v in TEAM_META.values()],
             "conference": [v[1] for v in TEAM_META.values()],
             "division": [v[2] for v in TEAM_META.values()]},
            schema={"team_id": pl.Int64, "abbreviation": pl.String, "conference": pl.String,
                    "division": pl.String})
        return (th.select("team_id", _str("teamCity").alias("city"), _str("teamName").alias("name"))
                .join(meta, on="team_id", how="right")
                .with_columns((pl.col("city") + " " + pl.col("name")).alias("full_name"),
                              pl.lit(True).alias("is_active")))

    # ------------------------------------------------------------------ games (results)
    def _game_results(self, first_season: int, issues: IssueLog) -> pl.DataFrame:
        g = (self._csv("Games.csv")
             .with_columns(_int("gameId").alias("game_id"))
             .with_columns(_gid_season().alias("season"), _gid_type().alias("season_type"))
             .filter(pl.col("season") >= first_season)
             .collect())
        g = g.with_columns(
            _int("hometeamId").alias("home_team_id"), _int("awayteamId").alias("away_team_id"),
            _int("homeScore").alias("home_score"), _int("awayScore").alias("away_score"),
            _int("winner").alias("winner_team_id"),
            _et_to_utc("gameDateTimeEst").alias("tipoff_utc"),
            _et_date("gameDateTimeEst").alias("game_date_et"),
            _str("gameLabel").alias("game_label"), _str("gameSubLabel").alias("game_sub_label"),
            pl.col("seriesGameNumber").str.extract(r"(\d+)").cast(pl.Int64).alias("series_game_number"),
            _str("arenaName").alias("arena_name"), _str("arenaCity").alias("arena_city"),
            _str("arenaState").alias("arena_state"), _int("attendance").alias("attendance"),
            (pl.concat_str([pl.col("gameType").fill_null(""), pl.col("gameLabel").fill_null(""),
                            pl.col("gameSubtype").fill_null("")], separator=" ")
             .str.to_lowercase().str.contains("cup|in-season")).alias("is_cup"),
        ).with_columns(
            pl.when(pl.col("series_game_number") == 0).then(None)
            .otherwise(pl.col("series_game_number")).alias("series_game_number"))
        non_nba = g.filter(~(pl.col("home_team_id").is_in(NBA_TEAM_IDS)
                             & pl.col("away_team_id").is_in(NBA_TEAM_IDS)))
        for row in non_nba.select("game_id", "season_type", "hometeamName", "awayteamName").iter_rows():
            issues.add("game", row[0], "non_nba_participant", "info", "dropped",
                       season_type=row[1], teams=f"{row[2]} vs {row[3]}")
        g = g.filter(pl.col("home_team_id").is_in(NBA_TEAM_IDS) & pl.col("away_team_id").is_in(NBA_TEAM_IDS))
        return g.with_columns(pl.lit(GameStatus.FINAL.value).alias("status"))

    def _fix_zero_scores(self, g: pl.DataFrame, issues: IssueLog) -> pl.DataFrame:
        """A 'final' 0-0 score is a missing result; recover it from play-by-play if possible."""
        zero = g.filter((pl.col("home_score") == 0) & (pl.col("away_score") == 0))["game_id"].to_list()
        if not zero:
            return g
        recovered: dict[int, tuple[int, int]] = {}
        pbp_path = self.dir / "PlayByPlay.parquet"
        if pbp_path.exists():
            pbp = (pl.scan_parquet(pbp_path)
                   .filter(pl.col("gameId").is_in([str(z) for z in zero]))
                   .group_by("gameId").agg(pl.col("scoreHome").max(), pl.col("scoreAway").max())
                   .collect())
            recovered = {int(r[0]): (int(r[1]), int(r[2])) for r in pbp.iter_rows()
                         if r[1] is not None and r[2] is not None and (r[1] or r[2])}
        rows = []
        for gid in zero:
            if gid in recovered:
                h, a = recovered[gid]
                issues.add("game", gid, "zero_zero_final", "warning", "fixed",
                           recovered_from="play_by_play", home=h, away=a)
                rows.append((gid, h, a))
            else:
                issues.add("game", gid, "zero_zero_final", "error", "flagged",
                           note="no play-by-play to recover from; status set to unknown")
        fix = pl.DataFrame(rows, schema={"game_id": pl.Int64, "h": pl.Int64, "a": pl.Int64}, orient="row")
        g = g.join(fix, on="game_id", how="left").with_columns(
            pl.coalesce("h", "home_score").alias("home_score"),
            pl.coalesce("a", "away_score").alias("away_score"),
        ).drop("h", "a")
        unrecovered = [z for z in zero if z not in recovered]
        return g.with_columns(
            pl.when(pl.col("game_id").is_in(unrecovered)).then(pl.lit(GameStatus.UNKNOWN.value))
            .otherwise(pl.col("status")).alias("status"),
            pl.when(pl.col("game_id").is_in(unrecovered)).then(None)
            .when(pl.col("home_score") > pl.col("away_score")).then(pl.col("home_team_id"))
            .otherwise(pl.col("away_team_id")).alias("winner_team_id"),
        ).with_columns(
            pl.when(pl.col("game_id").is_in(unrecovered)).then(None).otherwise(pl.col("home_score")).alias("home_score"),
            pl.when(pl.col("game_id").is_in(unrecovered)).then(None).otherwise(pl.col("away_score")).alias("away_score"),
        )

    # ------------------------------------------------------------------ team box scores
    def _team_game_stats(self, games: pl.DataFrame, issues: IssueLog) -> pl.DataFrame:
        ids = games["game_id"].implode()
        ts = (self._csv("TeamStatistics.csv")
              .with_columns(_int("gameId").alias("game_id"), _int("teamId").alias("team_id"))
              .filter(pl.col("game_id").is_in(ids))
              .collect())
        bad = ts.filter(~pl.col("team_id").is_in(NBA_TEAM_IDS))
        for gid, tid in bad.select("game_id", "team_id").iter_rows():
            issues.add("team_game_stats", f"{gid}:{tid}", "invalid_team_id", "error", "dropped")
        ts = ts.filter(pl.col("team_id").is_in(NBA_TEAM_IDS))
        out = ts.select(
            "game_id", "team_id", _int("opponentTeamId").alias("opponent_team_id"),
            (_int("home") == 1).alias("is_home"), (_int("win") == 1).alias("win"),
            _int("teamScore").alias("points"), _float("numMinutes").alias("minutes"),
            _int("fieldGoalsMade").alias("fgm"), _int("fieldGoalsAttempted").alias("fga"),
            _int("threePointersMade").alias("fg3m"), _int("threePointersAttempted").alias("fg3a"),
            _int("freeThrowsMade").alias("ftm"), _int("freeThrowsAttempted").alias("fta"),
            _int("reboundsOffensive").alias("oreb"), _int("reboundsDefensive").alias("dreb"),
            _int("reboundsTotal").alias("reb"), _int("assists").alias("ast"),
            _int("steals").alias("stl"), _int("blocks").alias("blk"),
            _int("turnovers").alias("tov"), _int("foulsPersonal").alias("pf"),
            _int("plusMinusPoints").alias("plus_minus"),
            _int("q1Points").alias("q1_points"), _int("q2Points").alias("q2_points"),
            _int("q3Points").alias("q3_points"), _int("q4Points").alias("q4_points"),
            pl.when(_int("q1Points").is_not_null()).then(_int("otAllPoints").fill_null(0))
            .alias("ot_points"),
        )
        return out

    def _apply_score_fixes(self, tgs: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
        """Keep team points in line with the (possibly recovered) game score."""
        sc = games.select("game_id", "home_score", "away_score")
        return (tgs.join(sc, on="game_id", how="left")
                .with_columns(
                    pl.when(pl.col("points") == 0)
                    .then(pl.when(pl.col("is_home")).then("home_score").otherwise("away_score"))
                    .otherwise(pl.col("points")).alias("points"))
                .with_columns(
                    pl.when(pl.col("is_home")).then(pl.col("home_score") > pl.col("away_score"))
                    .otherwise(pl.col("away_score") > pl.col("home_score")).alias("win"))
                .drop("home_score", "away_score"))

    # ------------------------------------------------------------------ schedules
    def _schedule(self, first_season: int, issues: IssueLog) -> pl.DataFrame:
        frames = []
        for f in sorted(self.dir.glob("LeagueSchedule*.csv")):
            lf = pl.scan_csv(f, infer_schema=False)
            cols = lf.collect_schema().names()
            home = "homeTeamId" if "homeTeamId" in cols else "hometeamId"
            away = "awayTeamId" if "awayTeamId" in cols else "awayteamId"
            frames.append(lf.select(
                _int("gameId").alias("game_id"), _int(home).alias("home_team_id"),
                _int(away).alias("away_team_id"),
                _et_to_utc("gameDateTimeEst").alias("tipoff_utc"),
                _et_date("gameDateTimeEst").alias("game_date_et"),
                _str("arenaName").alias("arena_name"), _str("arenaCity").alias("arena_city"),
                _str("arenaState").alias("arena_state"),
                _str("gameLabel").alias("game_label"), _str("gameSubLabel").alias("game_sub_label"),
                (pl.concat_str([pl.col("gameLabel").fill_null(""), pl.col("gameSubtype").fill_null("")],
                               separator=" ").str.to_lowercase().str.contains("cup|in-season")).alias("is_cup"),
                pl.col("seriesGameNumber").cast(pl.String).str.extract(r"(\d+)").cast(pl.Int64)
                .alias("series_game_number"),
            ))
        s = (pl.concat(frames).collect()
             .with_columns(_gid_season().alias("season"), _gid_type().alias("season_type"))
             .filter(pl.col("season") >= first_season)
             .unique("game_id", keep="last"))
        tbd = s.filter((pl.col("home_team_id") == 0) | (pl.col("away_team_id") == 0))
        for gid, lbl in tbd.select("game_id", "game_sub_label").iter_rows():
            issues.add("schedule", gid, "teams_tbd", "info", "dropped", label=lbl)
        s = s.filter(pl.col("home_team_id") != 0, pl.col("away_team_id") != 0)
        non_nba = s.filter(~(pl.col("home_team_id").is_in(NBA_TEAM_IDS)
                             & pl.col("away_team_id").is_in(NBA_TEAM_IDS)))
        for (gid,) in non_nba.select("game_id").iter_rows():
            issues.add("schedule", gid, "non_nba_participant", "info", "dropped")
        return s.filter(pl.col("home_team_id").is_in(NBA_TEAM_IDS) & pl.col("away_team_id").is_in(NBA_TEAM_IDS))

    def _merge_games(self, results: pl.DataFrame, schedule: pl.DataFrame, tgs: pl.DataFrame) -> pl.DataFrame:
        arena = schedule.select("game_id", pl.col("arena_name").alias("s_an"),
                                pl.col("arena_city").alias("s_ac"), pl.col("arena_state").alias("s_as"))
        periods = (tgs.filter(pl.col("is_home"))
                   .select("game_id", (4 + ((pl.col("minutes") - 240) / 25).round(0).clip(0, 10))
                           .cast(pl.Int64).alias("periods")))
        res = (results.join(arena, on="game_id", how="left")
               .with_columns(pl.coalesce("arena_name", "s_an").alias("arena_name"),
                             pl.coalesce("arena_city", "s_ac").alias("arena_city"),
                             pl.coalesce("arena_state", "s_as").alias("arena_state"))
               .join(periods, on="game_id", how="left"))
        upcoming = schedule.filter(~pl.col("game_id").is_in(results["game_id"].implode()))
        now = self.now
        upcoming = upcoming.with_columns(
            pl.when(pl.col("tipoff_utc") > pl.lit(now).cast(pl.Datetime("us", "UTC")))
            .then(pl.lit(GameStatus.SCHEDULED.value)).otherwise(pl.lit(GameStatus.UNKNOWN.value))
            .alias("status"))
        cols = list(GAME_COLUMNS)
        return pl.concat([_conform(res, GAME_COLUMNS), _conform(upcoming, GAME_COLUMNS)]).select(cols)

    # ------------------------------------------------------------------ player box scores
    def _player_game_stats(self, games: pl.DataFrame, issues: IssueLog) -> pl.DataFrame:
        g = games.filter(pl.col("status") == GameStatus.FINAL.value).select(
            "game_id", "home_team_id", "away_team_id")
        ps = (self._csv("PlayerStatistics.csv")
              .with_columns(_int("gameId").alias("game_id"))
              .filter(pl.col("game_id").is_in(g["game_id"].implode()))
              .collect())
        bad_min = ps.filter(_float("numMinutes") < 0)
        for gid, pid, m in bad_min.select("game_id", "personId", "numMinutes").iter_rows():
            issues.add("player_game_stats", f"{gid}:{pid}", "negative_minutes", "warning", "fixed",
                       raw=m, note="set to NULL")
        stat_cols = {"points": "points", "assists": "assists", "reboundsTotal": "rebounds",
                     "reboundsOffensive": "oreb", "reboundsDefensive": "dreb",
                     "fieldGoalsMade": "fgm", "fieldGoalsAttempted": "fga",
                     "threePointersMade": "fg3m", "threePointersAttempted": "fg3a",
                     "freeThrowsMade": "ftm", "freeThrowsAttempted": "fta", "steals": "steals",
                     "blocks": "blocks", "turnovers": "turnovers", "foulsPersonal": "fouls",
                     "plusMinusPoints": "plus_minus"}
        out = (ps.join(g, on="game_id", how="inner")
               .with_columns(_int("personId").alias("player_id"), (_int("home") == 1).alias("is_home"),
                             parse_minutes().alias("minutes"), _str("comment").alias("status_comment"),
                             _str("startingPosition").alias("starting_position"),
                             *[_int(src).alias(dst) for src, dst in stat_cols.items()]))
        any_stat = pl.any_horizontal([pl.col(c).fill_null(0) != 0 for c in
                                      ("points", "assists", "rebounds", "fga", "fta", "turnovers", "fouls",
                                       "steals", "blocks")])
        out = out.with_columns(
            pl.when(pl.col("is_home")).then("home_team_id").otherwise("away_team_id").alias("team_id"),
            pl.when(pl.col("is_home")).then("away_team_id").otherwise("home_team_id").alias("opponent_team_id"),
            classify_participation(pl.col("status_comment").fill_null(""), pl.col("minutes"), any_stat)
            .alias("participation"),
        ).with_columns(
            pl.when(pl.col("participation") == "played").then(pl.col("starting_position").is_not_null())
            .otherwise(False).alias("starter"),
        )
        # Disagreement between the source's own team id and the home flag -> trust home flag, log it.
        mismatch = out.filter(_int("playerteamId").is_not_null() & (_int("playerteamId") != pl.col("team_id")))
        for gid, pid in mismatch.select("game_id", "player_id").iter_rows():
            issues.add("player_game_stats", f"{gid}:{pid}", "team_id_mismatch", "warning", "fixed")
        missing_min = out.filter((pl.col("participation") == "played") & pl.col("minutes").is_null())
        if missing_min.height:
            for gid, n in missing_min.group_by("game_id").len().iter_rows():
                issues.add("player_game_stats", gid, "missing_minutes", "warning", "flagged", players=n)
        # Non-appearances carry NULL stats, never zeros (NFR-08.2).
        stat_names = list(stat_cols.values())
        out = out.with_columns([
            pl.when(pl.col("participation") == "played").then(pl.col(c)).otherwise(None).alias(c)
            for c in stat_names + ["minutes"]])
        dup = out.filter(pl.struct("game_id", "player_id").is_duplicated())
        if dup.height:
            for gid, pid in dup.select("game_id", "player_id").unique().iter_rows():
                issues.add("player_game_stats", f"{gid}:{pid}", "duplicate_row", "warning", "dropped")
            out = out.unique(["game_id", "player_id"], keep="first")
        names = out.select("player_id", _str("firstName").alias("first_name"),
                           _str("lastName").alias("last_name")).unique("player_id")
        self._ps_names = names
        return out

    # ------------------------------------------------------------------ players
    def _players(self, pgs: pl.DataFrame, issues: IssueLog) -> pl.DataFrame:
        ids = pgs["player_id"].unique()
        p = (self._csv("Players.csv").with_columns(_int("personId").alias("player_id"))
             .filter(pl.col("player_id").is_in(ids.implode())).collect())

        def nonneg(c):
            v = _int(c)
            return pl.when(v < 0).then(None).otherwise(v)

        pos = pl.concat_str([pl.when(_int("guard") == 1).then(pl.lit("G")),
                             pl.when(_int("forward") == 1).then(pl.lit("F")),
                             pl.when(_int("center") == 1).then(pl.lit("C"))], separator="-",
                            ignore_nulls=True)
        out = p.select(
            "player_id", _str("firstName").alias("first_name"), _str("lastName").alias("last_name"),
            pl.when(_str("birthDate") == "1900-01-01").then(None)
            .otherwise(_str("birthDate").str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False)).alias("birth_date"),
            _int("heightInches").alias("height_inches"), _int("bodyWeightLbs").alias("weight_lbs"),
            pl.when(pos == "").then(None).otherwise(pos).alias("position"),
            _str("jersey").alias("jersey"), _str("country").alias("country"),
            pl.when(_str("school") == "-").then(None).otherwise(_str("school")).alias("school"),
            nonneg("draftYear").alias("draft_year"), nonneg("draftRound").alias("draft_round"),
            nonneg("draftNumber").alias("draft_number"),
        )
        sentinel = p.filter((_int("draftYear") < 0) | (_int("draftRound") < 0) | (_str("birthDate") == "1900-01-01"))
        if sentinel.height:
            issues.add("player", None, "placeholder_values", "info", "fixed", rows=sentinel.height)
        missing = self._ps_names.filter(~pl.col("player_id").is_in(out["player_id"].implode()))
        for pid, fn, ln in missing.iter_rows():
            issues.add("player", pid, "missing_from_players_file", "warning", "fixed",
                       note="created from box-score name")
        out = pl.concat([out, missing], how="diagonal_relaxed")
        return out.with_columns(
            pl.concat_str([pl.col("first_name"), pl.col("last_name")], separator=" ", ignore_nulls=True)
            .alias("full_name"))

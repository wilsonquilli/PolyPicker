"""Source-independent validation of canonical frames (FR-10.2, spec §6.3).

* schema   : columns and dtypes match the canonical contract (a mismatch is a bug -> raise)
* keys     : primary keys present and unique
* ranges   : non-negative counts, made <= attempted, plausible minutes / scores
* identity : points = 2*FGM + 3PM + FTM, rebounds = OREB + DREB
* refs     : foreign keys resolve

Error-level violations drop the row; warnings keep it and log an issue.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from polypicker.etl.issues import IssueLog
from polypicker.etl.sources.base import (GAME_COLUMNS, PLAYER_COLUMNS, PLAYER_GAME_COLUMNS,
                                         TEAM_COLUMNS, TEAM_GAME_COLUMNS, CanonicalData)


class SchemaError(Exception):
    pass


@dataclass(frozen=True)
class Rule:
    name: str
    severity: str  # error | warning
    bad: pl.Expr   # True for violating rows


def _nonneg(cols: list[str]) -> pl.Expr:
    return pl.any_horizontal([pl.col(c) < 0 for c in cols])


def _made_gt_att(prefix_pairs: list[tuple[str, str]]) -> pl.Expr:
    return pl.any_horizontal([pl.col(m) > pl.col(a) for m, a in prefix_pairs])


TEAM_COUNT_COLS = ["points", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb", "ast",
                   "stl", "blk", "tov", "pf"]
PLAYER_COUNT_COLS = ["points", "assists", "rebounds", "oreb", "dreb", "fgm", "fga", "fg3m", "fg3a",
                     "ftm", "fta", "steals", "blocks", "turnovers", "fouls"]

GAME_RULES = [
    Rule("same_home_away", "error", pl.col("home_team_id") == pl.col("away_team_id")),
    Rule("missing_tipoff", "error", pl.col("tipoff_utc").is_null()),
    Rule("final_without_score", "error",
         (pl.col("status") == "final") & (pl.col("home_score").is_null() | pl.col("away_score").is_null())),
    Rule("score_out_of_range", "error",
         (pl.col("home_score") < 0) | (pl.col("home_score") > 200) | (pl.col("away_score") < 0)
         | (pl.col("away_score") > 200)),
    Rule("final_tie", "warning", (pl.col("status") == "final") & (pl.col("home_score") == pl.col("away_score"))),
    Rule("winner_inconsistent", "warning",
         (pl.col("status") == "final") & (
             ((pl.col("home_score") > pl.col("away_score")) & (pl.col("winner_team_id") != pl.col("home_team_id")))
             | ((pl.col("away_score") > pl.col("home_score")) & (pl.col("winner_team_id") != pl.col("away_team_id"))))),
]

TEAM_GAME_RULES = [
    Rule("negative_count", "error", _nonneg(TEAM_COUNT_COLS)),
    Rule("made_gt_attempted", "error", _made_gt_att([("fgm", "fga"), ("fg3m", "fg3a"), ("ftm", "fta"), ("fg3m", "fgm")])),
    Rule("points_identity", "warning",
         pl.col("points") != 2 * pl.col("fgm") + pl.col("fg3m") + pl.col("ftm")),
    Rule("rebound_identity", "warning", pl.col("reb") != pl.col("oreb") + pl.col("dreb")),
    Rule("quarter_sum", "warning",
         pl.col("q1_points").is_not_null()
         & (pl.col("points") != pl.col("q1_points") + pl.col("q2_points") + pl.col("q3_points")
            + pl.col("q4_points") + pl.col("ot_points").fill_null(0))),
]

PLAYER_GAME_RULES = [
    Rule("negative_count", "error", _nonneg(PLAYER_COUNT_COLS)),
    Rule("made_gt_attempted", "error", _made_gt_att([("fgm", "fga"), ("fg3m", "fg3a"), ("ftm", "fta"), ("fg3m", "fgm")])),
    Rule("minutes_out_of_range", "error", (pl.col("minutes") < 0) | (pl.col("minutes") > 70)),
    Rule("invalid_participation", "error",
         ~pl.col("participation").is_in(["played", "dnp", "inactive", "unknown"])),
    Rule("points_identity", "warning",
         pl.col("points") != 2 * pl.col("fgm") + pl.col("fg3m") + pl.col("ftm")),
    Rule("rebound_identity", "warning", pl.col("rebounds") != pl.col("oreb") + pl.col("dreb")),
    Rule("stats_on_non_appearance", "warning",
         (pl.col("participation") != "played") & pl.col("points").is_not_null()),
]


def check_schema(name: str, df: pl.DataFrame, contract: dict) -> None:
    missing = [c for c in contract if c not in df.columns]
    if missing:
        raise SchemaError(f"{name}: missing columns {missing}")
    wrong = {c: (str(df.schema[c]), str(t)) for c, t in contract.items() if df.schema[c] != t}
    if wrong:
        raise SchemaError(f"{name}: wrong dtypes {wrong}")


def _key_str(df: pl.DataFrame, keys: list[str]) -> pl.Series:
    return df.select(pl.concat_str([pl.col(k).cast(pl.String) for k in keys], separator=":")).to_series()


def apply_rules(name: str, df: pl.DataFrame, keys: list[str], rules: list[Rule], issues: IssueLog,
                max_logged_per_rule: int = 500) -> pl.DataFrame:
    # Keys
    null_key = df.filter(pl.any_horizontal([pl.col(k).is_null() for k in keys]))
    if null_key.height:
        issues.add(name, None, "null_key", "error", "dropped", rows=null_key.height)
        df = df.filter(pl.all_horizontal([pl.col(k).is_not_null() for k in keys]))
    dup = df.filter(pl.struct(keys).is_duplicated())
    if dup.height:
        for k in _key_str(dup, keys).unique().to_list()[:max_logged_per_rule]:
            issues.add(name, k, "duplicate_key", "error", "dropped", note="kept first occurrence")
        df = df.unique(keys, keep="first", maintain_order=True)

    drop_mask = pl.lit(False)
    for rule in rules:
        flagged = df.filter(rule.bad.fill_null(False))
        if not flagged.height:
            continue
        action = "dropped" if rule.severity == "error" else "flagged"
        ks = _key_str(flagged, keys).to_list()
        for k in ks[:max_logged_per_rule]:
            issues.add(name, k, rule.name, rule.severity, action)
        if len(ks) > max_logged_per_rule:
            issues.add(name, None, rule.name, rule.severity, action,
                       note=f"{len(ks) - max_logged_per_rule} more rows not logged individually")
        if rule.severity == "error":
            drop_mask = drop_mask | rule.bad.fill_null(False)
    return df.filter(~drop_mask)


def _refs(name: str, df: pl.DataFrame, col: str, valid: pl.Series, issues: IssueLog, keys: list[str]) -> pl.DataFrame:
    bad = df.filter(pl.col(col).is_not_null() & ~pl.col(col).is_in(valid.implode()))
    if bad.height:
        for k in _key_str(bad, keys).to_list()[:500]:
            issues.add(name, k, f"unknown_{col}", "error", "dropped")
        df = df.filter(~(pl.col(col).is_not_null() & ~pl.col(col).is_in(valid.implode())))
    return df


def validate(data: CanonicalData, issues: IssueLog) -> CanonicalData:
    for name, df, contract in [("teams", data.teams, TEAM_COLUMNS), ("players", data.players, PLAYER_COLUMNS),
                               ("games", data.games, GAME_COLUMNS),
                               ("team_game_stats", data.team_game_stats, TEAM_GAME_COLUMNS),
                               ("player_game_stats", data.player_game_stats, PLAYER_GAME_COLUMNS)]:
        check_schema(name, df, contract)

    teams = apply_rules("teams", data.teams, ["team_id"], [], issues)
    players = apply_rules("players", data.players, ["player_id"], [], issues)
    games = apply_rules("games", data.games, ["game_id"], GAME_RULES, issues)
    for c in ("home_team_id", "away_team_id"):
        games = _refs("games", games, c, teams["team_id"], issues, ["game_id"])

    tgs = apply_rules("team_game_stats", data.team_game_stats, ["game_id", "team_id"], TEAM_GAME_RULES, issues)
    tgs = _refs("team_game_stats", tgs, "game_id", games["game_id"], issues, ["game_id", "team_id"])
    # Team points must match the game score.
    chk = tgs.join(games.select("game_id", "home_score", "away_score"), on="game_id").filter(
        pl.when(pl.col("is_home")).then(pl.col("points") != pl.col("home_score"))
        .otherwise(pl.col("points") != pl.col("away_score")))
    for gid, tid in chk.select("game_id", "team_id").iter_rows():
        issues.add("team_game_stats", f"{gid}:{tid}", "points_vs_game_score", "warning", "flagged")
    per_game = tgs.group_by("game_id").len().filter(pl.col("len") != 2)
    for gid, n in per_game.iter_rows():
        issues.add("team_game_stats", gid, "not_two_teams", "warning", "flagged", rows=n)

    pgs = apply_rules("player_game_stats", data.player_game_stats, ["game_id", "player_id"],
                      PLAYER_GAME_RULES, issues)
    pgs = _refs("player_game_stats", pgs, "game_id", games["game_id"], issues, ["game_id", "player_id"])
    pgs = _refs("player_game_stats", pgs, "player_id", players["player_id"], issues, ["game_id", "player_id"])
    # Sum of player points must equal team points for fully-recorded games.
    sums = (pgs.group_by("game_id", "team_id").agg(pl.col("points").sum().alias("p"))
            .join(tgs.select("game_id", "team_id", "points"), on=["game_id", "team_id"])
            .filter(pl.col("p") != pl.col("points")))
    for gid, tid, *_ in sums.iter_rows():
        issues.add("player_game_stats", f"{gid}:{tid}", "player_points_vs_team", "warning", "flagged")

    return CanonicalData(teams=teams, players=players, games=games, team_game_stats=tgs,
                         player_game_stats=pgs)

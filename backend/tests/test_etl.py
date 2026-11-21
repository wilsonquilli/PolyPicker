import polars as pl
import pytest

from polypicker.etl.issues import IssueLog
from polypicker.etl.sources.base import PLAYER_GAME_COLUMNS
from polypicker.etl.sources.kaggle_csv import classify_participation, parse_minutes
from polypicker.etl.validate import PLAYER_GAME_RULES, SchemaError, apply_rules, check_schema


def test_parse_minutes_handles_every_source_format():
    df = pl.DataFrame({"numMinutes": ["34.5", "34:30", "0", "-2", "", None, "9:05"]})
    out = df.select(parse_minutes()).to_series().to_list()
    assert out[0] == pytest.approx(34.5)
    assert out[1] == pytest.approx(34.5)
    assert out[2] == 0
    assert out[3] is None          # negative -> NULL
    assert out[4] is None and out[5] is None
    assert out[6] == pytest.approx(9 + 5 / 60)


@pytest.mark.parametrize("comment,minutes,stat,expected", [
    ("", 31.0, True, "played"),
    ("DNP - Coach's Decision", None, False, "dnp"),
    ("DND - Injury/Illness", None, False, "inactive"),
    ("NWT - Not With Team", None, False, "inactive"),
    ("Inactive - Coach's Decision", None, False, "inactive"),
    ("", None, True, "played"),       # stats but minutes missing in source
    ("", 0.0, False, "dnp"),
    ("", None, False, "unknown"),     # nothing known -> not a zero-stat appearance
    ("DNP - Coach's Decision", 2.0, True, "played"),  # minutes beat a stale comment
])
def test_participation(comment, minutes, stat, expected):
    df = pl.DataFrame({"c": [comment], "m": [minutes], "s": [stat]}, schema={"c": pl.String, "m": pl.Float64,
                                                                          "s": pl.Boolean})
    assert df.select(classify_participation(pl.col("c"), pl.col("m"), pl.col("s"))).item() == expected


def _pgs(**over):
    row = {c: None for c in PLAYER_GAME_COLUMNS}
    row.update(game_id=1, player_id=10, team_id=100, opponent_team_id=200, is_home=True, participation="played",
               minutes=30.0, points=21, fgm=8, fga=15, fg3m=2, fg3a=5, ftm=3, fta=4, rebounds=7, oreb=2, dreb=5,
               assists=4, steals=1, blocks=0, turnovers=2, fouls=3)
    row.update(over)
    return row


def _frame(rows):
    return pl.DataFrame(rows, schema=PLAYER_GAME_COLUMNS)


def test_validation_drops_errors_and_flags_warnings():
    rows = [_pgs(),                                     # clean
            _pgs(player_id=11, points=-3),              # negative count -> dropped
            _pgs(player_id=12, fgm=20, fga=10),         # made > attempted -> dropped
            _pgs(player_id=13, points=22),              # points identity broken -> warning, kept
            _pgs(player_id=10)]                         # duplicate key -> dropped
    log = IssueLog()
    out = apply_rules("player_game_stats", _frame(rows), ["game_id", "player_id"], PLAYER_GAME_RULES, log)
    assert sorted(out["player_id"].to_list()) == [10, 13]
    rules = {(i.rule, i.action) for i in log.issues}
    assert ("negative_count", "dropped") in rules
    assert ("made_gt_attempted", "dropped") in rules
    assert ("points_identity", "flagged") in rules
    assert ("duplicate_key", "dropped") in rules


def test_schema_contract_is_enforced():
    df = _frame([_pgs()]).drop("minutes")
    with pytest.raises(SchemaError):
        check_schema("player_game_stats", df, PLAYER_GAME_COLUMNS)

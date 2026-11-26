"""Storage compaction for prediction snapshots.

Every refresh that sees a changed input publishes a full new revision of a game's forecast (~340 NBA rows: every
player line). Each finished game keeps up to ~15 revisions, most of them near-identical, and every one gets graded.
That's ~85 % of the predictions tables and was growing ~20 MB a day: too much for a free hosted Postgres.

Nothing reads superseded revisions of a finished game except as a trail: accuracy, calibration, line movement,
value and the "one per game" history all score the latest revision per (game, kind) only. So once a game is over
and its latest revision is fully graded, we delete the superseded revisions' rows (and their outcomes), **except**
the headline win-probability rows (``moneyline`` / soccer ``match_result``). Those are 2-3 rows per revision, and
keep the revision list ("revision 3 of 7", publish times) and the forecast's win-probability history intact.

The latest revision of every game, the rows every metric is computed from, is never touched. Rows are still never
updated; deletes are refused by the immutability trigger unless the transaction sets ``polypicker.compacting``.
"""

from __future__ import annotations

from sqlalchemy import Connection, Engine, text

# (predictions table, outcomes table, games table, markets kept on every revision)
SPORTS = {
    "nba": ("predictions", "prediction_outcomes", "games", ("moneyline",)),
    "nfl": ("nfl_predictions", "nfl_prediction_outcomes", "nfl_games", ("moneyline",)),
    "soccer": ("soccer_predictions", "soccer_prediction_outcomes", "soccer_games", ("match_result",)),
}


def _doomed_sql(pred: str, out: str, games: str) -> str:
    """Ids of superseded-revision rows of finished, fully graded games (outside the kept markets)."""
    return f"""
        WITH latest AS (SELECT game_id, kind, max(revision) r FROM {pred} GROUP BY 1, 2),
        done AS (
            SELECT l.game_id, l.kind, l.r FROM latest l JOIN {games} g ON g.game_id = l.game_id
            WHERE g.status IN ('final', 'canceled')
              AND NOT EXISTS (   -- the latest revision must be fully graded first
                  SELECT 1 FROM {pred} p LEFT JOIN {out} o ON o.prediction_id = p.id
                  WHERE p.game_id = l.game_id AND p.kind = l.kind AND p.revision = l.r
                    AND (o.prediction_id IS NULL OR o.graded_state = 'pending')))
        SELECT p.id FROM {pred} p JOIN done d ON d.game_id = p.game_id AND d.kind = p.kind
        WHERE p.revision < d.r AND p.market <> ALL(:keep)"""


def compact_conn(conn: Connection, sport: str, dry_run: bool = False) -> dict:
    """Compact one sport inside the caller's transaction."""
    pred, out, games, keep = SPORTS[sport]
    conn.execute(text("SET LOCAL polypicker.compacting = 'on'"))
    conn.execute(text("DROP TABLE IF EXISTS _doomed"))
    conn.execute(text(f"CREATE TEMP TABLE _doomed ON COMMIT DROP AS {_doomed_sql(pred, out, games)}"), {"keep": list(keep)})
    n = conn.execute(text("SELECT count(*) FROM _doomed")).scalar_one()
    if dry_run or not n:
        return {"prediction_rows": n, "deleted": False}
    o = conn.execute(text(f"DELETE FROM {out} WHERE prediction_id IN (SELECT id FROM _doomed)")).rowcount
    p = conn.execute(text(f"DELETE FROM {pred} WHERE id IN (SELECT id FROM _doomed)")).rowcount
    return {"prediction_rows": p, "outcome_rows": o, "deleted": True}


def compact(engine: Engine, sports: list[str] | None = None, dry_run: bool = False) -> dict:
    """Delete superseded revisions of finished games (see module docstring), one transaction per sport."""
    res: dict[str, dict] = {}
    for sport in sports or list(SPORTS):
        with engine.begin() as conn:
            res[sport] = compact_conn(conn, sport, dry_run)
    return res

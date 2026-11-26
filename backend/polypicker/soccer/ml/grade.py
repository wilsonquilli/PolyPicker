"""Grade soccer forecasts on the 90-minute score (extra time and penalties don't count): each match-result side (home / draw / away) is a hit or miss, expected goals get an
absolute error, over/under and both-teams-to-score are hits or misses. Snapshots are never touched."""

from __future__ import annotations

from sqlalchemy import Engine, text
from sqlalchemy.dialects.postgresql import insert

from polypicker.domain.markets import GradedState, Outcome
from polypicker.ml.grade import _scores
from polypicker.soccer import models as sm

_SQL = """
SELECT p.id, p.market, p.side, p.threshold_kind, p.threshold, p.probability, p.projection, g.status,
       CASE WHEN g.decided IS NOT NULL THEN g.home_score_90 ELSE g.home_score END AS home_score,   -- 90-minute score
       CASE WHEN g.decided IS NOT NULL THEN g.away_score_90 ELSE g.away_score END AS away_score
FROM soccer_predictions p JOIN soccer_games g ON g.game_id = p.game_id
LEFT JOIN soccer_prediction_outcomes o ON o.prediction_id = p.id
WHERE o.prediction_id IS NULL
   -- an existing pending grade can only change once the game is over (re-grading the rest is a no-op)
   OR ((o.graded_state = 'pending') AND g.status IN ('final', 'canceled'))
"""


def grade_row(r) -> dict:
    out = {"prediction_id": r.id, "graded_state": GradedState.PENDING.value, "outcome": None, "observed_value": None,
           "brier": None, "log_loss": None, "abs_error": None}
    if r.status == "canceled":
        return out | {"graded_state": GradedState.VOID.value}
    if r.status != "final":
        return out
    if r.home_score is None or r.away_score is None:
        return out | {"graded_state": GradedState.UNAVAILABLE.value}
    h, a = r.home_score, r.away_score
    out["graded_state"] = GradedState.FINAL.value
    if r.market == "team_goals":
        v = h if r.side == "home" else a
        return out | {"observed_value": float(v), "abs_error": abs(r.projection - v)}
    if r.market == "total_goals" and r.threshold_kind is None:
        return out | {"observed_value": float(h + a), "abs_error": abs(r.projection - (h + a))}
    if r.market == "match_result":
        res = "home" if h > a else "away" if a > h else "draw"
        hit, obs = r.side == res, float(h - a)
    elif r.market == "total_goals":
        hit, obs = (h + a) > r.threshold, float(h + a)
    elif r.market == "btts":
        hit, obs = h > 0 and a > 0, float(min(h, a) > 0)
    else:
        return out | {"graded_state": GradedState.UNEVALUABLE.value}
    b, ll = _scores(r.probability, int(hit))
    return out | {"observed_value": obs, "outcome": (Outcome.HIT if hit else Outcome.MISS).value, "brier": b, "log_loss": ll}


def grade_all(engine: Engine) -> dict:
    with engine.connect() as c:
        rows = c.execute(text(_SQL)).all()
    graded = [grade_row(r) for r in rows]
    counts: dict[str, int] = {}
    for g in graded:
        counts[g["graded_state"]] = counts.get(g["graded_state"], 0) + 1
    tbl = sm.SoccerPredictionOutcome.__table__
    with engine.begin() as c:
        for i in range(0, len(graded), 5000):
            stmt = insert(tbl).values(graded[i:i + 5000])
            c.execute(stmt.on_conflict_do_update(index_elements=["prediction_id"], set_={
                k: stmt.excluded[k] for k in ("graded_state", "outcome", "observed_value", "brier", "log_loss", "abs_error",
                                              "graded_at")}))
    return {"examined": len(rows), **counts}

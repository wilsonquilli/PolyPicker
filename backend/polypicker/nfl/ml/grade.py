"""Grade NFL forecast snapshots against final results (same states as the NBA: pending, final, void,
unavailable, unevaluable). Grades go to ``nfl_prediction_outcomes``; snapshots are never touched.

NFL specifics: a tie settles the moneyline as a push; a player with no box-score row in a game whose
box scores are in did not play -> void (same as an inactive player).
"""

from __future__ import annotations

from sqlalchemy import Engine, text
from sqlalchemy.dialects.postgresql import insert

from polypicker.domain.markets import GradedState, Market, Outcome, ThresholdKind, settle_spread, settle_threshold
from polypicker.ml.grade import _scores
from polypicker.nfl import models as nm
from polypicker.nfl.markets import MARKET_STAT

_SQL = """
SELECT p.id, p.market, p.side, p.threshold_kind, p.threshold, p.probability, p.projection, p.subject_id,
       g.status, g.home_score, g.away_score, g.has_player_stats,
       s.participation, s.passing_yards, s.passing_tds, s.rushing_yards, s.receptions, s.receiving_yards, s.scrimmage_tds
FROM nfl_predictions p
JOIN nfl_games g ON g.game_id = p.game_id
LEFT JOIN nfl_prediction_outcomes o ON o.prediction_id = p.id
LEFT JOIN nfl_player_game_stats s ON p.subject_type = 'player' AND s.game_id = p.game_id AND s.player_id = p.subject_id
WHERE o.prediction_id IS NULL
   -- an existing pending grade can only change once the game is over (re-grading the rest is a no-op)
   OR ((o.graded_state = 'pending' OR o.graded_state = 'unavailable') AND g.status IN ('final', 'canceled'))
"""


def grade_row(r) -> dict:
    out = {"prediction_id": r.id, "graded_state": GradedState.PENDING.value, "outcome": None, "observed_value": None,
           "brier": None, "log_loss": None, "abs_error": None}
    if r.status == "canceled":
        return out | {"graded_state": GradedState.VOID.value}
    if r.status != "final":
        return out
    stat = MARKET_STAT.get(r.market)
    kind = ThresholdKind(r.threshold_kind) if r.threshold_kind else None
    if stat:
        if r.probability is None and r.projection is None:      # excluded (Out/Doubtful): no forecast was made
            return out | {"graded_state": (GradedState.UNEVALUABLE if r.participation == "played" else GradedState.VOID).value}
        if not r.has_player_stats:
            return out | {"graded_state": GradedState.UNAVAILABLE.value}
        if r.participation != "played":
            return out | {"graded_state": GradedState.VOID.value}  # inactive / did not play
        value = getattr(r, stat)
        if value is None:
            return out | {"graded_state": GradedState.UNAVAILABLE.value}
    elif r.home_score is None or r.away_score is None:
        return out | {"graded_state": GradedState.UNAVAILABLE.value}
    else:
        margin = r.home_score - r.away_score
        market = Market(r.market)
        value = {Market.MONEYLINE: margin, Market.TEAM_POINTS: r.home_score if r.side == "home" else r.away_score,
                 Market.SPREAD: margin, Market.TOTAL: r.home_score + r.away_score}[market]
        if market is Market.MONEYLINE:
            out |= {"graded_state": GradedState.FINAL.value, "observed_value": float(margin)}
            if margin == 0:
                return out | {"outcome": Outcome.PUSH.value}
            won = (margin > 0) == (r.side == "home")
            b, ll = _scores(r.probability, int(won))
            return out | {"outcome": (Outcome.HIT if won else Outcome.MISS).value, "brier": b, "log_loss": ll}

    out |= {"graded_state": GradedState.FINAL.value, "observed_value": float(value)}
    if kind is None:
        return out | {"abs_error": abs(r.projection - value) if r.projection is not None else None}
    if kind is ThresholdKind.HANDICAP:
        res = settle_spread(value if r.side == "home" else -value, r.threshold)
    else:
        res = settle_threshold(value, kind, r.threshold)
    out["outcome"] = res.value
    if res is not Outcome.PUSH:
        b, ll = _scores(r.probability, int(res is Outcome.HIT))
        out |= {"brier": b, "log_loss": ll}
    if r.projection is not None:
        out["abs_error"] = abs(r.projection - value)
    return out


def grade_all(engine: Engine) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(text(_SQL)).all()
    graded = [grade_row(r) for r in rows]
    counts: dict[str, int] = {}
    for g in graded:
        counts[g["graded_state"]] = counts.get(g["graded_state"], 0) + 1
    tbl = nm.NflPredictionOutcome.__table__
    with engine.begin() as conn:
        for i in range(0, len(graded), 5000):
            chunk = graded[i:i + 5000]
            stmt = insert(tbl).values(chunk)
            conn.execute(stmt.on_conflict_do_update(index_elements=["prediction_id"], set_={
                c: stmt.excluded[c] for c in ("graded_state", "outcome", "observed_value", "brier", "log_loss",
                                              "abs_error", "graded_at")}))
    return {"examined": len(rows), **counts}

"""Grade prediction snapshots against final results (FR-09.2 - FR-09.6).

Grades go to ``prediction_outcomes``; the snapshots in ``predictions`` are never touched.
"""

from __future__ import annotations

import math

from sqlalchemy import Engine, text
from sqlalchemy.dialects.postgresql import insert

from polypicker.db import models as m
from polypicker.domain.markets import GradedState, Market, Outcome, ThresholdKind, settle_spread, settle_threshold

_SQL = """
SELECT p.id, p.market, p.side, p.threshold_kind, p.threshold, p.probability, p.projection, p.subject_id,
       g.status, g.home_score, g.away_score, g.has_player_stats,
       s.participation, s.points, s.assists, s.rebounds
FROM predictions p
JOIN games g ON g.game_id = p.game_id
LEFT JOIN prediction_outcomes o ON o.prediction_id = p.id
LEFT JOIN player_game_stats s ON p.subject_type = 'player' AND s.game_id = p.game_id AND s.player_id = p.subject_id
WHERE o.prediction_id IS NULL OR o.graded_state = 'pending'
"""


def _scores(p: float | None, y: int) -> tuple[float | None, float | None]:
    if p is None:
        return None, None
    q = min(max(p, 1e-6), 1 - 1e-6)
    return (p - y) ** 2, -(y * math.log(q) + (1 - y) * math.log(1 - q))


def grade_row(r) -> dict:
    """Pure function: one joined row -> outcome fields."""
    out = {"prediction_id": r.id, "graded_state": GradedState.PENDING.value, "outcome": None,
           "observed_value": None, "brier": None, "log_loss": None, "abs_error": None}
    if r.status == "canceled":
        return out | {"graded_state": GradedState.VOID.value}
    if r.status != "final":
        return out
    market = Market(r.market)
    kind = ThresholdKind(r.threshold_kind) if r.threshold_kind else None

    if market.is_player:
        if r.probability is None and r.projection is None:  # excluded / no forecast was made (FR-03.6)
            played = r.participation == "played"
            return out | {"graded_state": (GradedState.UNEVALUABLE if played else GradedState.VOID).value}
        if not r.has_player_stats:
            return out | {"graded_state": GradedState.UNAVAILABLE.value}
        if r.participation is None or r.participation != "played":
            return out | {"graded_state": GradedState.VOID.value}  # DNP / inactive / not listed
        value = getattr(r, market.stat_column)
        if value is None:
            return out | {"graded_state": GradedState.UNAVAILABLE.value}
    elif r.home_score is None or r.away_score is None:
        return out | {"graded_state": GradedState.UNAVAILABLE.value}
    else:
        margin = r.home_score - r.away_score
        value = {Market.MONEYLINE: margin, Market.TEAM_POINTS: r.home_score if r.side == "home" else r.away_score,
                 Market.SPREAD: margin, Market.TOTAL: r.home_score + r.away_score}[market]

    out |= {"graded_state": GradedState.FINAL.value, "observed_value": float(value)}
    if market is Market.MONEYLINE:
        won = (value > 0) == (r.side == "home")
        b, ll = _scores(r.probability, int(won))
        return out | {"outcome": (Outcome.HIT if won else Outcome.MISS).value, "brier": b, "log_loss": ll}
    if kind is None:  # projection-only row (team points, margin, total, player expected value)
        return out | {"abs_error": abs(r.projection - value) if r.projection is not None else None}
    if kind is ThresholdKind.HANDICAP:
        side_margin = value if r.side == "home" else -value
        res = settle_spread(side_margin, r.threshold)
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
    tbl = m.PredictionOutcome.__table__
    with engine.begin() as conn:
        for i in range(0, len(graded), 5000):
            chunk = graded[i:i + 5000]
            stmt = insert(tbl).values(chunk)
            conn.execute(stmt.on_conflict_do_update(
                index_elements=["prediction_id"],
                set_={c: stmt.excluded[c] for c in ("graded_state", "outcome", "observed_value", "brier",
                                                    "log_loss", "abs_error", "graded_at")}))
    return {"examined": len(rows), **counts}

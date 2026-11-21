"""Prediction markets and settlement semantics (spec §2.2, §7.3; FR-02.4/2.5, FR-03.4/3.7).

Threshold semantics
-------------------
* ``AT_LEAST`` k  -> "k+": hits when value >= k. Never pushes.
* ``OVER`` line   -> "over line": hits when value > line. If ``line`` is an integer,
  value == line is a PUSH; half-point lines (20.5) can never push.
  So "over 20.5" == "21+" and "20+" == "at least 20".
* Spread: side covers when (side margin + handicap) > 0; == 0 is a push.
"""

from __future__ import annotations

import math
from enum import StrEnum


class Market(StrEnum):
    MONEYLINE = "moneyline"            # full game incl. overtime
    TEAM_POINTS = "team_points"        # projected score per team
    SPREAD = "spread"
    TOTAL = "total"
    PLAYER_POINTS = "player_points"
    PLAYER_ASSISTS = "player_assists"
    PLAYER_REBOUNDS = "player_rebounds"

    @property
    def is_player(self) -> bool:
        return self.value.startswith("player_")

    @property
    def stat_column(self) -> str | None:
        return {"player_points": "points", "player_assists": "assists",
                "player_rebounds": "rebounds"}.get(self.value)


class ThresholdKind(StrEnum):
    AT_LEAST = "at_least"
    OVER = "over"
    HANDICAP = "handicap"  # spread: value is the points handicap applied to the side


class Outcome(StrEnum):
    HIT = "hit"
    MISS = "miss"
    PUSH = "push"


class GradedState(StrEnum):
    """FR-09.5."""
    PENDING = "pending"          # game not final yet
    FINAL = "final"              # graded against the final result
    VOID = "void"                # e.g. player DNP, game canceled
    UNAVAILABLE = "unavailable"  # result data missing (e.g. no box score ingested)
    UNEVALUABLE = "unevaluable"  # prediction cannot be scored (e.g. no probability)


def settle_threshold(value: float, kind: ThresholdKind, threshold: float) -> Outcome:
    if kind is ThresholdKind.AT_LEAST:
        return Outcome.HIT if value >= threshold else Outcome.MISS
    if kind is ThresholdKind.OVER:
        if value > threshold:
            return Outcome.HIT
        if value == threshold:
            return Outcome.PUSH
        return Outcome.MISS
    raise ValueError(f"Use settle_spread for {kind}")


def settle_spread(side_margin: float, handicap: float) -> Outcome:
    """side_margin = side score - opponent score; handicap e.g. -5.5 for a 5.5-pt favorite."""
    adj = side_margin + handicap
    if adj > 0:
        return Outcome.HIT
    if adj == 0:
        return Outcome.PUSH
    return Outcome.MISS


def can_push(kind: ThresholdKind, threshold: float) -> bool:
    if kind is ThresholdKind.AT_LEAST:
        return False
    return float(threshold).is_integer()


def label(market: Market, kind: ThresholdKind, threshold: float | None, side: str | None = None) -> str:
    """Human-readable definition shown next to every threshold (FR-02.5, FR-03.7)."""
    noun = {Market.PLAYER_POINTS: "points", Market.PLAYER_ASSISTS: "assists",
            Market.PLAYER_REBOUNDS: "rebounds", Market.TOTAL: "total points"}.get(market, "")
    if market is Market.MONEYLINE:
        return f"{side} wins (including overtime)"
    if market is Market.SPREAD:
        sign = "+" if threshold > 0 else ""
        push = " (exact = push)" if can_push(kind, threshold) else ""
        return f"{side} {sign}{_fmt(threshold)} covers{push}"
    if kind is ThresholdKind.AT_LEAST:
        return f"{_fmt(threshold)}+ {noun}"
    if kind is ThresholdKind.OVER:
        tail = f" (exactly {_fmt(threshold)} = push)" if can_push(kind, threshold) else \
            f" (i.e. {math.floor(threshold) + 1}+)"
        return f"{side or 'over'} {_fmt(threshold)} {noun}{tail}"
    return f"{market} {threshold}"


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}"


def round_probability_pair(p_home: float) -> tuple[float, float]:
    """Round two complementary probabilities to 1 decimal % so they sum to exactly 100 (FR-02.2)."""
    home = round(p_home * 100, 1)
    return home, round(100.0 - home, 1)

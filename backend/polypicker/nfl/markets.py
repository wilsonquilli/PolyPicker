"""NFL market names and labels. Settlement rules (k+, over/under with pushes, spreads) are shared with
the NBA in ``polypicker.domain.markets``; the NFL adds ties (moneyline push) and football stats."""

from __future__ import annotations

import math

from polypicker.domain.markets import ThresholdKind, can_push, label as generic_label, Market

PLAYER_MARKETS = {
    "passing_yards": "player_passing_yards", "passing_tds": "player_passing_tds",
    "rushing_yards": "player_rushing_yards", "receptions": "player_receptions",
    "receiving_yards": "player_receiving_yards", "scrimmage_tds": "player_anytime_td",
}
MARKET_STAT = {v: k for k, v in PLAYER_MARKETS.items()}
NOUN = {"passing_yards": "passing yards", "passing_tds": "passing TDs", "rushing_yards": "rushing yards",
        "receptions": "receptions", "receiving_yards": "receiving yards", "scrimmage_tds": "rushing/receiving TDs"}
# Rollout (like the NBA's FR-03.3): yardage and receptions first; touchdowns are lumpier -> beta.
ROLLOUT = {"passing_yards": "ga", "rushing_yards": "ga", "receiving_yards": "ga", "receptions": "ga",
           "passing_tds": "beta", "scrimmage_tds": "beta"}


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}"


def moneyline_label(side: str) -> str:
    return f"{side} wins (a tie is a push)"


def game_label(market: Market, kind: ThresholdKind, threshold: float, side: str) -> str:
    return generic_label(market, kind, threshold, side)


def player_label(stat: str, kind: ThresholdKind, threshold: float) -> str:
    noun = NOUN[stat]
    if stat == "scrimmage_tds" and kind is ThresholdKind.AT_LEAST:
        return "Scores a rushing or receiving TD" if threshold == 1 else f"{_fmt(threshold)}+ rushing/receiving TDs"
    if kind is ThresholdKind.AT_LEAST:
        return f"{_fmt(threshold)}+ {noun}"
    tail = f" (exactly {_fmt(threshold)} = push)" if can_push(kind, threshold) else f" (i.e. {math.floor(threshold) + 1}+)"
    return f"over {_fmt(threshold)} {noun}{tail}"

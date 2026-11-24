"""Market-implied expected goals: the (home, away) Poisson scoring rates whose scoreline grid best reproduces a
match's sportsbook 1X2 probabilities and, when quoted, its over/under line.

Solved by nearest neighbour over a precomputed grid of rate pairs (0.05..4.5 goals, step 0.02), using a
Dixon-Coles grid with a typical rho so draws aren't under-priced. Outputs feed the market model as log rates.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import polars as pl
from scipy import stats
from scipy.spatial import cKDTree

RATES = np.arange(0.05, 4.5001, 0.02)
LINES = (0.5, 1.5, 2.5, 3.5, 4.5)
RHO = -0.05
MAX_GOALS = 12


@lru_cache(maxsize=1)
def _table() -> tuple[np.ndarray, np.ndarray, dict[float, np.ndarray]]:
    k = np.arange(MAX_GOALS + 1)
    pmf = stats.poisson.pmf(k[None, :], RATES[:, None])            # (R, K)
    lh, la = np.meshgrid(RATES, RATES, indexing="ij")
    g = pmf[:, None, :, None] * pmf[None, :, None, :]                 # (R, R, K, K)
    g[:, :, 0, 0] *= (1 - lh * la * RHO)
    g[:, :, 0, 1] *= (1 + lh * RHO)
    g[:, :, 1, 0] *= (1 + la * RHO)
    g[:, :, 1, 1] *= (1 - RHO)
    g = np.clip(g, 0, None)
    g /= g.sum(axis=(2, 3), keepdims=True)
    i, j = np.indices((MAX_GOALS + 1, MAX_GOALS + 1))
    home = (g * (i > j)).sum(axis=(2, 3)).ravel()
    away = (g * (i < j)).sum(axis=(2, 3)).ravel()
    over = {L: (g * ((i + j) > L)).sum(axis=(2, 3)).ravel() for L in LINES}
    pairs = np.stack([lh.ravel(), la.ravel()], axis=1)
    return pairs, np.stack([home, away], axis=1), over


@lru_cache(maxsize=8)
def _tree(line: float | None) -> cKDTree:
    _, ha, over = _table()
    return cKDTree(ha if line is None else np.column_stack([ha, over[line]]))


def implied_rates(p_home: np.ndarray, p_away: np.ndarray, line: np.ndarray, p_over: np.ndarray) -> np.ndarray:
    """(n, 2) array of implied (home, away) expected goals; NaN where the 1X2 is missing."""
    pairs = _table()[0]
    out = np.full((len(p_home), 2), np.nan)
    ok = ~(np.isnan(p_home) | np.isnan(p_away))
    has_tot = ok & ~np.isnan(line) & ~np.isnan(p_over) & np.isin(line, LINES)
    for L in LINES:
        m = has_tot & (line == L)
        if m.any():
            _, ix = _tree(L).query(np.column_stack([p_home[m], p_away[m], p_over[m]]))
            out[m] = pairs[ix]
    m = ok & ~has_tot
    if m.any():
        _, ix = _tree(None).query(np.column_stack([p_home[m], p_away[m]]))
        out[m] = pairs[ix]
    return out


def odds_features(odds: pl.DataFrame) -> pl.DataFrame:
    """game_id, mkt_xg_home, mkt_xg_away from soccer_odds rows."""
    if odds.is_empty():
        return pl.DataFrame(schema={"game_id": pl.Int64, "mkt_xg_home": pl.Float64, "mkt_xg_away": pl.Float64})
    f = lambda c: odds[c].cast(pl.Float64).fill_null(np.nan).to_numpy()  # noqa: E731
    r = implied_rates(f("p_home"), f("p_away"), f("total_line"), f("p_over"))
    return pl.DataFrame({"game_id": odds["game_id"], "mkt_xg_home": r[:, 0], "mkt_xg_away": r[:, 1]})

"""Predictive distributions -> threshold probabilities (spec §7.3).

A projection (expected value) is NOT a probability; threshold probabilities come from a
modeled distribution whose spread was fitted on held-out data:

* game margin / total: integer outcomes, modeled as a continuity-corrected Normal
* player counts (points, assists, rebounds): Negative Binomial (over-dispersed Poisson)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import special, stats

from polypicker.domain.markets import ThresholdKind


@dataclass(frozen=True)
class ThresholdProb:
    p_hit: float   # P(over / at-least / cover)
    p_push: float  # P(exact landing on an integer line)

    @property
    def p_miss(self) -> float:
        return max(0.0, 1.0 - self.p_hit - self.p_push)


# ---------------------------------------------------------------- integer Normal (game markets)

def normal_over(mu: float, sigma: float, line: float) -> ThresholdProb:
    """P(X > line) for integer-valued X ~ Normal(mu, sigma) with continuity correction."""
    if float(line).is_integer():
        p_hit = 1 - stats.norm.cdf(line + 0.5, mu, sigma)
        p_push = stats.norm.cdf(line + 0.5, mu, sigma) - stats.norm.cdf(line - 0.5, mu, sigma)
    else:
        p_hit = 1 - stats.norm.cdf(line, mu, sigma)  # no integer between line +/- 0.5 except via rounding
        p_push = 0.0
    return ThresholdProb(float(p_hit), float(p_push))


def spread_cover(mu_margin: float, sigma: float, handicap: float) -> ThresholdProb:
    """P(side margin + handicap > 0), margin ~ Normal(mu, sigma) on the side's perspective."""
    return normal_over(mu_margin, sigma, -handicap)


def win_probability(mu_margin: float, sigma_win: float) -> float:
    """NBA games cannot tie (overtime), so P(win) = P(margin > 0) on a continuous scale."""
    return float(stats.norm.sf(0, mu_margin, sigma_win))


# ---------------------------------------------------------------- Negative Binomial (player counts)

def nb_params(mean: float, dispersion: float) -> tuple[float, float]:
    """scipy's (n, p) for NB with given mean and size ``dispersion`` (var = m + m^2 / r)."""
    mean = max(mean, 1e-6)
    r = dispersion
    p = r / (r + mean)
    return r, p


def count_threshold(mean: float, dispersion: float, kind: ThresholdKind, threshold: float) -> ThresholdProb:
    n, p = nb_params(mean, dispersion)
    if kind is ThresholdKind.AT_LEAST:
        k = int(np.ceil(threshold))
        return ThresholdProb(float(stats.nbinom.sf(k - 1, n, p)), 0.0)
    if kind is ThresholdKind.OVER:
        if float(threshold).is_integer():
            k = int(threshold)
            return ThresholdProb(float(stats.nbinom.sf(k, n, p)), float(stats.nbinom.pmf(k, n, p)))
        return ThresholdProb(float(stats.nbinom.sf(int(np.floor(threshold)), n, p)), 0.0)
    raise ValueError(kind)


def _nb_loglik(y: np.ndarray, mu: np.ndarray, r: np.ndarray) -> float:
    return float(np.sum(special.gammaln(y + r) - special.gammaln(r) - special.gammaln(y + 1)
                        + r * np.log(r / (r + mu)) + y * np.log(mu / (r + mu))))


def dispersion_at(mean: float, r0: float, gamma: float) -> float:
    """Mean-dependent NB size: r(mu) = r0 * mu^gamma (gamma=0 is a constant-dispersion NB)."""
    return float(r0 * max(mean, 0.05) ** gamma)


def fit_nb_dispersion(y: np.ndarray, mu: np.ndarray) -> tuple[float, float]:
    """Maximum-likelihood (r0, gamma) for r(mu) = r0 * mu^gamma, given fixed means.

    A single constant dispersion is pulled toward low-minute players whose scoring is lumpy,
    which makes high-usage players' distributions too wide (under-confident thresholds).
    """
    y = np.asarray(y, float)
    mu = np.clip(np.asarray(mu, float), 0.05, None)
    best, best_ll = (1.0, 0.0), -np.inf
    for gamma in np.linspace(0.0, 1.6, 33):
        mg = mu ** gamma
        for r0 in np.exp(np.linspace(np.log(0.02), np.log(200), 90)):
            ll = _nb_loglik(y, mu, r0 * mg)
            if ll > best_ll:
                best, best_ll = (float(r0), float(gamma)), ll
    return best


def fit_sigma_for_win(mu_margin: np.ndarray, home_won: np.ndarray) -> float:
    """Scale that minimizes log loss of P(win)=Phi(mu/sigma) -- a one-parameter calibration."""
    best, best_ll = 12.0, np.inf
    for s in np.linspace(6, 25, 191):
        p = np.clip(stats.norm.sf(0, mu_margin, s), 1e-6, 1 - 1e-6)
        ll = -np.mean(home_won * np.log(p) + (1 - home_won) * np.log(1 - p))
        if ll < best_ll:
            best, best_ll = s, ll
    return float(best)

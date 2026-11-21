"""Player prop models: expected points / assists / rebounds + Negative Binomial thresholds (FR-03).

For each stat: a gradient-boosted Poisson regressor on pregame features (minutes trend,
per-minute rates, opponent and pace context). The spread of outcomes is a Negative Binomial
whose dispersion is fitted on held-out games. Compared against two baselines: the player's
season average and a simple minutes x rate projection (NFR-09.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingRegressor

from polypicker.domain.markets import ThresholdKind
from polypicker.ml import metrics
from polypicker.ml.distributions import count_threshold, dispersion_at, fit_nb_dispersion

COMMON = ["min_l5", "min_l10", "min_ewm", "min_season", "start_rate_l10", "gp_rate_l10", "season_games",
          "days_since_played", "is_home", "b2b", "pace_factor", "opp_def_factor", "opp_def_factor_l10"]


def stat_features(stat: str) -> list[str]:
    return COMMON + [f"{stat}_rate", f"{stat}_rate_l10", f"{stat}_rate_vs_league", f"{stat}_base"]


# Default thresholds offered per stat ("k+" lines); filtered to informative probabilities.
DEFAULT_AT_LEAST = {"points": [10, 15, 20, 25, 30, 35, 40], "assists": [2, 4, 6, 8, 10, 12],
                    "rebounds": [4, 6, 8, 10, 12, 14]}
MIN_TRAIN_HISTORY = 3
THRESHOLD_MAX_ECE = 0.04


def _X(df: pl.DataFrame, stat: str) -> np.ndarray:
    return df.select([pl.col(c).cast(pl.Float64) for c in stat_features(stat)]).to_numpy()


@dataclass
class StatModel:
    stat: str
    model: object
    r0: float
    gamma: float
    thresholds_supported: bool
    metrics: dict = field(default_factory=dict)
    version: str = ""

    @classmethod
    def train(cls, stat: str, data: pl.DataFrame, train_end: date, valid_start: date, valid_end: date) -> "StatModel":
        data = data.filter(pl.col("n_recent") >= MIN_TRAIN_HISTORY)
        tr = data.filter(pl.col("game_date_et") <= train_end)
        va = data.filter((pl.col("game_date_et") >= valid_start) & (pl.col("game_date_et") <= valid_end))
        y_tr, y_va = tr[f"y_{stat}"].to_numpy().astype(float), va[f"y_{stat}"].to_numpy().astype(float)

        def fit(X, y):
            return HistGradientBoostingRegressor(loss="poisson", learning_rate=0.05, max_iter=300,
                                                 max_leaf_nodes=31, min_samples_leaf=80,
                                                 l2_regularization=1.0, random_state=7).fit(X, y)

        mdl = fit(_X(tr, stat), y_tr)
        mu = np.clip(mdl.predict(_X(va, stat)), 0.05, None)
        r0, gamma = fit_nb_dispersion(y_va, mu)
        # Threshold calibration on held-out games
        ps, ys = [], []
        for k in DEFAULT_AT_LEAST[stat]:
            p = np.array([count_threshold(m, dispersion_at(m, r0, gamma), ThresholdKind.AT_LEAST, k).p_hit
                          for m in mu])
            keep = (p > 0.03) & (p < 0.97)
            ps.append(p[keep]); ys.append((y_va[keep] >= k).astype(float))
        ps_all, ys_all = np.concatenate(ps), np.concatenate(ys)
        ece = metrics.expected_calibration_error(ps_all, ys_all)
        report = {
            "validation_rows": int(va.height),
            "mae": round(metrics.mae(mu, y_va), 3),
            "mae_baseline_season_avg": round(metrics.mae(va[f"{stat}_season_avg"].to_numpy(), y_va), 3),
            "mae_baseline_minutes_x_rate": round(metrics.mae(va[f"{stat}_base"].to_numpy(), y_va), 3),
            "nb_dispersion": {"r0": round(r0, 4), "gamma": round(gamma, 3)},
            "threshold": metrics.binary_report(ps_all, ys_all),
            "calibration_threshold": metrics.calibration_bins(ps_all, ys_all),
        }
        full = data.filter(pl.col("game_date_et") <= valid_end)
        final = fit(_X(full, stat), full[f"y_{stat}"].to_numpy().astype(float))
        return cls(stat, final, r0, gamma, bool(ece <= THRESHOLD_MAX_ECE), report)

    def dispersion(self, mean: float) -> float:
        return dispersion_at(mean, self.r0, self.gamma)

    def predict(self, cands: pl.DataFrame) -> np.ndarray:
        return np.clip(self.model.predict(_X(cands, self.stat)), 0.05, None)

    def threshold_rows(self, mean: float) -> list[tuple[ThresholdKind, float, float, float]]:
        """Default lines: informative 'k+' thresholds plus one half-point 'over' line near the mean."""
        out = []
        for k in DEFAULT_AT_LEAST[self.stat]:
            tp = count_threshold(mean, self.dispersion(mean), ThresholdKind.AT_LEAST, k)
            if 0.03 < tp.p_hit < 0.97:
                out.append((ThresholdKind.AT_LEAST, float(k), tp.p_hit, tp.p_push))
        line = float(np.floor(mean)) + 0.5
        tp = count_threshold(mean, self.dispersion(mean), ThresholdKind.OVER, line)
        out.append((ThresholdKind.OVER, line, tp.p_hit, tp.p_push))
        return out


def explain_player(row: dict, stat: str, mean: float) -> list[str]:
    """Plain-language description of the main inputs (no causal claims; FR-05.4)."""
    lines = [f"Projected around {row['min_ewm']:.0f} minutes (recent average {row['min_l10']:.0f} over last games played)",
             f"Rate of {row[f'{stat}_rate'] * 36:.1f} {stat} per 36 minutes this season "
             f"(last 10 games: {row[f'{stat}_rate_l10'] * 36:.1f})"]
    if stat == "points":
        pct = (row["opp_def_factor"] - 1) * 100
        if abs(pct) >= 2:
            lines.append(f"Opponent allows {abs(pct):.0f}% {'more' if pct > 0 else 'fewer'} points per possession than league average")
    pace = (row["pace_factor"] - 1) * 100
    if abs(pace) >= 2:
        lines.append(f"Expected game pace {abs(pace):.0f}% {'faster' if pace > 0 else 'slower'} than league average")
    if row["b2b"]:
        lines.append("Team is on the second night of a back-to-back")
    return lines

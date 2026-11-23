"""NFL player prop models: expected passing/rushing/receiving yards, receptions, passing TDs, anytime TD.

Same recipe as the NBA props: a gradient-boosted Poisson regressor on pregame features (snap share,
usage, recent and season production, opponent defense, expected game script), and a Negative
Binomial whose (mean-dependent) dispersion is fitted on held-out games for 'N+' probabilities.
Threshold probabilities are only published if they pass a held-out calibration check.
Baseline: the player's (prior-blended) season average.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingRegressor

from polypicker.domain.markets import ThresholdKind
from polypicker.ml import metrics
from polypicker.ml.distributions import ThresholdProb, count_threshold, dispersion_at, fit_nb_dispersion, normal_over

COMMON = ["games_l8", "season_games", "snap_l3", "snap_l8", "team_struct_pts", "team_epa_matchup", "exp_plays",
          "is_home", "dome", "team_qb_out", "spread_proxy", "questionable", "pos_qb", "pos_rb", "pos_wr", "pos_te"]
SPECIFIC = {
    "passing_yards": ["pass_attempts_l3", "pass_attempts_l8", "passing_yards_season", "passing_yards_l3",
                      "passing_yards_l8", "opp_pass_def"],
    "passing_tds": ["pass_attempts_l3", "pass_attempts_l8", "passing_tds_season", "passing_tds_l8",
                    "passing_yards_season", "opp_pass_def"],
    "rushing_yards": ["carries_l3", "carries_l8", "carry_share", "rushing_yards_season", "rushing_yards_l3",
                      "rushing_yards_l8", "opp_rush_def", "pass_attempts_l8"],
    "receptions": ["targets_l3", "targets_l8", "target_share", "receptions_season", "receptions_l3", "receptions_l8",
                   "receiving_yards_season", "opp_pass_def"],
    "receiving_yards": ["targets_l3", "targets_l8", "target_share", "receptions_season", "receiving_yards_season",
                        "receiving_yards_l3", "receiving_yards_l8", "opp_pass_def"],
    "scrimmage_tds": ["carries_l8", "targets_l8", "carry_share", "target_share", "scrimmage_tds_season",
                      "scrimmage_tds_l8", "rushing_yards_season", "receiving_yards_season", "opp_rush_def",
                      "opp_pass_def"],
}
DEFAULT_AT_LEAST = {
    "passing_yards": [150, 200, 225, 250, 275, 300, 350],
    "passing_tds": [1, 2, 3, 4],
    "rushing_yards": [20, 40, 50, 60, 75, 100, 125],
    "receptions": [2, 3, 4, 5, 6, 7, 8, 10],
    "receiving_yards": [20, 40, 50, 60, 75, 100, 125],
    "scrimmage_tds": [1, 2],
}
NOUN = {"passing_yards": "passing yards", "passing_tds": "passing TDs", "rushing_yards": "rushing yards",
        "receptions": "receptions", "receiving_yards": "receiving yards", "scrimmage_tds": "rushing + receiving TDs"}
THRESHOLD_MAX_ECE = 0.04
MIN_HISTORY = 2
# Passing yards are roughly symmetric (a Negative Binomial fit is pulled wide by early exits): Normal.
DISTRIBUTION = {"passing_yards": "normal"}
# Chosen on the 2025 holdout: heavier regularization helped rushing/receiving, not passing.
HGB = {"default": dict(learning_rate=0.03, max_iter=200, max_leaf_nodes=8, min_samples_leaf=200, l2_regularization=5.0),
       "passing": dict(learning_rate=0.05, max_iter=250, max_leaf_nodes=15, min_samples_leaf=60, l2_regularization=1.0)}


def threshold_prob(dist: dict, kind: ThresholdKind, threshold: float) -> ThresholdProb:
    """P(stat meets a line) from a stored predictive distribution (used by forecasts and the custom-line API)."""
    if dist["type"] == "normal":
        line = threshold - 1 if kind is ThresholdKind.AT_LEAST else threshold   # "k+" == "over k-1" on integers
        tp = normal_over(dist["mean"], dist["sd"], line)
        return ThresholdProb(tp.p_hit, 0.0 if kind is ThresholdKind.AT_LEAST else tp.p_push)
    disp = dist.get("dispersion") or dispersion_at(dist["mean"], dist["r0"], dist["gamma"])
    return count_threshold(dist["mean"], disp, kind, threshold)


def features(stat: str) -> list[str]:
    return COMMON + SPECIFIC[stat]


def _X(df: pl.DataFrame, stat: str) -> np.ndarray:
    return df.select([pl.col(c).cast(pl.Float64) for c in features(stat)]).to_numpy()


def eligible_rows(df: pl.DataFrame, stat: str) -> pl.DataFrame:
    return df.filter(pl.col("eligible").list.contains(stat))


@dataclass
class NflStatModel:
    stat: str
    model: object
    r0: float
    gamma: float
    thresholds_supported: bool
    metrics: dict = field(default_factory=dict)
    version: str = ""
    sigma: float | None = None   # Normal sd for stats modeled as Normal

    @classmethod
    def train(cls, stat: str, data: pl.DataFrame, train_end: date, valid_start: date, valid_end: date) -> "NflStatModel":
        data = eligible_rows(data.filter(pl.col("games_l8") >= MIN_HISTORY), stat)
        tr = data.filter(pl.col("game_date_et") <= train_end)
        va = data.filter((pl.col("game_date_et") >= valid_start) & (pl.col("game_date_et") <= valid_end))
        y_tr = np.clip(tr[f"y_{stat}"].to_numpy().astype(float), 0, None)
        y_va_raw = va[f"y_{stat}"].to_numpy().astype(float)
        y_va = np.clip(y_va_raw, 0, None)

        cfg = HGB["passing" if stat.startswith("passing") else "default"]

        def fit(X, y):
            return HistGradientBoostingRegressor(loss="poisson", random_state=7, **cfg).fit(X, y)

        mdl = fit(_X(tr, stat), y_tr)
        mu = np.clip(mdl.predict(_X(va, stat)), 0.02, None)
        dist_type = DISTRIBUTION.get(stat, "negative_binomial")
        if dist_type == "normal":
            r0, gamma, sigma = 0.0, 0.0, float(np.std(y_va_raw - mu))
        else:
            (r0, gamma), sigma = fit_nb_dispersion(y_va, mu), None
        dist_of = (lambda m: {"type": "normal", "mean": m, "sd": sigma}) if dist_type == "normal" else \
            (lambda m: {"type": "negative_binomial", "mean": m, "r0": r0, "gamma": gamma})
        ps, ys = [], []
        for k in DEFAULT_AT_LEAST[stat]:
            p = np.array([threshold_prob(dist_of(m), ThresholdKind.AT_LEAST, k).p_hit for m in mu])
            keep = (p > 0.03) & (p < 0.97)
            ps.append(p[keep]); ys.append((y_va_raw[keep] >= k).astype(float))
        ps_all, ys_all = np.concatenate(ps), np.concatenate(ys)
        ece = metrics.expected_calibration_error(ps_all, ys_all) if len(ps_all) else float("nan")
        report = {
            "validation_rows": int(va.height), "train_rows": int(tr.height),
            "mae": round(metrics.mae(mu, y_va_raw), 3),
            "mae_baseline_season_avg": round(metrics.mae(va[f"{stat}_season"].to_numpy(), y_va_raw), 3),
            "distribution": ({"type": "normal", "sd": round(sigma, 2)} if dist_type == "normal"
                             else {"type": "negative_binomial", "r0": round(r0, 4), "gamma": round(gamma, 3)}),
            "threshold": metrics.binary_report(ps_all, ys_all) if len(ps_all) else None,
            "calibration_threshold": metrics.calibration_bins(ps_all, ys_all) if len(ps_all) else [],
        }
        full = data.filter(pl.col("game_date_et") <= valid_end)
        final = fit(_X(full, stat), np.clip(full[f"y_{stat}"].to_numpy().astype(float), 0, None))
        return cls(stat, final, r0, gamma, bool(ece <= THRESHOLD_MAX_ECE), report, sigma=sigma)

    def distribution(self, mean: float) -> dict:
        if self.sigma is not None:
            return {"type": "normal", "mean": mean, "sd": self.sigma}
        return {"type": "negative_binomial", "mean": mean, "dispersion": dispersion_at(mean, self.r0, self.gamma),
                "r0": self.r0, "gamma": self.gamma}

    def predict(self, cands: pl.DataFrame) -> np.ndarray:
        return np.clip(self.model.predict(_X(cands, self.stat)), 0.02, None)

    def threshold_rows(self, mean: float) -> list[tuple[ThresholdKind, float, float, float]]:
        out = []
        dist = self.distribution(mean)
        for k in DEFAULT_AT_LEAST[self.stat]:
            tp = threshold_prob(dist, ThresholdKind.AT_LEAST, k)
            if 0.03 < tp.p_hit < 0.97:
                out.append((ThresholdKind.AT_LEAST, float(k), tp.p_hit, tp.p_push))
        if self.stat != "scrimmage_tds":
            line = float(np.floor(mean)) + 0.5
            tp = threshold_prob(dist, ThresholdKind.OVER, line)
            out.append((ThresholdKind.OVER, line, tp.p_hit, tp.p_push))
        return out


def explain_player(row: dict, stat: str, mean: float) -> list[str]:
    """Plain-language summary of the main inputs (no causal claims)."""
    lines = [f"Played {row['snap_l3'] * 100:.0f}% of offensive snaps over the last 3 games"]
    if stat in ("passing_yards", "passing_tds"):
        lines.append(f"Averaging {row['pass_attempts_l3']:.0f} pass attempts over the last 3 games")
    elif stat == "rushing_yards":
        lines.append(f"Averaging {row['carries_l3']:.1f} carries over the last 3 games ({row['carry_share'] * 100:.0f}% of team carries)")
    elif stat in ("receptions", "receiving_yards"):
        lines.append(f"Averaging {row['targets_l3']:.1f} targets over the last 3 games ({row['target_share'] * 100:.0f}% target share)")
    else:
        lines.append(f"{row['carries_l8'] + row['targets_l8']:.1f} carries + targets per game recently")
    lines.append(f"Season average: {row[f'{stat}_season']:.1f} {NOUN[stat]} per game")
    key = "opp_rush_def" if stat == "rushing_yards" else "opp_pass_def"
    pct = (row[key] - 1) * 100
    if abs(pct) >= 5 and stat != "scrimmage_tds":
        kind = "rushing" if key == "opp_rush_def" else "passing"
        lines.append(f"Opponent allows {abs(pct):.0f}% {'more' if pct > 0 else 'fewer'} {kind} yards per game than league average")
    if row["team_qb_out"] and stat != "passing_yards":
        lines.append("Team's starting quarterback is listed Out or Doubtful")
    if row["questionable"]:
        lines.append("Listed Questionable on this week's injury report")
    return lines

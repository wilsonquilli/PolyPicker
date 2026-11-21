"""Game model: projected team scores -> margin/total distributions -> win, spread, total probabilities.

One ridge regression predicts a team's points from its own and its opponent's pregame
state. It's linear on purpose: every projection can be decomposed into plain-language
factors (FR-05.4). Win probability = P(margin > 0) under a Normal whose scale is
calibrated on held-out games, so the moneyline is consistent with the projected score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from polypicker.ml import metrics
from polypicker.ml.distributions import fit_sigma_for_win, normal_over, spread_cover, win_probability
from polypicker.ml.team_features import elo_win_prob

FEATURES = ["struct_pts", "struct_pts_l10", "ortg", "opp_drtg", "ortg_l10", "opp_drtg_l10", "exp_pace",
            "is_home", "rest_days", "opp_rest_days", "b2b", "opp_b2b", "elo_diff", "is_playoffs"]

# Plain-language grouping for explanations (FR-05.4).
FACTOR_GROUPS = {
    "Season efficiency (offense vs. opponent defense)": ["struct_pts", "ortg", "opp_drtg"],
    "Recent form (last 10 games)": ["struct_pts_l10", "ortg_l10", "opp_drtg_l10"],
    "Overall team rating (Elo)": ["elo_diff"],
    "Home court": ["is_home"],
    "Rest and back-to-backs": ["rest_days", "opp_rest_days", "b2b", "opp_b2b"],
    "Pace": ["exp_pace"],
    "Playoff context": ["is_playoffs"],
}

LINE_CALIBRATION_MAX_ECE = 0.04  # spread/total probabilities are published only below this (FR-02.4)
WARMUP_DAYS = 21                 # skip the first weeks of the first season (no prior information)


def design(matchups: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    g = games.select("game_id", "season_type", "home_score", "away_score")
    return (matchups.join(g, on="game_id", how="left")
            .with_columns((pl.col("season_type") == "playoffs").alias("is_playoffs"),
                          pl.when(pl.col("is_home")).then("home_score").otherwise("away_score").alias("y_points")))


def _X(df: pl.DataFrame) -> np.ndarray:
    return df.select([pl.col(c).cast(pl.Float64) for c in FEATURES]).to_numpy()


def _paired(df: pl.DataFrame, pred: np.ndarray) -> pl.DataFrame:
    d = df.select("game_id", "is_home", "y_points", "elo_diff").with_columns(pl.Series("pred", pred))
    h = d.filter(pl.col("is_home")).rename({"pred": "mu_home", "y_points": "home_pts", "elo_diff": "elo_diff_home"})
    a = d.filter(~pl.col("is_home")).select("game_id", pl.col("pred").alias("mu_away"),
                                            pl.col("y_points").alias("away_pts"))
    return h.drop("is_home").join(a, on="game_id")


@dataclass
class GameModel:
    pipeline: object
    sigma_margin: float
    sigma_total: float
    sigma_win: float
    lines_supported: bool
    metrics: dict = field(default_factory=dict)
    version: str = ""

    # ------------------------------------------------------------------ training
    @classmethod
    def train(cls, data: pl.DataFrame, train_end: date, valid_start: date, valid_end: date) -> "GameModel":
        """Fit on [start, train_end], evaluate on [valid_start, valid_end], then refit on everything
        up to valid_end for production use. Distribution scales come from the held-out period."""
        data = data.filter(pl.col("y_points").is_not_null())
        first = data["game_date_et"].min()
        data = data.filter(pl.col("game_date_et") >= first + timedelta(days=WARMUP_DAYS))
        tr = data.filter(pl.col("game_date_et") <= train_end)
        va = data.filter((pl.col("game_date_et") >= valid_start) & (pl.col("game_date_et") <= valid_end))
        pipe = make_pipeline(StandardScaler(), Ridge(alpha=3.0)).fit(_X(tr), tr["y_points"].to_numpy())
        pv = _paired(va, pipe.predict(_X(va)))
        mu_m = (pv["mu_home"] - pv["mu_away"]).to_numpy()
        mu_t = (pv["mu_home"] + pv["mu_away"]).to_numpy()
        m = (pv["home_pts"] - pv["away_pts"]).to_numpy()
        t = (pv["home_pts"] + pv["away_pts"]).to_numpy()
        won = (m > 0).astype(float)
        sigma_m, sigma_t = float(np.std(m - mu_m)), float(np.std(t - mu_t))
        sigma_w = fit_sigma_for_win(mu_m, won)
        p_win = np.array([win_probability(x, sigma_w) for x in mu_m])

        # Baselines (NFR-09.2)
        ptr = _paired(tr, np.zeros(tr.height))
        home_rate = float((ptr["home_pts"] > ptr["away_pts"]).mean())
        p_elo = np.array([elo_win_prob(x) for x in pv["elo_diff_home"].to_numpy()])
        tr_pts = tr["y_points"].to_numpy()

        # Calibration of off-center spread/total lines (+/- 3 / 5 points from the projection)
        sp_p, sp_y, to_p, to_y = [], [], [], []
        for mm, tt, mo, to in zip(mu_m, mu_t, m, t):
            for off in (-3.5, 3.5):
                h = -(np.floor(mm + off) + 0.5)  # half-point handicap for the home side
                sp_p.append(spread_cover(mm, sigma_m, h).p_hit); sp_y.append(float(mo + h > 0))
            for off in (-5.5, 5.5):
                line = np.floor(tt + off) + 0.5
                to_p.append(normal_over(tt, sigma_t, line).p_hit); to_y.append(float(to > line))
        ece_sp = metrics.expected_calibration_error(np.array(sp_p), np.array(sp_y))
        ece_to = metrics.expected_calibration_error(np.array(to_p), np.array(to_y))

        report = {
            "validation_games": int(len(pv)),
            "moneyline": metrics.binary_report(p_win, won),
            "moneyline_baseline_home_rate": metrics.binary_report(np.full(len(won), home_rate), won),
            "moneyline_baseline_elo": metrics.binary_report(p_elo, won),
            "team_points_mae": round(metrics.mae(np.r_[pv["mu_home"], pv["mu_away"]],
                                                 np.r_[pv["home_pts"], pv["away_pts"]]), 3),
            "team_points_mae_baseline_train_mean": round(metrics.mae(np.full(2 * len(pv), tr_pts.mean()),
                                                                     np.r_[pv["home_pts"], pv["away_pts"]]), 3),
            "margin_mae": round(metrics.mae(mu_m, m), 3), "total_mae": round(metrics.mae(mu_t, t), 3),
            "sigma_margin": round(sigma_m, 3), "sigma_total": round(sigma_t, 3), "sigma_win": round(sigma_w, 3),
            "spread_line_ece": round(ece_sp, 4), "total_line_ece": round(ece_to, 4),
            "calibration_moneyline": metrics.calibration_bins(p_win, won),
        }
        lines_ok = ece_sp <= LINE_CALIBRATION_MAX_ECE and ece_to <= LINE_CALIBRATION_MAX_ECE and len(pv) >= 300
        full = data.filter(pl.col("game_date_et") <= valid_end)
        final_pipe = make_pipeline(StandardScaler(), Ridge(alpha=3.0)).fit(_X(full), full["y_points"].to_numpy())
        return cls(final_pipe, sigma_m, sigma_t, sigma_w, lines_ok, report)

    # ------------------------------------------------------------------ inference
    def predict_games(self, rows: pl.DataFrame) -> list[dict]:
        """rows: design frame with both team perspectives for each game to forecast."""
        X = _X(rows)
        pred = self.pipeline.predict(X)
        scaler, ridge = self.pipeline.steps[0][1], self.pipeline.steps[1][1]
        w = ridge.coef_ / scaler.scale_
        contrib = X * w  # per-feature points contribution (relative to zero; differences are what matter)
        d = rows.select("game_id", "team_id", "is_home", "exp_pace").with_columns(pl.Series("mu", pred))
        idx = {(r[0], r[2]): i for i, r in enumerate(d.iter_rows())}
        out = []
        for gid in d["game_id"].unique(maintain_order=True).to_list():
            ih, ia = idx.get((gid, True)), idx.get((gid, False))
            if ih is None or ia is None:
                continue
            mu_h, mu_a = float(pred[ih]), float(pred[ia])
            mu_m = mu_h - mu_a
            out.append({
                "game_id": gid, "home_team_id": d["team_id"][ih], "away_team_id": d["team_id"][ia],
                "mu_home": mu_h, "mu_away": mu_a, "mu_margin": mu_m, "mu_total": mu_h + mu_a,
                "p_home_win": win_probability(mu_m, self.sigma_win),
                "factors": self._factors(contrib[ih] - contrib[ia]),
                "inputs": {c: _num(rows[c][ih]) for c in FEATURES} | {f"away_{c}": _num(rows[c][ia]) for c in FEATURES},
            })
        return out

    def _factors(self, margin_contrib: np.ndarray) -> list[dict]:
        """Group linear contributions to the projected home margin into plain-language factors."""
        out = []
        for name, feats in FACTOR_GROUPS.items():
            v = float(sum(margin_contrib[FEATURES.index(f)] for f in feats))
            if abs(v) >= 0.25:
                out.append({"factor": name, "home_margin_points": round(v, 1),
                            "favors": "home" if v > 0 else "away"})
        return sorted(out, key=lambda x: -abs(x["home_margin_points"]))


def _num(v):
    return float(v) if v is not None else None

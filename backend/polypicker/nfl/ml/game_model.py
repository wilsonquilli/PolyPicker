"""NFL game model: projected team points -> margin/total distributions -> win, spread, total probabilities.

Same design as the NBA model: one ridge regression predicts a team's points from its own and its
opponent's pregame state (linear, so every projection decomposes into plain-language factors), and
P(win) = P(margin > 0) under a Normal whose scale is calibrated on held-out games.

NFL specifics
* Ties exist (rare): moneyline settles as a push on a tie.
* Benchmarked against the betting market's closing line as well as home-rate and Elo baselines --
  the market line is only ever a yardstick, never a model input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import polars as pl
from scipy import stats
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from polypicker.ml import metrics
from polypicker.ml.distributions import fit_sigma_for_win, normal_over, spread_cover, win_probability
from polypicker.nfl.ml.features import elo_win_prob

FEATURES = ["struct_pts", "struct_pts_l4", "epa_matchup", "epa_matchup_l4", "off_epa", "opp_def_epa", "elo_diff",
            "home_field", "rest_days", "opp_rest_days", "short_week", "opp_short_week", "off_bye", "opp_off_bye",
            "qb_out", "opp_qb_out", "dome", "div_game", "is_playoffs", "exp_plays", "league_ppg"]

FACTOR_GROUPS = {
    "Season scoring (offense vs. opponent defense)": ["struct_pts", "league_ppg"],
    "Efficiency per play (EPA)": ["epa_matchup", "off_epa", "opp_def_epa"],
    "Recent form (last 4 games)": ["struct_pts_l4", "epa_matchup_l4"],
    "Overall team rating (Elo)": ["elo_diff"],
    "Home field": ["home_field"],
    "Rest, byes and short weeks": ["rest_days", "opp_rest_days", "short_week", "opp_short_week", "off_bye", "opp_off_bye"],
    "Starting quarterback injury report": ["qb_out", "opp_qb_out"],
    "Venue and game context": ["dome", "div_game", "is_playoffs", "exp_plays"],
}

LINE_CALIBRATION_MAX_ECE = 0.04
MIN_LINE_VALIDATION_GAMES = 250   # one NFL season is ~285 games
MARKET_SIGMA = 13.5               # typical NFL margin sd, used to turn the market spread into a win probability


def design(matchups: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    g = games.select("game_id", "home_score", "away_score", "market_spread_home", "market_total", "kickoff_utc")
    return matchups.join(g, on="game_id", how="left").with_columns(
        pl.when(pl.col("is_home")).then("home_score").otherwise("away_score").alias("y_points"))


def _X(df: pl.DataFrame) -> np.ndarray:
    return df.select([pl.col(c).cast(pl.Float64) for c in FEATURES]).to_numpy()


def _paired(df: pl.DataFrame, pred: np.ndarray) -> pl.DataFrame:
    d = df.select("game_id", "is_home", "y_points", "elo_diff", "market_spread_home", "market_total").with_columns(
        pl.Series("pred", pred))
    h = d.filter(pl.col("is_home")).rename({"pred": "mu_home", "y_points": "home_pts", "elo_diff": "elo_diff_home"})
    a = d.filter(~pl.col("is_home")).select("game_id", pl.col("pred").alias("mu_away"), pl.col("y_points").alias("away_pts"))
    return h.drop("is_home").join(a, on="game_id")


@dataclass
class NflGameModel:
    pipeline: object
    sigma_margin: float
    sigma_total: float
    sigma_win: float
    lines_supported: bool
    metrics: dict = field(default_factory=dict)
    version: str = ""

    @classmethod
    def train(cls, data: pl.DataFrame, train_end: date, valid_start: date, valid_end: date) -> "NflGameModel":
        data = data.filter(pl.col("y_points").is_not_null())
        first_season = data["season"].min()
        data = data.filter(pl.col("season") > first_season)  # first season only seeds team ratings
        tr = data.filter(pl.col("game_date_et") <= train_end)
        va = data.filter((pl.col("game_date_et") >= valid_start) & (pl.col("game_date_et") <= valid_end))
        pipe = make_pipeline(StandardScaler(), Ridge(alpha=10.0)).fit(_X(tr), tr["y_points"].to_numpy())
        pv = _paired(va, pipe.predict(_X(va)))
        mu_m = (pv["mu_home"] - pv["mu_away"]).to_numpy()
        mu_t = (pv["mu_home"] + pv["mu_away"]).to_numpy()
        m = (pv["home_pts"] - pv["away_pts"]).to_numpy()
        t = (pv["home_pts"] + pv["away_pts"]).to_numpy()
        decided = m != 0                                  # ties are pushes for the moneyline
        won = (m > 0).astype(float)
        sigma_m, sigma_t = float(np.std(m - mu_m)), float(np.std(t - mu_t))
        sigma_w = fit_sigma_for_win(mu_m[decided], won[decided])
        p_win = np.array([win_probability(x, sigma_w) for x in mu_m])

        ptr = _paired(tr, np.zeros(tr.height))
        home_rate = float((ptr["home_pts"] > ptr["away_pts"]).mean())
        p_elo = np.array([elo_win_prob(x) for x in pv["elo_diff_home"].to_numpy()])
        mk = pv["market_spread_home"].to_numpy().astype(float)
        has_mk = ~np.isnan(mk) & decided
        p_mkt = stats.norm.sf(0, mk, MARKET_SIGMA)

        sp_p, sp_y, to_p, to_y = [], [], [], []
        for mm, tt, mo, to in zip(mu_m, mu_t, m, t):
            for off in (-3.5, 0.0, 3.5):
                h = -(np.floor(mm + off) + 0.5)
                sp_p.append(spread_cover(mm, sigma_m, h).p_hit); sp_y.append(float(mo + h > 0))
            for off in (-4.5, 0.0, 4.5):
                line = np.floor(tt + off) + 0.5
                to_p.append(normal_over(tt, sigma_t, line).p_hit); to_y.append(float(to > line))
        ece_sp = metrics.expected_calibration_error(np.array(sp_p), np.array(sp_y))
        ece_to = metrics.expected_calibration_error(np.array(to_p), np.array(to_y))
        mt = pv["market_total"].to_numpy().astype(float)
        has_mt = ~np.isnan(mt)
        report = {
            "validation_games": int(len(pv)), "validation_ties": int((~decided).sum()),
            "moneyline": metrics.binary_report(p_win[decided], won[decided]),
            "moneyline_baseline_home_rate": metrics.binary_report(np.full(decided.sum(), home_rate), won[decided]),
            "moneyline_baseline_elo": metrics.binary_report(p_elo[decided], won[decided]),
            "moneyline_benchmark_market": (metrics.binary_report(p_mkt[has_mk], won[has_mk]) if has_mk.any() else None),
            "moneyline_model_same_games_as_market": (metrics.binary_report(p_win[has_mk], won[has_mk]) if has_mk.any() else None),
            "team_points_mae": round(metrics.mae(np.r_[pv["mu_home"], pv["mu_away"]], np.r_[pv["home_pts"], pv["away_pts"]]), 3),
            "team_points_mae_baseline_train_mean": round(metrics.mae(np.full(2 * len(pv), tr["y_points"].mean()),
                                                                     np.r_[pv["home_pts"], pv["away_pts"]]), 3),
            "margin_mae": round(metrics.mae(mu_m, m), 3), "total_mae": round(metrics.mae(mu_t, t), 3),
            "margin_mae_benchmark_market": round(metrics.mae(mk[~np.isnan(mk)], m[~np.isnan(mk)]), 3) if (~np.isnan(mk)).any() else None,
            "total_mae_benchmark_market": round(metrics.mae(mt[has_mt], t[has_mt]), 3) if has_mt.any() else None,
            "sigma_margin": round(sigma_m, 3), "sigma_total": round(sigma_t, 3), "sigma_win": round(sigma_w, 3),
            "spread_line_ece": round(ece_sp, 4), "total_line_ece": round(ece_to, 4),
            "calibration_moneyline": metrics.calibration_bins(p_win[decided], won[decided]),
        }
        lines_ok = (ece_sp <= LINE_CALIBRATION_MAX_ECE and ece_to <= LINE_CALIBRATION_MAX_ECE
                    and len(pv) >= MIN_LINE_VALIDATION_GAMES)
        full = data.filter(pl.col("game_date_et") <= valid_end)
        final = make_pipeline(StandardScaler(), Ridge(alpha=10.0)).fit(_X(full), full["y_points"].to_numpy())
        return cls(final, sigma_m, sigma_t, sigma_w, lines_ok, report)

    def predict_games(self, rows: pl.DataFrame) -> list[dict]:
        X = _X(rows)
        pred = self.pipeline.predict(X)
        scaler, ridge = self.pipeline.steps[0][1], self.pipeline.steps[1][1]
        contrib = X * (ridge.coef_ / scaler.scale_)
        d = rows.select("game_id", "team_id", "is_home")
        idx = {(r[0], r[2]): i for i, r in enumerate(d.iter_rows())}
        out = []
        for gid in d["game_id"].unique(maintain_order=True).to_list():
            ih, ia = idx.get((gid, True)), idx.get((gid, False))
            if ih is None or ia is None:
                continue
            mu_h, mu_a = float(pred[ih]), float(pred[ia])
            out.append({
                "game_id": gid, "home_team_id": d["team_id"][ih], "away_team_id": d["team_id"][ia],
                "mu_home": mu_h, "mu_away": mu_a, "mu_margin": mu_h - mu_a, "mu_total": mu_h + mu_a,
                "p_home_win": win_probability(mu_h - mu_a, self.sigma_win),
                "p_tie": float(stats.norm.cdf(0.5, mu_h - mu_a, self.sigma_margin) - stats.norm.cdf(-0.5, mu_h - mu_a, self.sigma_margin)),
                "factors": self._factors(contrib[ih] - contrib[ia]),
                "qb_out": {"home": bool(rows["qb_out"][ih]), "away": bool(rows["qb_out"][ia])},
            })
        return out

    def _factors(self, margin_contrib: np.ndarray) -> list[dict]:
        out = []
        for name, feats in FACTOR_GROUPS.items():
            v = float(sum(margin_contrib[FEATURES.index(f)] for f in feats))
            if abs(v) >= 0.25:
                out.append({"factor": name, "home_margin_points": round(v, 1), "favors": "home" if v > 0 else "away"})
        return sorted(out, key=lambda x: -abs(x["home_margin_points"]))

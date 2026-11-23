"""Soccer match model: expected goals per team (Poisson regression) -> Dixon-Coles scoreline grid ->
home / draw / away, likely scores, total goals and both-teams-to-score probabilities.

* Each team's goals ~ Poisson(lambda), log(lambda) linear in pregame features (so projections decompose
  into plain-language factors).
* Dixon-Coles: independent Poissons under-predict 0-0 / 1-1 and over-predict 1-0 / 0-1; one parameter
  (rho), fitted on held-out matches, corrects the four low scorelines.
* Validated against historical H/D/A frequencies and an Elo-only model; totals/BTTS probabilities are
  published only if held-out calibration error <= 0.04.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import polars as pl
from scipy import stats
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from polypicker.ml import metrics

FEATURES = ["goal_matchup", "sot_matchup", "shot_matchup", "season_matchup", "gf_ew", "opp_ga_ew", "tf_ew", "opp_ta_ew",
            "elo_diff", "home", "rest_days", "opp_rest_days", "promoted", "opp_promoted", "league_goals"]
ELO_FEATURES = ["elo_diff", "home", "league_goals"]
FACTOR_GROUPS = {
    "Recent scoring vs. opponent's defence": ["goal_matchup", "gf_ew", "opp_ga_ew"],
    "Chance creation (shots on target)": ["sot_matchup", "tf_ew", "opp_ta_ew"],
    "Shot volume": ["shot_matchup"],
    "Season-long goal record": ["season_matchup"],
    "Overall team rating (Elo)": ["elo_diff"],
    "Home advantage": ["home"],
    "Rest days": ["rest_days", "opp_rest_days"],
    "Newly promoted": ["promoted", "opp_promoted"],
}
MAX_GOALS = 10
TOTAL_LINES = (0.5, 1.5, 2.5, 3.5, 4.5)
CALIBRATION_MAX_ECE = 0.04


def design(matchups: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    g = games.select("game_id", "home_score", "away_score", "kickoff_utc")
    return matchups.join(g, on="game_id", how="left").with_columns(
        pl.when(pl.col("is_home")).then("home_score").otherwise("away_score").alias("y_goals"))


def _X(df: pl.DataFrame, feats=FEATURES) -> np.ndarray:
    return df.select([pl.col(c).cast(pl.Float64) for c in feats]).to_numpy()


def score_grid(lh: float, la: float, rho: float) -> np.ndarray:
    """P(home = i, away = j) for i, j in 0..MAX_GOALS with the Dixon-Coles low-score adjustment."""
    k = np.arange(MAX_GOALS + 1)
    g = np.outer(stats.poisson.pmf(k, lh), stats.poisson.pmf(k, la))
    g[0, 0] *= 1 - lh * la * rho
    g[0, 1] *= 1 + lh * rho
    g[1, 0] *= 1 + la * rho
    g[1, 1] *= 1 - rho
    g = np.clip(g, 0, None)
    return g / g.sum()


def outcome_probs(grid: np.ndarray) -> dict:
    n = grid.shape[0]
    i, j = np.indices(grid.shape)
    tot = i + j
    out = {"home": float(grid[i > j].sum()), "draw": float(np.trace(grid)), "away": float(grid[i < j].sum()),
           "btts": float(grid[1:, 1:].sum())}
    for line in TOTAL_LINES:
        out[f"over_{line}"] = float(grid[tot > line].sum())
    flat = [(float(grid[a, b]), a, b) for a in range(n) for b in range(n)]
    out["top_scores"] = [{"home": a, "away": b, "p": p} for p, a, b in sorted(flat, reverse=True)[:8]]
    return out


def _paired(df: pl.DataFrame, mu: np.ndarray) -> pl.DataFrame:
    d = df.select("game_id", "is_home", "y_goals").with_columns(pl.Series("mu", mu))
    h = d.filter(pl.col("is_home")).select("game_id", pl.col("mu").alias("lh"), pl.col("y_goals").alias("hg"))
    a = d.filter(~pl.col("is_home")).select("game_id", pl.col("mu").alias("la"), pl.col("y_goals").alias("ag"))
    return h.join(a, on="game_id")


def fit_rho(lh: np.ndarray, la: np.ndarray, hg: np.ndarray, ag: np.ndarray) -> float:
    best, best_ll = 0.0, -np.inf
    for rho in np.linspace(-0.25, 0.25, 101):
        ll = 0.0
        for x, y, h, a in zip(lh, la, hg, ag):
            g = score_grid(x, y, rho)
            ll += np.log(max(g[min(h, MAX_GOALS), min(a, MAX_GOALS)], 1e-12))
        if ll > best_ll:
            best, best_ll = float(rho), ll
    return best


def rps(p: np.ndarray, y: np.ndarray) -> float:
    """Ranked probability score for ordered outcomes (home, draw, away); lower is better."""
    cp, cy = np.cumsum(p, axis=1)[:, :2], np.cumsum(y, axis=1)[:, :2]
    return float(np.mean(np.sum((cp - cy) ** 2, axis=1) / 2))


def three_way_report(p: np.ndarray, y: np.ndarray) -> dict:
    eps = 1e-12
    return {"n": int(len(p)), "rps": round(rps(p, y), 5),
            "log_loss": round(float(-np.mean(np.log(np.clip((p * y).sum(1), eps, 1)))), 5),
            "brier": round(float(np.mean(np.sum((p - y) ** 2, axis=1))), 5),
            "accuracy": round(float(np.mean(p.argmax(1) == y.argmax(1))), 4)}


def _fit(X, y):
    return make_pipeline(StandardScaler(), PoissonRegressor(alpha=0.02, max_iter=500)).fit(X, y)


@dataclass
class SoccerModel:
    pipeline: object
    rho: float
    lines_supported: bool
    metrics: dict = field(default_factory=dict)
    version: str = ""

    @classmethod
    def train(cls, data: pl.DataFrame, train_end: date, valid_start: date, valid_end: date) -> "SoccerModel":
        data = data.filter(pl.col("y_goals").is_not_null() & (pl.col("season") > data["season"].min()))
        tr = data.filter(pl.col("game_date") <= train_end)
        va = data.filter((pl.col("game_date") >= valid_start) & (pl.col("game_date") <= valid_end))
        pipe = _fit(_X(tr), tr["y_goals"].to_numpy())
        pv = _paired(va, pipe.predict(_X(va)))
        lh, la, hg, ag = (pv[c].to_numpy() for c in ("lh", "la", "hg", "ag"))
        rho = fit_rho(lh, la, hg.astype(int), ag.astype(int))
        probs = [outcome_probs(score_grid(x, z, rho)) for x, z in zip(lh, la)]
        P = np.array([[o["home"], o["draw"], o["away"]] for o in probs])
        Y = np.stack([(hg > ag), (hg == ag), (hg < ag)], axis=1).astype(float)

        ptr = _paired(tr, np.zeros(tr.height))
        freq = np.array([(ptr["hg"] > ptr["ag"]).mean(), (ptr["hg"] == ptr["ag"]).mean(), (ptr["hg"] < ptr["ag"]).mean()])
        elo = _fit(_X(tr, ELO_FEATURES), tr["y_goals"].to_numpy())
        pe = _paired(va, elo.predict(_X(va, ELO_FEATURES)))
        Pe = np.array([[o["home"], o["draw"], o["away"]] for o in
                       (outcome_probs(score_grid(x, z, rho)) for x, z in zip(pe["lh"].to_numpy(), pe["la"].to_numpy()))])

        tot = hg + ag
        ece = {}
        for line in (1.5, 2.5, 3.5):
            ece[f"over_{line}"] = metrics.expected_calibration_error(np.array([o[f"over_{line}"] for o in probs]), (tot > line).astype(float))
        ece["btts"] = metrics.expected_calibration_error(np.array([o["btts"] for o in probs]), ((hg > 0) & (ag > 0)).astype(float))
        report = {
            "validation_matches": int(len(pv)),
            "match_result": three_way_report(P, Y),
            "match_result_baseline_frequencies": three_way_report(np.tile(freq, (len(P), 1)), Y),
            "match_result_baseline_elo": three_way_report(Pe, Y),
            "draw_rate_actual": round(float(Y[:, 1].mean()), 4), "draw_rate_predicted": round(float(P[:, 1].mean()), 4),
            "goals_mae": round(metrics.mae(np.r_[lh, la], np.r_[hg, ag]), 3),
            "goals_mae_baseline_train_mean": round(metrics.mae(np.full(2 * len(pv), tr["y_goals"].mean()), np.r_[hg, ag]), 3),
            "rho": round(rho, 4), "line_ece": {k: round(v, 4) for k, v in ece.items()},
            "calibration_home_win": metrics.calibration_bins(P[:, 0], Y[:, 0]),
            "calibration_draw": metrics.calibration_bins(P[:, 1], Y[:, 1]),
        }
        lines_ok = all(v <= CALIBRATION_MAX_ECE for v in ece.values())
        full = data.filter(pl.col("game_date") <= valid_end)
        return cls(_fit(_X(full), full["y_goals"].to_numpy()), rho, lines_ok, report)

    def predict_games(self, rows: pl.DataFrame) -> list[dict]:
        X = _X(rows)
        mu = self.pipeline.predict(X)
        scaler, glm = self.pipeline.steps[0][1], self.pipeline.steps[1][1]
        contrib = X * (glm.coef_ / scaler.scale_)          # log-goal contributions
        d = rows.select("game_id", "team_id", "is_home")
        idx = {(r[0], r[2]): i for i, r in enumerate(d.iter_rows())}
        out = []
        for gid in d["game_id"].unique(maintain_order=True).to_list():
            ih, ia = idx.get((gid, True)), idx.get((gid, False))
            if ih is None or ia is None:
                continue
            lh, la = float(mu[ih]), float(mu[ia])
            o = outcome_probs(score_grid(lh, la, self.rho))
            out.append({"game_id": gid, "home_team_id": d["team_id"][ih], "away_team_id": d["team_id"][ia],
                        "xg_home": lh, "xg_away": la, **o,
                        "factors": self._factors(contrib[ih] - contrib[ia], lh, la)})
        return out

    def _factors(self, diff: np.ndarray, lh: float, la: float) -> list[dict]:
        """Each factor's effect on the expected goal difference (log-scale contributions, scaled to goals)."""
        scale = (lh + la) / 2
        out = []
        for name, feats in FACTOR_GROUPS.items():
            v = float(sum(diff[FEATURES.index(f)] for f in feats)) * scale
            if abs(v) >= 0.03:
                out.append({"factor": name, "home_goal_edge": round(v, 2), "favors": "home" if v > 0 else "away"})
        return sorted(out, key=lambda x: -abs(x["home_goal_edge"]))

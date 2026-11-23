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

LEGACY_FEATURES = ["struct_pts", "struct_pts_l10", "ortg", "opp_drtg", "ortg_l10", "opp_drtg_l10", "exp_pace",
                   "is_home", "rest_days", "opp_rest_days", "b2b", "opp_b2b", "elo_diff", "is_playoffs"]
# v2 adds player-based lineup strength + availability (polypicker/ml/lineup_features.py). Validated on
# 2024-25 and 2025-26: log loss 0.608 -> 0.593 and 0.598 -> 0.586; accuracy 66.7 -> 68.9% and 68.2 -> 68.3%.
LINEUP = ["lineup_pm", "opp_lineup_pm", "lineup_gs", "opp_lineup_gs", "missing_pm", "opp_missing_pm",
          "missing_min", "opp_missing_min"]
FEATURES = LEGACY_FEATURES + LINEUP
# v3: when a game has a betting line, a second model also sees the market (sportsbook spread/total and
# Polymarket price, as an expected margin + half the total). Games without a line use the v2 model.
MARKET = ["mkt_margin", "mkt_total_half"]
# Tested 2026-11-21 (2024-25 / 2025-26 hold-outs + 2026-27 to date): market + injuries matched the best
# variant (market alone) and beat market + every v2 input, which over-fits on ~2 seasons of lines.
MARKET_MODEL = ["is_home", "missing_pm", "opp_missing_pm"] + MARKET

# Plain-language grouping for explanations (FR-05.4).
FACTOR_GROUPS = {
    "Season efficiency (offense vs. opponent defense)": ["struct_pts", "ortg", "opp_drtg"],
    "Recent form (last 10 games)": ["struct_pts_l10", "ortg_l10", "opp_drtg_l10"],
    "Overall team rating (Elo)": ["elo_diff"],
    "Home court": ["is_home"],
    "Rest and back-to-backs": ["rest_days", "opp_rest_days", "b2b", "opp_b2b"],
    "Pace": ["exp_pace"],
    "Playoff context": ["is_playoffs"],
    "Player ratings of tonight's lineup": ["lineup_pm", "opp_lineup_pm", "lineup_gs", "opp_lineup_gs"],
    "Injuries and absences": ["missing_pm", "opp_missing_pm", "missing_min", "opp_missing_min"],
    "Betting markets (sportsbooks + Polymarket)": ["mkt_margin", "mkt_total_half"],
}

LINE_CALIBRATION_MAX_ECE = 0.04  # spread/total probabilities are published only below this (FR-02.4)
WARMUP_DAYS = 21                 # skip the first weeks of the first season (no prior information)


def design(matchups: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    g = games.select("game_id", "season_type", "home_score", "away_score")
    return (matchups.join(g, on="game_id", how="left")
            .with_columns((pl.col("season_type") == "playoffs").alias("is_playoffs"),
                          pl.when(pl.col("is_home")).then("home_score").otherwise("away_score").alias("y_points")))


def _X(df: pl.DataFrame, features: list[str] | None = None) -> np.ndarray:
    return df.select([pl.col(c).cast(pl.Float64) for c in (features or FEATURES)]).to_numpy()


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
    features: list | None = None  # None = models pickled before v2 (LEGACY_FEATURES)

    @property
    def feature_list(self) -> list[str]:
        return self.features or LEGACY_FEATURES

    # ------------------------------------------------------------------ training
    @classmethod
    def train(cls, data: pl.DataFrame, train_end: date, valid_start: date, valid_end: date,
              features: list[str] | None = None) -> "GameModel":
        """Fit on [start, train_end], evaluate on [valid_start, valid_end], then refit on everything
        up to valid_end for production use. Distribution scales come from the held-out period."""
        data = data.filter(pl.col("y_points").is_not_null())
        first = data["game_date_et"].min()
        data = data.filter(pl.col("game_date_et") >= first + timedelta(days=WARMUP_DAYS))
        tr = data.filter(pl.col("game_date_et") <= train_end)
        va = data.filter((pl.col("game_date_et") >= valid_start) & (pl.col("game_date_et") <= valid_end))
        feats = list(features or FEATURES)
        pipe = make_pipeline(StandardScaler(), Ridge(alpha=3.0)).fit(_X(tr, feats), tr["y_points"].to_numpy())
        pv = _paired(va, pipe.predict(_X(va, feats)))
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

        market_report = {}
        for col, key in (("market_p_home", "vs_market"), ("polymarket_p_home", "vs_polymarket")):
            if col not in va.columns:
                continue
            mk = (pv.select("game_id").with_columns(pl.Series("p_model", p_win), pl.Series("won", won))
                  .join(va.filter(pl.col("is_home")).select("game_id", col), on="game_id")
                  .filter(pl.col(col).is_not_null()))
            if mk.height >= 100:
                y = mk["won"].to_numpy()
                market_report[key] = {"n": mk.height, "model": metrics.binary_report(mk["p_model"].to_numpy(), y),
                                      "market": metrics.binary_report(mk[col].to_numpy(), y)}

        report = {
            **market_report,
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
        final_pipe = make_pipeline(StandardScaler(), Ridge(alpha=3.0)).fit(_X(full, feats), full["y_points"].to_numpy())
        gm = cls(final_pipe, sigma_m, sigma_t, sigma_w, lines_ok, report, features=feats)
        val = pv.select("game_id").with_columns(pl.Series("p_win", p_win), pl.Series("won", won),
                                                 pl.Series("elo_p", p_elo))
        extra = [c for c in ("market_p_home", "polymarket_p_home") if c in va.columns]
        if extra:
            val = val.join(va.filter(pl.col("is_home")).select("game_id", *extra), on="game_id", how="left")
        gm._val = val  # validation predictions (not part of the persisted model's behaviour)
        return gm

    # ------------------------------------------------------------------ inference
    def predict_games(self, rows: pl.DataFrame) -> list[dict]:
        """rows: design frame with both team perspectives for each game to forecast."""
        feats = self.feature_list
        X = _X(rows, feats)
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
                "sigma_win": self.sigma_win, "sigma_margin": self.sigma_margin, "sigma_total": self.sigma_total,
                "lines_supported": self.lines_supported,
                "factors": self._factors(contrib[ih] - contrib[ia]),
                "inputs": {c: _num(rows[c][ih]) for c in feats} | {f"away_{c}": _num(rows[c][ia]) for c in feats},
            })
        return out

    def _factors(self, margin_contrib: np.ndarray) -> list[dict]:
        """Group linear contributions to the projected home margin into plain-language factors."""
        out = []
        fl = self.feature_list
        for name, feats in FACTOR_GROUPS.items():
            present = [f for f in feats if f in fl]
            if not present:
                continue
            v = float(sum(margin_contrib[fl.index(f)] for f in present))
            if abs(v) >= 0.25:
                out.append({"factor": name, "home_margin_points": round(v, 1),
                            "favors": "home" if v > 0 else "away"})
        return sorted(out, key=lambda x: -abs(x["home_margin_points"]))


def _market_report(val: pl.DataFrame) -> dict:
    out = {}
    for col, key in (("market_p_home", "vs_market"), ("polymarket_p_home", "vs_polymarket")):
        if col not in val.columns:
            continue
        mk = val.filter(pl.col(col).is_not_null())
        if mk.height >= 100:
            y = mk["won"].to_numpy()
            out[key] = {"n": mk.height, "model": metrics.binary_report(mk["p_win"].to_numpy(), y),
                        "market": metrics.binary_report(mk[col].to_numpy(), y)}
    return out


@dataclass
class MarketAwareGameModel:
    """v3: the market model for games with a betting line, the v2 model otherwise."""
    base: GameModel
    market: GameModel | None
    metrics: dict = field(default_factory=dict)
    version: str = ""

    # pipeline-facing defaults (per-game values come back from predict_games)
    @property
    def sigma_win(self) -> float: return self.base.sigma_win
    @property
    def sigma_margin(self) -> float: return self.base.sigma_margin
    @property
    def sigma_total(self) -> float: return self.base.sigma_total
    @property
    def lines_supported(self) -> bool: return self.base.lines_supported
    @property
    def features(self) -> list[str]: return self.base.feature_list + MARKET

    @staticmethod
    def has_market(df: pl.DataFrame) -> pl.Expr:
        return pl.all_horizontal([pl.col(c).is_not_null() & pl.col(c).is_not_nan() for c in MARKET])

    @classmethod
    def train(cls, data: pl.DataFrame, train_end: date, valid_start: date, valid_end: date) -> "MarketAwareGameModel":
        base = GameModel.train(data, train_end, valid_start, valid_end, FEATURES)
        if not all(c in data.columns for c in MARKET):
            return cls(base, None, base.metrics)
        # a game counts only if both team rows carry the market
        ok = (data.filter(cls.has_market(data)).group_by("game_id").len().filter(pl.col("len") == 2)["game_id"])
        mdata = data.filter(pl.col("game_id").is_in(ok.implode()))
        market = GameModel.train(mdata, train_end, valid_start, valid_end, MARKET_MODEL)
        # combined validation: market model where a line existed, v2 elsewhere
        mv = market._val
        bv = base._val.filter(~pl.col("game_id").is_in(mv["game_id"].implode()))
        val = pl.concat([mv, bv], how="diagonal_relaxed")
        y = val["won"].to_numpy()
        report = {**base.metrics, **_market_report(val),
                  "validation_games": val.height,
                  "moneyline": metrics.binary_report(val["p_win"].to_numpy(), y),
                  "moneyline_baseline_elo": metrics.binary_report(val["elo_p"].to_numpy(), y),
                  "calibration_moneyline": metrics.calibration_bins(val["p_win"].to_numpy(), y),
                  "games_with_market": mv.height,
                  "base_model": {"moneyline": base.metrics["moneyline"], "margin_mae": base.metrics["margin_mae"]},
                  "market_model": {"moneyline": market.metrics["moneyline"], "margin_mae": market.metrics["margin_mae"],
                                   "validation_games": market.metrics["validation_games"]}}
        return cls(base, market, report)

    def predict_games(self, rows: pl.DataFrame) -> list[dict]:
        if self.market is None or not all(c in rows.columns for c in MARKET):
            return [{**g, "uses_market": False} for g in self.base.predict_games(rows)]
        ok = (rows.filter(self.has_market(rows)).group_by("game_id").len().filter(pl.col("len") == 2)["game_id"])
        with_m = rows.filter(pl.col("game_id").is_in(ok.implode()))
        without = rows.filter(~pl.col("game_id").is_in(ok.implode()))
        out = [{**g, "uses_market": True} for g in self.market.predict_games(with_m)] if with_m.height else []
        out += [{**g, "uses_market": False} for g in self.base.predict_games(without)] if without.height else []
        return out


def _num(v):
    return float(v) if v is not None else None

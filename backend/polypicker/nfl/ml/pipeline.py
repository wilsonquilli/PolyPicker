"""NFL glue: features from the DB, train + register models, write immutable forecasts, backtest.

Mirrors the NBA pipeline (same registry, run tracking, revision and input-hash semantics); forecasts
go to ``nfl_predictions``. Kept off the web request path.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import polars as pl
from sqlalchemy import Engine, func, insert, select

from polypicker.config import get_settings
from polypicker.db import models as m
from polypicker.domain.games import ET
from polypicker.domain.markets import Market, ThresholdKind
from polypicker.ml.distributions import normal_over, spread_cover
from polypicker.ml.pipeline import _register, finish_run, load_model, start_run
from polypicker.nfl import models as nm
from polypicker.nfl.markets import PLAYER_MARKETS, ROLLOUT, game_label, moneyline_label, player_label
from polypicker.nfl.ml import data as ndata
from polypicker.nfl.ml.features import STATS, build_player_features, build_team_features, matchup_frame
from polypicker.nfl.ml.game_model import MarketAwareNflGameModel, NflGameModel, design
from polypicker.nfl.ml.player_model import NflStatModel, explain_player

log = logging.getLogger(__name__)
TARGET_GAME = "nfl_game"


def target(stat: str) -> str:
    return f"nfl_player_{stat}"


@dataclass
class FeatureSet:
    games: pl.DataFrame
    game_design: pl.DataFrame
    player_train: pl.DataFrame
    player_cands: pl.DataFrame


def build_features(engine: Engine) -> FeatureSet:
    games = ndata.load_games(engine)
    tb, pb, inj = ndata.load_team_box(engine), ndata.load_player_box(engine), ndata.load_injuries(engine)
    mf = matchup_frame(build_team_features(games, tb, pb, inj))
    ptrain, pcands = build_player_features(games, pb, mf, inj, ndata.load_roster_weeks(engine))
    return FeatureSet(games, design(mf, games), ptrain, pcands)


def _season_bounds(games: pl.DataFrame, season: int) -> tuple[date, date]:
    s = games.filter(pl.col("season") == season)
    return s["game_date_et"].min(), s["game_date_et"].max()


def train_models(engine: Engine, fs: FeatureSet, validation_season: int, *, activate: bool, tag: str = "") -> dict[str, int]:
    """Train on seasons < validation_season, validate on it, refit on both, register."""
    v_start, v_end = _season_bounds(fs.games, validation_season)
    train_end = v_start - timedelta(days=1)
    t_start = fs.games["game_date_et"].min()
    version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + (f"-{tag}" if tag else "")
    art = get_settings().resolved_artifacts_dir()
    art.mkdir(parents=True, exist_ok=True)
    ids: dict[str, int] = {}
    gm = MarketAwareNflGameModel.train(fs.game_design, train_end, v_start, v_end)
    gm.version = version
    ids["game"] = _register(engine, TARGET_GAME, version, "ridge+normal+market", gm, art, t_start, v_end, v_start, v_end,
                            {"features": gm.features, "lines_supported": gm.lines_supported}, gm.metrics, activate)
    for stat in STATS:
        sm = NflStatModel.train(stat, fs.player_train, train_end, v_start, v_end)
        sm.version = version
        ids[stat] = _register(engine, target(stat), version, f"hgb_poisson+{sm.metrics['distribution']['type']}", sm, art,
                              t_start, v_end, v_start, v_end, {"thresholds_supported": sm.thresholds_supported},
                              sm.metrics, activate)
    return ids


def active_model_ids(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        rows = conn.execute(select(m.Model.target, m.Model.id).where(m.Model.is_active, m.Model.target.like("nfl_%"))).all()
    ids = {("game" if t == TARGET_GAME else t.removeprefix("nfl_player_")): i for t, i in rows}
    missing = {"game", *STATS} - ids.keys()
    if missing:
        raise RuntimeError(f"no active NFL model for {sorted(missing)}; run `polypicker nfl train` first")
    return ids


# ---------------------------------------------------------------- forecasting

def forecast(engine: Engine, fs: FeatureSet, model_ids: dict[str, int], game_ids: list[int], *, kind: str,
             data_cutoff: datetime, run_id: int, skip_unchanged: bool = True) -> dict:
    gm: NflGameModel | MarketAwareNflGameModel = load_model(engine, model_ids["game"])
    sms: dict[str, NflStatModel] = {s: load_model(engine, model_ids[s]) for s in STATS}
    gd = fs.game_design.filter(pl.col("game_id").is_in(game_ids))
    game_preds = {p["game_id"]: p for p in gm.predict_games(gd)}
    cands = fs.player_cands.filter(pl.col("game_id").is_in(game_ids))
    means = {s: (sms[s].predict(cands) if cands.height else []) for s in STATS}
    cand_rows = list(cands.iter_rows(named=True))
    by_game: dict[int, list[int]] = {}
    for i, r in enumerate(cand_rows):
        by_game.setdefault(r["game_id"], []).append(i)

    P = nm.NflPrediction
    with engine.connect() as conn:
        last_rev = dict(conn.execute(select(P.game_id, func.max(P.revision))
                                     .where(P.kind == kind, P.game_id.in_(game_ids)).group_by(P.game_id)).all())
        last_hash = dict(conn.execute(
            select(P.game_id, P.details["input_hash"].astext)
            .where(P.kind == kind, P.game_id.in_(game_ids), P.market == Market.MONEYLINE.value, P.side == "home")
            .order_by(P.game_id, P.revision.desc()).distinct(P.game_id)).all())

    out_rows: list[dict] = []
    written = skipped = 0
    for gid, gp in game_preds.items():
        payload = [(cand_rows[i], {s: float(means[s][i]) for s in STATS}) for i in by_game.get(gid, [])]
        h = hashlib.sha1(json.dumps({
            "g": [round(gp["mu_home"], 2), round(gp["mu_away"], 2)],
            "p": sorted((r["player_id"], r["availability"], *[round(v[s], 1) for s in r["eligible"]]) for r, v in payload),
            "m": model_ids}, sort_keys=True, default=str).encode()).hexdigest()
        if skip_unchanged and last_hash.get(gid) == h:
            skipped += 1
            continue
        rev = (last_rev.get(gid) or 0) + 1
        base = {"run_id": run_id, "kind": kind, "game_id": gid, "revision": rev, "data_cutoff_utc": data_cutoff}
        out_rows += _game_rows(gp, gm, model_ids["game"], base, h)
        for row, mean in payload:
            for s in row["eligible"]:
                out_rows += _player_rows(row, s, mean[s], sms[s], model_ids[s], base)
        written += 1
    if out_rows:
        with engine.begin() as conn:
            for i in range(0, len(out_rows), 2000):
                conn.execute(insert(P), out_rows[i:i + 2000])
    return {"games_written": written, "games_unchanged": skipped, "prediction_rows": len(out_rows)}


def _game_rows(gp: dict, gm: NflGameModel | MarketAwareNflGameModel, model_id: int, base: dict,
               input_hash: str) -> list[dict]:
    rows = []
    # per-game scales: games with a betting line come from the market model, which has its own
    sig_w, sig_m, sig_t = (gp.get("sigma_win", gm.sigma_win), gp.get("sigma_margin", gm.sigma_margin),
                           gp.get("sigma_total", gm.sigma_total))
    lines_ok = gp.get("lines_supported", gm.lines_supported)
    common = {"factors": gp["factors"], "model_version": gm.version, "qb_out": gp["qb_out"],
              "uses_market": bool(gp.get("uses_market", False))}
    p_home = gp["p_home_win"]
    for side, team, p in (("home", gp["home_team_id"], p_home), ("away", gp["away_team_id"], 1 - p_home)):
        rows.append({**base, "model_id": model_id, "market": Market.MONEYLINE.value, "subject_type": "team",
                     "subject_id": str(team), "side": side, "threshold_kind": None, "threshold": None,
                     "probability": p, "push_probability": gp["p_tie"], "projection": None,
                     "label": moneyline_label(side),
                     "details": ({**common, "input_hash": input_hash,
                                  "distribution": {"type": "normal", "mu_margin_home": gp["mu_margin"],
                                                   "sigma_win": sig_w}} if side == "home" else common)})
    for side, team, mu in (("home", gp["home_team_id"], gp["mu_home"]), ("away", gp["away_team_id"], gp["mu_away"])):
        rows.append({**base, "model_id": model_id, "market": Market.TEAM_POINTS.value, "subject_type": "team",
                     "subject_id": str(team), "side": side, "threshold_kind": None, "threshold": None,
                     "probability": None, "push_probability": None, "projection": mu,
                     "label": f"Projected {side} points", "details": None})
    dist = {"lines_supported": lines_ok, "sigma_margin": sig_m, "sigma_total": sig_t,
            "mu_margin_home": gp["mu_margin"], "mu_total": gp["mu_total"], "type": "normal_integer"}
    for mk, proj, lbl in ((Market.SPREAD, gp["mu_margin"], "Projected home margin"),
                          (Market.TOTAL, gp["mu_total"], "Projected total points")):
        rows.append({**base, "model_id": model_id, "market": mk.value, "subject_type": "game", "subject_id": None,
                     "side": None, "threshold_kind": None, "threshold": None, "probability": None,
                     "push_probability": None, "projection": proj, "label": lbl, "details": dist})
    if lines_ok:
        center = float(int(gp["mu_margin"] // 1)) + 0.5
        for line in (center - 3, center, center + 3):
            tp = spread_cover(gp["mu_margin"], sig_m, -line)
            rows.append({**base, "model_id": model_id, "market": Market.SPREAD.value, "subject_type": "team",
                         "subject_id": str(gp["home_team_id"]), "side": "home",
                         "threshold_kind": ThresholdKind.HANDICAP.value, "threshold": -line, "probability": tp.p_hit,
                         "push_probability": tp.p_push, "projection": gp["mu_margin"],
                         "label": game_label(Market.SPREAD, ThresholdKind.HANDICAP, -line, "home"), "details": None})
        tcenter = float(int(gp["mu_total"] // 1)) + 0.5
        for line in (tcenter - 4, tcenter, tcenter + 4):
            tp = normal_over(gp["mu_total"], sig_t, line)
            rows.append({**base, "model_id": model_id, "market": Market.TOTAL.value, "subject_type": "game",
                         "subject_id": None, "side": "over", "threshold_kind": ThresholdKind.OVER.value, "threshold": line,
                         "probability": tp.p_hit, "push_probability": tp.p_push, "projection": gp["mu_total"],
                         "label": game_label(Market.TOTAL, ThresholdKind.OVER, line, "over"), "details": None})
    return rows


def _player_rows(row: dict, stat: str, mean: float, sm: NflStatModel, model_id: int, base: dict) -> list[dict]:
    market = PLAYER_MARKETS[stat]
    common = {**base, "model_id": model_id, "market": market, "subject_type": "player", "subject_id": row["player_id"]}
    details = {"team_id": row["team_id"], "position": row["position"],
               "availability": {"status": row["availability"], "reason": row["availability_reason"],
                                "source": "official NFL injury report for this week (when published)"},
               "rollout": ROLLOUT[stat], "distribution": sm.distribution(mean),
               "thresholds_supported": sm.thresholds_supported,
               "recent": {"snap_pct_l3": round(row["snap_l3"], 3), f"{stat}_season_avg": round(row[f"{stat}_season"], 2),
                          f"{stat}_last3": round(row[f"{stat}_l3"], 2)},
               "explanation": explain_player(row, stat, mean)}
    if row["excluded"]:
        return [{**common, "side": None, "threshold_kind": None, "threshold": None, "probability": None,
                 "push_probability": None, "projection": None, "label": f"No forecast: {row['availability_reason']}",
                 "details": {**details, "excluded": True}}]
    rows = [{**common, "side": None, "threshold_kind": None, "threshold": None, "probability": None,
             "push_probability": None, "projection": mean, "label": f"Expected {stat.replace('_', ' ')}", "details": details}]
    if sm.thresholds_supported:
        for kind, thr, p, push in sm.threshold_rows(mean):
            rows.append({**common, "side": "over" if kind is ThresholdKind.OVER else None, "threshold_kind": kind.value,
                         "threshold": thr, "probability": p, "push_probability": push, "projection": mean,
                         "label": player_label(stat, kind, thr), "details": None})
    return rows


# ---------------------------------------------------------------- jobs

def predict_upcoming(engine: Engine, weeks: int = 2, now: datetime | None = None) -> dict:
    """Live forecasts for every scheduled game in the next ``weeks`` NFL weeks (whole weeks, never a partial slate)."""
    now = now or datetime.now(timezone.utc)
    fs = build_features(engine)
    up = fs.games.filter((pl.col("status") == "scheduled") & (pl.col("kickoff_utc") > now))
    next_weeks = (up.group_by("season", "week").agg(pl.col("kickoff_utc").min().alias("k")).sort("k")
                  .head(weeks).select("season", "week"))
    ids = up.join(next_weeks, on=["season", "week"])["game_id"].to_list()
    run_id = start_run(engine, "live", now)
    try:
        st = forecast(engine, fs, active_model_ids(engine), ids, kind="live", data_cutoff=now, run_id=run_id)
        st["games_in_window"] = len(ids)
    except Exception as e:
        finish_run(engine, run_id, "failed", error=str(e)[:4000])
        raise
    finish_run(engine, run_id, "succeeded", {"league": "nfl", **st})
    return {"run_id": run_id, **st}


def backtest_season(engine: Engine, season: int) -> dict:
    """Point-in-time forecasts for every completed game of ``season`` with models trained only on earlier
    seasons (validated on season-1). Stored as kind='backtest' -- never presented as published picks."""
    fs = build_features(engine)
    if season - 1 not in fs.games["season"].unique().to_list():
        raise ValueError(f"need season {season - 1} for validation before backtesting {season}")
    ids = train_models(engine, fs, validation_season=season - 1, activate=False, tag=f"nflbt{season}")
    finals = fs.games.filter((pl.col("season") == season) & (pl.col("status") == "final"))
    total = {"games_written": 0, "prediction_rows": 0}
    for (day,), day_games in finals.group_by(["game_date_et"], maintain_order=True):
        cutoff = datetime.combine(day, datetime.min.time(), tzinfo=ET).astimezone(timezone.utc)
        run_id = start_run(engine, "backtest", cutoff)
        st = forecast(engine, fs, ids, day_games["game_id"].to_list(), kind="backtest", data_cutoff=cutoff, run_id=run_id)
        finish_run(engine, run_id, "succeeded", {"league": "nfl", **st})
        for k in total:
            total[k] += st[k]
    return {"season": season, "models": ids, **total}

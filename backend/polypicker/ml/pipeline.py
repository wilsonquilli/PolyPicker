"""Glue: build features from the DB, train + register models, generate forecasts.

Kept out of the web request path entirely (FR-06.5): these run from the CLI / scheduler.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pickle
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl
from sqlalchemy import Engine, func, insert, select, update

from polypicker.config import get_settings
from polypicker.db import models as m
from polypicker.domain.games import ET, season_label
from polypicker.domain.markets import Market, ThresholdKind, label
from polypicker.ml import data as mdata
from polypicker.ml.distributions import normal_over, spread_cover
from polypicker.ml.game_model import FEATURES as GAME_FEATURES, GameModel, design
from polypicker.ml.player_features import STATS, build_player_features
from polypicker.ml.player_model import StatModel, explain_player
from polypicker.ml.team_features import build_team_features, matchup_frame

log = logging.getLogger(__name__)
STAT_MARKET = {"points": Market.PLAYER_POINTS, "assists": Market.PLAYER_ASSISTS,
               "rebounds": Market.PLAYER_REBOUNDS}
STAT_ROLLOUT = {"points": "ga", "assists": "ga", "rebounds": "beta"}  # FR-03.3: points/assists first


@dataclass
class FeatureSet:
    games: pl.DataFrame
    game_design: pl.DataFrame
    player_train: pl.DataFrame
    player_cands: pl.DataFrame


def build_features(engine: Engine) -> FeatureSet:
    games = mdata.load_games(engine)
    tb = mdata.load_team_box(engine)
    pb = mdata.load_player_box(engine)
    tf = build_team_features(games, tb)
    mf = matchup_frame(tf)
    gd = design(mf, games)
    ptrain, pcands = build_player_features(games, pb, mf)
    return FeatureSet(games, gd, ptrain, pcands)


# ---------------------------------------------------------------- training / registry

def _season_bounds(games: pl.DataFrame, season: int) -> tuple[date, date]:
    s = games.filter(pl.col("season") == season)
    return s["game_date_et"].min(), s["game_date_et"].max()


def train_models(engine: Engine, fs: FeatureSet, validation_season: int, *, activate: bool,
                 tag: str = "") -> dict[str, int]:
    """Train on seasons < validation_season, validate on validation_season, refit on both, register.

    Default split from spec §9.4: train 2023-24 + 2024-25, validate 2025-26, forward-test 2026-27.
    """
    v_start, v_end = _season_bounds(fs.games, validation_season)
    train_end = v_start - timedelta(days=1)
    t_start = fs.games["game_date_et"].min()
    version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + (f"-{tag}" if tag else "")
    art_dir = get_settings().resolved_artifacts_dir()
    art_dir.mkdir(parents=True, exist_ok=True)
    ids: dict[str, int] = {}

    gm = GameModel.train(fs.game_design, train_end, v_start, v_end)
    gm.version = version
    ids["game"] = _register(engine, "game", version, "ridge+normal", gm, art_dir, t_start, v_end, v_start, v_end,
                            {"features": GAME_FEATURES,
                             "lines_supported": gm.lines_supported}, gm.metrics, activate)
    for stat in STATS:
        sm = StatModel.train(stat, fs.player_train, train_end, v_start, v_end)
        sm.version = version
        ids[stat] = _register(engine, f"player_{stat}", version, "hgb_poisson+negbin", sm, art_dir, t_start, v_end,
                              v_start, v_end, {"thresholds_supported": sm.thresholds_supported,
                                               "r0": sm.r0, "gamma": sm.gamma}, sm.metrics, activate)
    return ids


def _register(engine, target, version, algo, obj, art_dir: Path, t_start, t_end, v_start, v_end, params, metrics_,
              activate) -> int:
    path = art_dir / f"{target}-{version}.pkl"
    with open(path, "wb") as f:
        pickle.dump(obj, f)
    with engine.begin() as conn:
        if activate:
            conn.execute(update(m.Model).where(m.Model.target == target).values(is_active=False))
        mid = conn.execute(insert(m.Model).values(
            target=target, version=version, algorithm=algo, train_start=t_start, train_end=t_end,
            validation_start=v_start, validation_end=v_end, params=params, metrics=metrics_,
            artifact_path=str(path), is_active=activate).returning(m.Model.id)).scalar_one()
        conn.execute(insert(m.AdminAuditEvent).values(
            actor="cli", action="model_registered",
            detail={"model_id": mid, "target": target, "version": version, "activated": activate}))
    return mid


def load_model(engine: Engine, model_id: int):
    with engine.connect() as conn:
        path = conn.execute(select(m.Model.artifact_path).where(m.Model.id == model_id)).scalar_one()
    with open(path, "rb") as f:
        return pickle.load(f)


def active_model_ids(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        rows = conn.execute(select(m.Model.target, m.Model.id).where(m.Model.is_active)).all()
    ids = {t.replace("player_", ""): i for t, i in rows}
    missing = {"game", *STATS} - ids.keys()
    if missing:
        raise RuntimeError(f"no active model for {sorted(missing)}; run `polypicker train` first")
    return ids


# ---------------------------------------------------------------- forecasting

def forecast(engine: Engine, fs: FeatureSet, model_ids: dict[str, int], game_ids: list[int], *, kind: str,
             data_cutoff: datetime, run_id: int, skip_unchanged: bool = True) -> dict:
    """Write immutable prediction snapshots for the given games (one new revision per game)."""
    gm: GameModel = load_model(engine, model_ids["game"])
    sms: dict[str, StatModel] = {s: load_model(engine, model_ids[s]) for s in STATS}
    gd = fs.game_design.filter(pl.col("game_id").is_in(game_ids))
    game_preds = {p["game_id"]: p for p in gm.predict_games(gd)}
    cands = fs.player_cands.filter(pl.col("game_id").is_in(game_ids))
    stat_means = {s: (sms[s].predict(cands) if cands.height else []) for s in STATS}

    with engine.connect() as conn:
        last_rev = dict(conn.execute(
            select(m.Prediction.game_id, func.max(m.Prediction.revision))
            .where(m.Prediction.kind == kind, m.Prediction.game_id.in_(game_ids))
            .group_by(m.Prediction.game_id)).all())
        last_hash = dict(conn.execute(
            select(m.Prediction.game_id, m.Prediction.details["input_hash"].astext)
            .where(m.Prediction.kind == kind, m.Prediction.game_id.in_(game_ids),
                   m.Prediction.market == Market.MONEYLINE.value, m.Prediction.side == "home")
            .order_by(m.Prediction.game_id, m.Prediction.revision.desc())
            .distinct(m.Prediction.game_id)).all())

    cand_rows = list(cands.iter_rows(named=True))
    by_game: dict[int, list[int]] = {}
    for i, r in enumerate(cand_rows):
        by_game.setdefault(r["game_id"], []).append(i)

    out_rows: list[dict] = []
    written = skipped = 0
    for gid, gp in game_preds.items():
        players_payload = [(cand_rows[i], {s: float(stat_means[s][i]) for s in STATS}) for i in by_game.get(gid, [])]
        h = hashlib.sha1(json.dumps({"g": [round(gp["mu_home"], 2), round(gp["mu_away"], 2)],
                                     "p": sorted((r["player_id"], round(v["points"], 2), r["availability"])
                                                 for r, v in players_payload),
                                     "m": model_ids}, sort_keys=True).encode()).hexdigest()
        if skip_unchanged and last_hash.get(gid) == h:
            skipped += 1
            continue
        rev = (last_rev.get(gid) or 0) + 1
        base = {"run_id": run_id, "kind": kind, "game_id": gid, "revision": rev, "data_cutoff_utc": data_cutoff}
        out_rows += _game_rows(gp, gm, model_ids["game"], base, h)
        for row, means in players_payload:
            for s in STATS:
                out_rows += _player_rows(row, s, means[s], sms[s], model_ids[s], base)
        written += 1
    if out_rows:
        with engine.begin() as conn:
            for i in range(0, len(out_rows), 2000):
                conn.execute(insert(m.Prediction), out_rows[i:i + 2000])
    return {"games_written": written, "games_unchanged": skipped, "prediction_rows": len(out_rows)}


def _game_rows(gp: dict, gm: GameModel, model_id: int, base: dict, input_hash: str) -> list[dict]:
    rows = []
    common_details = {"factors": gp["factors"], "model_version": gm.version}
    p_home = gp["p_home_win"]
    for side, team, p in (("home", gp["home_team_id"], p_home), ("away", gp["away_team_id"], 1 - p_home)):
        rows.append({**base, "model_id": model_id, "market": Market.MONEYLINE.value, "subject_type": "team",
                     "subject_id": team, "side": side, "threshold_kind": None, "threshold": None,
                     "probability": p, "push_probability": 0.0, "projection": None,
                     "label": label(Market.MONEYLINE, ThresholdKind.AT_LEAST, None, side),
                     "details": {**common_details, "input_hash": input_hash,
                                 "distribution": {"type": "normal", "mu_margin_home": gp["mu_margin"],
                                                  "sigma_win": gm.sigma_win}} if side == "home" else common_details})
    for side, team, mu in (("home", gp["home_team_id"], gp["mu_home"]), ("away", gp["away_team_id"], gp["mu_away"])):
        rows.append({**base, "model_id": model_id, "market": Market.TEAM_POINTS.value, "subject_type": "team",
                     "subject_id": team, "side": side, "threshold_kind": None, "threshold": None,
                     "probability": None, "push_probability": None, "projection": mu,
                     "label": f"Projected {side} points", "details": None})
    dist = {"lines_supported": gm.lines_supported, "sigma_margin": gm.sigma_margin, "sigma_total": gm.sigma_total,
            "mu_margin_home": gp["mu_margin"], "mu_total": gp["mu_total"], "type": "normal_integer"}
    # Spread: projection = home margin; probabilities only if calibrated (FR-02.4)
    rows.append({**base, "model_id": model_id, "market": Market.SPREAD.value, "subject_type": "game",
                 "subject_id": None, "side": None, "threshold_kind": None, "threshold": None, "probability": None,
                 "push_probability": None, "projection": gp["mu_margin"], "label": "Projected home margin",
                 "details": dist})
    rows.append({**base, "model_id": model_id, "market": Market.TOTAL.value, "subject_type": "game",
                 "subject_id": None, "side": None, "threshold_kind": None, "threshold": None, "probability": None,
                 "push_probability": None, "projection": gp["mu_total"], "label": "Projected total points",
                 "details": dist})
    if gm.lines_supported:
        center = float(int(gp["mu_margin"] // 1)) + 0.5
        for line in (center - 3, center, center + 3):  # line on the home margin; home handicap = -line
            tp = spread_cover(gp["mu_margin"], gm.sigma_margin, -line)
            rows.append({**base, "model_id": model_id, "market": Market.SPREAD.value, "subject_type": "team",
                         "subject_id": gp["home_team_id"], "side": "home", "threshold_kind": ThresholdKind.HANDICAP.value,
                         "threshold": -line, "probability": tp.p_hit, "push_probability": tp.p_push,
                         "projection": gp["mu_margin"],
                         "label": label(Market.SPREAD, ThresholdKind.HANDICAP, -line, "home"), "details": None})
        tcenter = float(int(gp["mu_total"] // 1)) + 0.5
        for line in (tcenter - 5, tcenter, tcenter + 5):
            tp = normal_over(gp["mu_total"], gm.sigma_total, line)
            rows.append({**base, "model_id": model_id, "market": Market.TOTAL.value, "subject_type": "game",
                         "subject_id": None, "side": "over", "threshold_kind": ThresholdKind.OVER.value,
                         "threshold": line, "probability": tp.p_hit, "push_probability": tp.p_push,
                         "projection": gp["mu_total"],
                         "label": label(Market.TOTAL, ThresholdKind.OVER, line, "over"), "details": None})
    return rows


def _player_rows(row: dict, stat: str, mean: float, sm: StatModel, model_id: int, base: dict) -> list[dict]:
    market = STAT_MARKET[stat]
    availability = {"status": row["availability"], "reason": row["availability_reason"],
                    "source": "inferred from recent box scores (no injury feed)"}
    common = {**base, "model_id": model_id, "market": market.value, "subject_type": "player",
              "subject_id": row["player_id"]}
    details = {"team_id": row["team_id"], "availability": availability, "rollout": STAT_ROLLOUT[stat],
               "distribution": {"type": "negative_binomial", "mean": mean, "dispersion": sm.dispersion(mean),
                                "r0": sm.r0, "gamma": sm.gamma},
               "thresholds_supported": sm.thresholds_supported,
               "recent": {"min_l5": round(row["min_l5"], 1), "min_l10": round(row["min_l10"], 1),
                          f"{stat}_season_avg": round(row[f"{stat}_season_avg"], 2)},
               "explanation": explain_player(row, stat, mean)}
    if row["excluded"]:
        # FR-03.6: no confident forecast for a player who looks out; keep a void-able record.
        return [{**common, "side": None, "threshold_kind": None, "threshold": None, "probability": None,
                 "push_probability": None, "projection": None, "label": f"No forecast: {row['availability_reason']}",
                 "details": {**details, "excluded": True}}]
    rows = [{**common, "side": None, "threshold_kind": None, "threshold": None, "probability": None,
             "push_probability": None, "projection": mean, "label": f"Expected {stat}", "details": details}]
    if sm.thresholds_supported:
        for kind, thr, p, push in sm.threshold_rows(mean):
            rows.append({**common, "side": "over" if kind is ThresholdKind.OVER else None, "threshold_kind": kind.value,
                         "threshold": thr, "probability": p, "push_probability": push, "projection": mean,
                         "label": label(market, kind, thr, "over" if kind is ThresholdKind.OVER else None),
                         "details": None})
    return rows


# ---------------------------------------------------------------- runs

def start_run(engine: Engine, kind: str, cutoff: datetime) -> int:
    with engine.begin() as conn:
        return conn.execute(insert(m.PredictionRun).values(kind=kind, data_cutoff_utc=cutoff, status="running")
                            .returning(m.PredictionRun.id)).scalar_one()


def finish_run(engine: Engine, run_id: int, status: str, stats: dict | None = None, error: str | None = None):
    with engine.begin() as conn:
        conn.execute(update(m.PredictionRun).where(m.PredictionRun.id == run_id).values(
            status=status, stats=stats, error=error, finished_at=datetime.now(timezone.utc)))


def predict_upcoming(engine: Engine, days: int = 2, now: datetime | None = None) -> dict:
    """Live forecasts for scheduled games tipping off within ``days`` (FR-06.2)."""
    now = now or datetime.now(timezone.utc)
    fs = build_features(engine)
    horizon = now + timedelta(days=days)
    up = fs.games.filter((pl.col("status") == "scheduled") & (pl.col("tipoff_utc") > now)
                         & (pl.col("tipoff_utc") <= horizon))
    ids = up["game_id"].to_list()
    run_id = start_run(engine, "live", now)
    try:
        stats = forecast(engine, fs, active_model_ids(engine), ids, kind="live", data_cutoff=now, run_id=run_id)
        stats["games_in_window"] = len(ids)
    except Exception as e:
        finish_run(engine, run_id, "failed", error=str(e)[:4000])
        raise
    finish_run(engine, run_id, "succeeded", stats)
    return {"run_id": run_id, **stats}


def backtest_season(engine: Engine, season: int) -> dict:
    """Regenerate point-in-time forecasts for every completed game of ``season`` using models trained
    only on earlier seasons. Stored with kind='backtest' -- never presented as published live picks."""
    fs = build_features(engine)
    seasons = sorted(fs.games["season"].unique().to_list())
    if season - 1 not in seasons:
        raise ValueError(f"need season {season - 1} for validation before backtesting {season}")
    ids = train_models(engine, fs, validation_season=season - 1, activate=False, tag=f"bt{season}")
    finals = fs.games.filter((pl.col("season") == season) & (pl.col("status") == "final"))
    total = {"games_written": 0, "prediction_rows": 0}
    for (day,), day_games in finals.group_by(["game_date_et"], maintain_order=True):
        cutoff = datetime.combine(day, datetime.min.time(), tzinfo=ET).astimezone(timezone.utc)
        run_id = start_run(engine, "backtest", cutoff)
        st = forecast(engine, fs, ids, day_games["game_id"].to_list(), kind="backtest", data_cutoff=cutoff,
                      run_id=run_id)
        finish_run(engine, run_id, "succeeded", st)
        for k in total:
            total[k] += st[k]
    return {"season": season_label(season), "models": ids, **total}

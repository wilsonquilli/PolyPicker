"""Soccer glue: features from the DB, train + register, immutable forecasts, backtest (mirrors the NFL pipeline).

Markets per match: match_result (home / draw / away), team_goals (expected goals), total_goals (over N.5),
btts (both teams to score). Likely scorelines are stored in the match_result details.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import polars as pl
from sqlalchemy import Engine, func, insert, select

from polypicker.config import get_settings
from polypicker.db import models as m
from polypicker.domain.games import ET
from polypicker.ml.data import _read
from polypicker.ml.pipeline import _register, finish_run, load_model, start_run
from polypicker.soccer import models as sm
from polypicker.soccer.leagues import League
from polypicker.soccer.ml.features import build_team_features, matchup_frame
from polypicker.soccer.ml.model import FEATURES, TOTAL_LINES, SoccerModel, design

SIDE_LABEL = {"home": "home wins", "draw": "draw", "away": "away wins"}


@dataclass
class FeatureSet:
    league: League
    games: pl.DataFrame
    design: pl.DataFrame


def load_games(engine: Engine, competitions: tuple[str, ...]) -> pl.DataFrame:
    """Matches of the given competitions. Scores are 90-minute scores (extra-time goals aren't forecast); a match
    settled in extra time whose 90-minute score is unknown has no scores, so it's neither trained on nor graded."""
    comps = ", ".join(f"'{c}'" for c in competitions)   # codes come from the league registry, never from users
    return _read(engine, f"""
        SELECT game_id, competition, season, kickoff_utc, game_date, home_team_id, away_team_id, status,
               CASE WHEN decided IS NOT NULL THEN home_score_90 ELSE home_score END AS home_score,
               CASE WHEN decided IS NOT NULL THEN away_score_90 ELSE away_score END AS away_score,
               coalesce(neutral, false) AS neutral
        FROM soccer_games WHERE competition IN ({comps}) AND status NOT IN ('canceled')
        ORDER BY kickoff_utc, game_id""", schema_overrides={"home_score": pl.Int64, "away_score": pl.Int64}
    ).with_columns(pl.col("kickoff_utc").dt.convert_time_zone("UTC"))


def load_stats(engine: Engine) -> pl.DataFrame:
    return _read(engine, "SELECT game_id, team_id, shots, shots_on_target FROM soccer_team_game_stats",
                 schema_overrides={"shots": pl.Int64, "shots_on_target": pl.Int64})


def build_features(engine: Engine, league: League) -> FeatureSet:
    """Team ratings run over the league's feature pool (cups: the domestic leagues we cover plus the cups, so a
    club's form carries between competitions); the design and model use only this competition's matches."""
    pool = load_games(engine, league.pool)
    own = pool.filter(pl.col("competition") == league.competition)
    mf = matchup_frame(build_team_features(pool, load_stats(engine)))
    return FeatureSet(league, own, design(mf.filter(pl.col("game_id").is_in(own["game_id"].implode())), own))


def _bounds(games: pl.DataFrame, season: int) -> tuple[date, date]:
    s = games.filter(pl.col("season") == season)
    return s["game_date"].min(), s["game_date"].max()


def train(engine: Engine, fs: FeatureSet, validation_season: int, league: League | None = None, *, activate: bool, tag: str = "") -> int:
    vs, ve = _bounds(fs.games, validation_season)
    version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + (f"-{tag}" if tag else "")
    art = get_settings().resolved_artifacts_dir()
    art.mkdir(parents=True, exist_ok=True)
    mdl = SoccerModel.train(fs.design, vs - timedelta(days=1), vs, ve)
    mdl.version = version
    return _register(engine, (league or fs.league).target, version, "poisson_glm+dixon_coles", mdl, art, fs.games["game_date"].min(), ve, vs, ve,
                     {"features": FEATURES, "lines_supported": mdl.lines_supported, "rho": mdl.rho}, mdl.metrics, activate)


def active_model_id(engine: Engine, league: League) -> int:
    with engine.connect() as c:
        mid = c.execute(select(m.Model.id).where(m.Model.is_active, m.Model.target == league.target)).scalar()
    if mid is None:
        raise RuntimeError(f"no active {league.name} model; run `polypicker soccer train --league {league.slug}` first")
    return mid


def forecast(engine: Engine, fs: FeatureSet, model_id: int, game_ids: list[int], *, kind: str, data_cutoff: datetime,
             run_id: int, skip_unchanged: bool = True) -> dict:
    if not game_ids:   # between rounds / off-season: nothing to forecast
        return {"games_written": 0, "games_unchanged": 0, "prediction_rows": 0}
    mdl: SoccerModel = load_model(engine, model_id)
    preds = mdl.predict_games(fs.design.filter(pl.col("game_id").is_in(game_ids)))
    P = sm.SoccerPrediction
    with engine.connect() as c:
        last_rev = dict(c.execute(select(P.game_id, func.max(P.revision)).where(P.kind == kind, P.game_id.in_(game_ids))
                                  .group_by(P.game_id)).all())
        last_hash = dict(c.execute(select(P.game_id, P.details["input_hash"].astext)
                                   .where(P.kind == kind, P.game_id.in_(game_ids), P.market == "match_result", P.side == "home")
                                   .order_by(P.game_id, P.revision.desc()).distinct(P.game_id)).all())
    rows, written, skipped = [], 0, 0
    for p in preds:
        h = hashlib.sha1(json.dumps([round(p["xg_home"], 3), round(p["xg_away"], 3), model_id]).encode()).hexdigest()
        if skip_unchanged and last_hash.get(p["game_id"]) == h:
            skipped += 1
            continue
        base = {"run_id": run_id, "model_id": model_id, "kind": kind, "game_id": p["game_id"],
                "revision": (last_rev.get(p["game_id"]) or 0) + 1, "data_cutoff_utc": data_cutoff}
        for side in ("home", "draw", "away"):
            details = None
            if side == "home":
                details = {"input_hash": h, "factors": p["factors"], "top_scores": p["top_scores"], "model_version": mdl.version,
                           "distribution": {"type": "poisson_dixon_coles", "xg_home": p["xg_home"], "xg_away": p["xg_away"],
                                            "rho": mdl.rho}}
            rows.append({**base, "market": "match_result", "subject_type": "game",
                         "subject_id": p["home_team_id"] if side == "home" else p["away_team_id"] if side == "away" else None,
                         "side": side, "threshold_kind": None, "threshold": None, "probability": p[side], "push_probability": 0.0,
                         "projection": None, "label": SIDE_LABEL[side], "details": details})
        for side, tid, xg in (("home", p["home_team_id"], p["xg_home"]), ("away", p["away_team_id"], p["xg_away"])):
            rows.append({**base, "market": "team_goals", "subject_type": "team", "subject_id": tid, "side": side,
                         "threshold_kind": None, "threshold": None, "probability": None, "push_probability": None,
                         "projection": xg, "label": f"Expected {side} goals", "details": None})
        lines = {"lines_supported": mdl.lines_supported}
        rows.append({**base, "market": "total_goals", "subject_type": "game", "subject_id": None, "side": None,
                     "threshold_kind": None, "threshold": None, "probability": None, "push_probability": None,
                     "projection": p["xg_home"] + p["xg_away"], "label": "Expected total goals", "details": lines})
        if mdl.lines_supported:
            for line in TOTAL_LINES:
                rows.append({**base, "market": "total_goals", "subject_type": "game", "subject_id": None, "side": "over",
                             "threshold_kind": "over", "threshold": line, "probability": p[f"over_{line}"],
                             "push_probability": 0.0, "projection": p["xg_home"] + p["xg_away"],
                             "label": f"Over {line} goals ({int(line + 0.5)}+)", "details": None})
            rows.append({**base, "market": "btts", "subject_type": "game", "subject_id": None, "side": "yes",
                         "threshold_kind": None, "threshold": None, "probability": p["btts"], "push_probability": 0.0,
                         "projection": None, "label": "Both teams score", "details": None})
        written += 1
    if rows:
        with engine.begin() as c:
            for i in range(0, len(rows), 2000):
                c.execute(insert(P), rows[i:i + 2000])
    return {"games_written": written, "games_unchanged": skipped, "prediction_rows": len(rows)}


def predict_upcoming(engine: Engine, league: League, days: int = 10, now: datetime | None = None) -> dict:
    """Live forecasts for one league's scheduled matches in the next ``days`` (about one round)."""
    now = now or datetime.now(timezone.utc)
    fs = build_features(engine, league)
    ids = fs.games.filter((pl.col("status") == "scheduled") & (pl.col("kickoff_utc") > now)
                          & (pl.col("kickoff_utc") <= now + timedelta(days=days)))["game_id"].to_list()
    run_id = start_run(engine, "live", now)
    try:
        st = forecast(engine, fs, active_model_id(engine, league), ids, kind="live", data_cutoff=now, run_id=run_id)
        st["games_in_window"] = len(ids)
    except Exception as e:
        finish_run(engine, run_id, "failed", error=str(e)[:4000])
        raise
    finish_run(engine, run_id, "succeeded", {"league": "soccer", "competition": league.competition, **st})
    return {"league": league.slug, "run_id": run_id, **st}


def backtest_season(engine: Engine, season: int, league: League) -> dict:
    fs = build_features(engine, league)
    mid = train(engine, fs, season - 1, activate=False, tag=f"{league.slug}bt{season}")
    finals = fs.games.filter((pl.col("season") == season) & (pl.col("status") == "final"))
    total = {"games_written": 0, "prediction_rows": 0}
    for (day,), dg in finals.group_by(["game_date"], maintain_order=True):
        cutoff = datetime.combine(day, datetime.min.time(), tzinfo=ET).astimezone(timezone.utc)
        run_id = start_run(engine, "backtest", cutoff)
        st = forecast(engine, fs, mid, dg["game_id"].to_list(), kind="backtest", data_cutoff=cutoff, run_id=run_id)
        finish_run(engine, run_id, "succeeded", {"league": "soccer", "competition": league.competition, **st})
        for k in total:
            total[k] += st[k]
    return {"league": league.slug, "season": season, "model_id": mid, **total}

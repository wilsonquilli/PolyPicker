"""Point-in-time adjusted plus-minus (APM) player ratings.

A ridge regression of each game's final margin on the players who took part, each weighted by
minutes/48 (+ home side, - away side). The coefficient is a player's impact on his team's margin per
48 minutes after accounting for every teammate and opponent he shared the floor-time with -- the same
idea as RAPM, at game level (no play-by-play needed, so it updates from the live box scores).

Refit every 7 days on earlier games only, with older games down-weighted (half-life 365 days).

Validated 2026-11-24 (train <= 2024-25, tune on 2025-26, confirm on 2026-27 to date). Added to the
no-market model: 2025-26 log loss 0.5864 -> 0.5835; 2026-27 accuracy 65.9 -> 66.7%, log loss
0.5901 -> 0.5877. Not used by the market model: there it helped 2025-26 but hurt 2026-27 log loss.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
from scipy import sparse
from sklearn.linear_model import Ridge

ALPHA = 15.0
HALF_LIFE_DAYS = 365.0
REFIT_EVERY_DAYS = 7
MIN_GAMES = 400          # no ratings until this many earlier games exist
UNRATED = -2.0           # per 48 minutes: players without a rating (rookies, call-ups) ~ replacement level
# Possession RAPM prior (SportsDataverse possessions, polypicker/etl/sources/sdv_possessions.py): net points per 100
# possessions (~ per 48 minutes at NBA pace) from every *earlier* season, recent seasons weighted more.
RAPM_ALPHA = 3000.0
RAPM_DECAY = 0.6          # weight per season back


def rapm_priors(poss: pl.DataFrame, seasons: list[int], *, alpha: float = RAPM_ALPHA,
                decay: float = RAPM_DECAY) -> dict[int, dict[int, float]]:
    """season -> {player_id: RAPM} fitted on possessions from seasons strictly before it (point-in-time: the
    possession files are published after each season). ``poss``: season, off_ids, def_ids, possessions, points."""
    out: dict[int, dict[int, float]] = {}
    for season in seasons:
        p = poss.filter(pl.col("season") < season)
        if p.height < 1000:
            out[season] = {}
            continue
        off, dfn = p["off_ids"].to_list(), p["def_ids"].to_list()
        pids = sorted({x for lst in off + dfn for x in lst})
        ix = {q: i for i, q in enumerate(pids)}
        X = sparse.csr_matrix((np.tile(np.r_[np.ones(5), -np.ones(5)], p.height),
                               (np.repeat(np.arange(p.height), 10), [ix[x] for o, d in zip(off, dfn) for x in (*o, *d)])),
                              shape=(p.height, len(pids)))
        n = p["possessions"].to_numpy().astype(float)
        y = 100 * p["points"].to_numpy() / n
        w = n * decay ** (season - 1 - p["season"].to_numpy())
        m = Ridge(alpha=alpha).fit(X, y - np.average(y, weights=w), sample_weight=w)
        out[season] = dict(zip(pids, m.coef_.tolist()))
    return out


def apm_ratings(games: pl.DataFrame, box: pl.DataFrame, *, alpha: float = ALPHA,
                half_life_days: float = HALF_LIFE_DAYS,
                priors: dict[int, dict[int, float]] | None = None) -> dict[date, dict[int, float]]:
    """game day -> {player_id: rating per 48 minutes}, each from games strictly before that day.

    ``priors`` (season -> {player: rating}, e.g. ``rapm_priors``): the ridge shrinks each player toward his prior for
    that season instead of toward 0, and players with a prior but no games yet start from it."""
    fin = games.filter(pl.col("status") == "final").select("game_id", "game_date_et", "home_team_id",
                                                             "home_score", "away_score")
    b = (box.filter(pl.col("participation") == "played").select("game_id", "player_id", "team_id", "minutes")
         .drop_nulls().join(fin, on="game_id"))
    days = sorted(set(games["game_date_et"].to_list()))
    if fin.is_empty() or b.is_empty():
        return {d: {} for d in days}
    gidx = {g: i for i, g in enumerate(fin["game_id"].to_list())}
    pids = b["player_id"].unique().sort().to_list()
    pidx = {p: i for i, p in enumerate(pids)}
    side = np.where(b["team_id"].to_numpy() == b["home_team_id"].to_numpy(), 1.0, -1.0)
    X = sparse.csr_matrix((side * b["minutes"].to_numpy().astype(float) / 48.0,
                           ([gidx[g] for g in b["game_id"]], [pidx[p] for p in b["player_id"]])),
                          shape=(len(gidx), len(pids)))
    y = (fin["home_score"] - fin["away_score"]).to_numpy().astype(float)
    gdates = fin["game_date_et"].to_numpy().astype("datetime64[D]")
    day_season = dict(zip(games["game_date_et"].to_list(), games["season"].to_list())) if "season" in games.columns else {}
    out: dict[date, dict[int, float]] = {}
    cur: dict[int, float] = {}
    last_fit: date | None = None
    last_season = None
    for d in days:
        season = day_season.get(d)
        if last_fit is None or (d - last_fit).days >= REFIT_EVERY_DAYS or season != last_season:
            prior = (priors or {}).get(season, {})
            mask = gdates < np.datetime64(d, "D")
            if mask.sum() >= MIN_GAMES:
                age = (np.datetime64(d, "D") - gdates[mask]).astype(float)
                p0 = np.array([prior.get(q, 0.0) for q in pids])
                m = Ridge(alpha=alpha).fit(X[mask], y[mask] - X[mask] @ p0, sample_weight=0.5 ** (age / half_life_days))
                cur = {**prior, **dict(zip(pids, (p0 + m.coef_).tolist()))}
            else:
                cur = dict(prior)
            last_fit, last_season = d, season
        out[d] = cur
    return out

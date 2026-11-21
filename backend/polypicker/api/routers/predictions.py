"""Forecasts, custom thresholds, archive and model performance (FR-02, FR-03, FR-06.4, FR-08.3, FR-09)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection

from polypicker.api.deps import get_conn, q, q1
from polypicker.api.queries import freshness, prediction_meta, teams_by_id
from polypicker.domain.markets import Market, ThresholdKind, label, round_probability_pair
from polypicker.ml.distributions import count_threshold, dispersion_at, normal_over, spread_cover

router = APIRouter(tags=["predictions"])

# FR-08.3 category filter -> internal markets
CATEGORY = {"winner": ["moneyline", "team_points"], "spread": ["spread"], "total": ["total"],
            "points": ["player_points"], "assists": ["player_assists"], "rebounds": ["player_rebounds"]}
Category = Literal["winner", "spread", "total", "points", "assists", "rebounds"]
Kind = Literal["live", "backtest"]


def _latest_revision(conn: Connection, game_id: int, kind: Kind | None, revision: int | None) -> dict | None:
    where = "game_id = :g" + (" AND kind = :k" if kind else "") + (" AND revision = :r" if revision else "")
    return q1(conn, f"""SELECT kind, revision, max(published_at) published_at, max(data_cutoff_utc) data_cutoff_utc
                       FROM predictions WHERE {where} GROUP BY kind, revision
                       ORDER BY (kind = 'live') DESC, revision DESC LIMIT 1""",
              g=game_id, k=kind, r=revision)


def _forecast_status(game: dict, rev: dict | None, fresh: dict) -> str:
    """current | stale | pending | unavailable (NFR-10.4)."""
    if rev is None:
        return "pending" if game["status"] == "scheduled" else "unavailable"
    if rev["kind"] == "backtest":
        return "backtest"
    sync = fresh.get("last_data_sync_utc")
    if game["status"] == "scheduled" and sync and rev["data_cutoff_utc"] < sync:
        return "stale"  # newer data was ingested after this forecast's input cutoff
    return "current"


@router.get("/games/{game_id}/predictions")
def game_predictions(game_id: int, category: list[Category] | None = Query(None),
                     kind: Kind | None = Query(None, description="Default: live if available, else backtest"),
                     revision: int | None = None, conn: Connection = Depends(get_conn)):
    """Latest published forecast for a game: winner, projected score, spread/total, player props (FR-02, FR-03)."""
    game = q1(conn, "SELECT * FROM games WHERE game_id = :g", g=game_id)
    if not game:
        raise HTTPException(404, f"Game {game_id} not found")
    fresh = freshness(conn)
    rev = _latest_revision(conn, game_id, kind, revision)
    status = _forecast_status(game, rev, fresh)
    if rev is None:
        return {"data": {"game_id": game_id, "forecast_status": status, "markets": None, "players": []},
                "meta": prediction_meta(extra={"freshness": fresh})}
    markets = sum((CATEGORY[c] for c in (category or CATEGORY)), [])
    rows = q(conn, """
        SELECT p.id, p.market, p.subject_type, p.subject_id, p.side, p.threshold_kind, p.threshold, p.probability,
               p.push_probability, p.projection, p.label, p.details, p.model_id, mo.version AS model_version,
               o.graded_state, o.outcome, o.observed_value
        FROM predictions p JOIN models mo ON mo.id = p.model_id
        LEFT JOIN prediction_outcomes o ON o.prediction_id = p.id
        WHERE p.game_id = :g AND p.kind = :k AND p.revision = :r AND p.market = ANY(:m)
        ORDER BY p.market, p.subject_id, p.threshold NULLS FIRST""",
             g=game_id, k=rev["kind"], r=rev["revision"], m=markets)
    revisions = q(conn, """SELECT kind, revision, min(published_at) published_at FROM predictions
                           WHERE game_id = :g GROUP BY 1, 2 ORDER BY 1, 2""", g=game_id)
    teams = teams_by_id(conn)
    game_markets, players, excluded, model_versions = _shape(rows, game, teams)
    for r in rows:
        model_versions[r["market"]] = r["model_version"]
    ids = list({p["player_id"] for p in players} | {e["player_id"] for e in excluded})
    if ids:
        names = {r["player_id"]: r for r in q(conn, """SELECT player_id, full_name, position FROM players
                                                        WHERE player_id = ANY(:ids)""", ids=ids)}
        for p in players + excluded:
            n = names.get(p["player_id"], {})
            p["full_name"], p["position"] = n.get("full_name"), n.get("position")
    return {"data": {
        "game_id": game_id, "game_status": game["status"], "forecast_status": status,
        "kind": rev["kind"], "revision": rev["revision"], "published_at": rev["published_at"],
        "data_cutoff_utc": rev["data_cutoff_utc"], "model_versions": model_versions,
        "markets": game_markets, "players": players, "excluded_players": excluded, "revisions": revisions,
        "notes": {
            "kind": "Backtest forecasts were regenerated after the fact using only data available before "
                    "each game; they were not published live." if rev["kind"] == "backtest" else None,
            "availability": "Player availability is inferred from recent box scores; no injury feed is used.",
            "thresholds": "'20+' means at least 20. 'Over 20.5' means 21 or more. An integer over/under or "
                          "spread line can push (exact landing); pushes are reported separately.",
        }},
        "meta": prediction_meta(extra={"freshness": fresh})}


def _outcome(r: dict) -> dict | None:
    if not r["graded_state"]:
        return None
    return {"state": r["graded_state"], "result": r["outcome"], "observed": r["observed_value"]}


def _shape(rows: list[dict], game: dict, teams: dict) -> tuple[dict, list, list, dict]:
    gm: dict = {}
    players: dict[int, dict] = {}
    excluded: list = []
    for r in rows:
        mk = r["market"]
        if mk == "moneyline":
            ml = gm.setdefault("winner", {"label": "Full game winner (including overtime)"})
            ml[r["side"]] = {"team_id": r["subject_id"], "abbreviation": teams[r["subject_id"]]["abbreviation"],
                             "probability": r["probability"], "outcome": _outcome(r)}
            if r["side"] == "home" and r["details"]:
                ml["factors"] = r["details"].get("factors")
                dist = r["details"].get("distribution") or {}
                if dist:  # P(home win) = P(margin > 0) under Normal(mean, sd)
                    ml["distribution"] = {"type": "normal", "mean": dist.get("mu_margin_home"),
                                          "sd": dist.get("sigma_win")}
        elif mk == "team_points":
            sc = gm.setdefault("projected_score", {})
            sc[r["side"]] = round(r["projection"], 1)
            sc[f"{r['side']}_outcome"] = _outcome(r)
        elif mk in ("spread", "total"):
            m = gm.setdefault(mk, {"lines": []})
            if r["threshold_kind"] is None:
                m["projection"] = round(r["projection"], 1)
                m["projection_label"] = r["label"]
                d = r["details"] or {}
                m["probabilities_available"] = bool(d.get("lines_supported"))
                m["distribution"] = {"type": "normal",
                                     "mean": d.get("mu_margin_home") if mk == "spread" else d.get("mu_total"),
                                     "sd": d.get("sigma_margin") if mk == "spread" else d.get("sigma_total")}
                m["projection_outcome"] = _outcome(r)
            else:
                m["lines"].append({"line": r["threshold"], "side": r["side"], "label": r["label"],
                                   "probability": r["probability"], "push_probability": r["push_probability"],
                                   "complement_probability": max(0.0, 1 - r["probability"] - (r["push_probability"] or 0)),
                                   "outcome": _outcome(r)})
        else:  # player markets
            stat = mk.removeprefix("player_")
            if r["details"] and r["details"].get("excluded"):
                excluded.append({"player_id": r["subject_id"], "market": mk, "reason": r["label"]})
                continue
            pl = players.setdefault(r["subject_id"], {"player_id": r["subject_id"], "markets": {}})
            entry = pl["markets"].setdefault(stat, {"thresholds": []})
            if r["threshold_kind"] is None:
                d = r["details"] or {}
                pl.update(team_id=d.get("team_id"), availability=d.get("availability"))
                entry.update(expected=round(r["projection"], 2), rollout=d.get("rollout"),
                             explanation=d.get("explanation"), recent=d.get("recent"),
                             thresholds_available=d.get("thresholds_supported"), outcome=_outcome(r))
            else:
                entry["thresholds"].append({
                    "kind": r["threshold_kind"], "threshold": r["threshold"], "label": r["label"],
                    "probability": r["probability"], "push_probability": r["push_probability"],
                    "complement_probability": max(0.0, 1 - r["probability"] - (r["push_probability"] or 0)),
                    "outcome": _outcome(r)})
    if "winner" in gm:
        gm["winner"]["home_pct"], gm["winner"]["away_pct"] = round_probability_pair(gm["winner"]["home"]["probability"])
    return gm, sorted(players.values(), key=lambda p: -(p["markets"].get("points", {}).get("expected") or 0)), excluded, {}


@router.get("/games/{game_id}/predictions/custom")
def custom_threshold(game_id: int,
                     market: Literal["spread", "total", "player_points", "player_assists", "player_rebounds"],
                     threshold: float = Query(..., ge=-80, le=400),
                     threshold_kind: Literal["at_least", "over"] = "over",
                     side: Literal["home", "away"] = "home",
                     player_id: int | None = None, conn: Connection = Depends(get_conn)):
    """Probability for a user-selected line, from the stored predictive distribution of the latest snapshot
    (FR-03.2, FR-03.4, FR-03.7). Cheap closed-form math -- no model inference on the request path."""
    rev = _latest_revision(conn, game_id, None, None)
    if not rev:
        raise HTTPException(404, "No forecast for this game")
    mk = Market(market)
    if mk.is_player:
        if player_id is None:
            raise HTTPException(400, "player_id is required for player markets")
        if threshold < 0 or (threshold_kind == "at_least" and not float(threshold).is_integer()):
            raise HTTPException(400, "'at_least' thresholds must be non-negative integers")
        r = q1(conn, """SELECT details FROM predictions WHERE game_id = :g AND kind = :k AND revision = :rv
                        AND market = :m AND subject_id = :p AND threshold_kind IS NULL""",
               g=game_id, k=rev["kind"], rv=rev["revision"], m=market, p=player_id)
        if not r or not r["details"] or r["details"].get("excluded"):
            raise HTTPException(404, "No forecast for this player in this game")
        if not r["details"].get("thresholds_supported"):
            raise HTTPException(409, "Threshold probabilities are not published for this market (calibration gate)")
        dist = r["details"]["distribution"]
        kind = ThresholdKind(threshold_kind)
        disp = dist.get("dispersion") or dispersion_at(dist["mean"], dist["r0"], dist["gamma"])
        tp = count_threshold(dist["mean"], disp, kind, threshold)
        lbl = label(mk, kind, threshold, "over" if kind is ThresholdKind.OVER else None)
        expected = dist["mean"]
    else:
        r = q1(conn, """SELECT details, projection FROM predictions WHERE game_id = :g AND kind = :k AND revision = :rv
                        AND market = :m AND threshold_kind IS NULL""",
               g=game_id, k=rev["kind"], rv=rev["revision"], m=market)
        d = r and r["details"]
        if not d or not d.get("lines_supported"):
            raise HTTPException(409, "Spread/total probabilities are not published for this game (FR-02.4)")
        if mk is Market.SPREAD:
            mu = d["mu_margin_home"] if side == "home" else -d["mu_margin_home"]
            tp = spread_cover(mu, d["sigma_margin"], threshold)
            lbl = label(mk, ThresholdKind.HANDICAP, threshold, side)
            expected = mu
        else:
            tp = normal_over(d["mu_total"], d["sigma_total"], threshold)
            lbl = label(mk, ThresholdKind.OVER, threshold, "over")
            expected = d["mu_total"]
    return {"data": {"game_id": game_id, "market": market, "player_id": player_id, "threshold": threshold,
                     "label": lbl, "expected": round(expected, 2), "p_hit": tp.p_hit, "p_push": tp.p_push,
                     "p_miss": tp.p_miss, "kind": rev["kind"], "revision": rev["revision"],
                     "derived": True},
            "meta": prediction_meta()}


@router.get("/predictions/history")
def prediction_history(
    category: list[Category] | None = Query(None), kind: Kind | None = None,
    state: list[Literal["pending", "final", "void", "unavailable", "unevaluable"]] | None = Query(None),
    date_from: date | None = None, date_to: date | None = None, team: int | None = None,
    player_id: int | None = None, probabilities_only: bool = False,
    favorites_only: bool = Query(False, description="Winner market: only the favored side (one row per game)"),
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
    conn: Connection = Depends(get_conn),
):
    """Archived pregame snapshots with their grading (FR-09.1, FR-09.5). Snapshots are never rewritten."""
    where = ["1=1"]
    params: dict = {}
    if category:
        where.append("p.market = ANY(:m)"); params["m"] = sum((CATEGORY[c] for c in category), [])
    if kind:
        where.append("p.kind = :k"); params["k"] = kind
    if state:
        where.append("coalesce(o.graded_state, 'pending') = ANY(:st)"); params["st"] = list(state)
    if date_from:
        where.append("g.game_date_et >= :df"); params["df"] = date_from
    if date_to:
        where.append("g.game_date_et <= :dt"); params["dt"] = date_to
    if team:
        where.append("(g.home_team_id = :t OR g.away_team_id = :t)"); params["t"] = team
    if player_id:
        where.append("p.subject_type = 'player' AND p.subject_id = :pid"); params["pid"] = player_id
    if probabilities_only:
        where.append("p.probability IS NOT NULL")
    if favorites_only:
        where.append("(p.market <> 'moneyline' OR p.probability >= 0.5)")
    w = " AND ".join(where)
    base = f"""FROM predictions p JOIN games g USING (game_id)
               JOIN teams ht ON ht.team_id = g.home_team_id JOIN teams at ON at.team_id = g.away_team_id
               LEFT JOIN players pl ON p.subject_type = 'player' AND pl.player_id = p.subject_id
               LEFT JOIN teams st ON p.subject_type = 'team' AND st.team_id = p.subject_id
               LEFT JOIN prediction_outcomes o ON o.prediction_id = p.id WHERE {w}"""
    total = q1(conn, f"SELECT count(*) n {base}", **params)["n"]
    rows = q(conn, f"""
        SELECT p.id AS prediction_id, p.game_id, g.game_date_et AS game_date, p.kind, p.revision, p.market,
               p.subject_type, p.subject_id, p.side, p.threshold_kind, p.threshold, p.label, p.probability,
               p.push_probability, p.projection, p.published_at, p.data_cutoff_utc,
               coalesce(o.graded_state, 'pending') AS graded_state, o.outcome, o.observed_value, o.brier,
               o.abs_error, o.graded_at, at.abbreviation || ' @ ' || ht.abbreviation AS matchup,
               g.home_score, g.away_score, coalesce(pl.full_name, st.abbreviation) AS subject_name
        {base} ORDER BY g.tipoff_utc DESC, p.game_id, p.id LIMIT :lim OFFSET :off""",
             **params, lim=limit, off=offset)
    return {"data": rows, "meta": prediction_meta(total=total, limit=limit, offset=offset)}


@router.get("/models/performance")
def model_performance(kind: Kind | None = None, season: int | None = None,
                      category: list[Category] | None = Query(None), conn: Connection = Depends(get_conn)):
    """Accuracy, Brier, log loss, calibration and MAE from graded snapshots (FR-09.3, FR-09.4), plus the
    held-out validation metrics recorded when each active model was trained."""
    where, params = ["1=1"], {}
    if kind:
        where.append("p.kind = :k"); params["k"] = kind
    if season:
        where.append("g.season = :s"); params["s"] = season
    if category:
        where.append("p.market = ANY(:m)"); params["m"] = sum((CATEGORY[c] for c in category), [])
    w = " AND ".join(where)
    base = f"FROM predictions p JOIN games g USING (game_id) JOIN prediction_outcomes o ON o.prediction_id = p.id WHERE {w}"
    states = q(conn, f"SELECT p.market, o.graded_state, count(*) n {base} GROUP BY 1, 2 ORDER BY 1, 2", **params)
    prob = q(conn, f"""
        SELECT p.market, count(*) n, avg(o.brier) brier, avg(o.log_loss) log_loss,
               avg(CASE WHEN o.outcome = 'hit' THEN 1.0 ELSE 0.0 END) hit_rate
        {base} AND o.graded_state = 'final' AND p.probability IS NOT NULL AND o.outcome IN ('hit','miss')
        GROUP BY 1 ORDER BY 1""", **params)
    winner = q1(conn, f"""
        SELECT count(*) n, avg(CASE WHEN o.outcome = 'hit' THEN 1.0 ELSE 0.0 END) accuracy
        {base} AND p.market = 'moneyline' AND o.graded_state = 'final' AND p.probability > 0.5""", **params)
    mae = q(conn, f"""
        SELECT p.market, count(*) n, avg(o.abs_error) mae
        {base} AND o.graded_state = 'final' AND p.threshold_kind IS NULL AND o.abs_error IS NOT NULL
        GROUP BY 1 ORDER BY 1""", **params)
    calib = q(conn, f"""
        SELECT p.market, width_bucket(p.probability, 0, 1, 10) AS bucket, count(*) n,
               avg(p.probability) mean_predicted, avg(CASE WHEN o.outcome = 'hit' THEN 1.0 ELSE 0.0 END) observed_rate
        {base} AND o.graded_state = 'final' AND p.probability IS NOT NULL AND o.outcome IN ('hit','miss')
        GROUP BY 1, 2 ORDER BY 1, 2""", **params)
    active = q(conn, """SELECT id, target, version, algorithm, train_start, train_end, validation_start,
                               validation_end, metrics, trained_at FROM models WHERE is_active ORDER BY target""")
    by_market: dict[str, dict] = {}
    for r in states:
        by_market.setdefault(r["market"], {"graded_states": {}})["graded_states"][r["graded_state"]] = r["n"]
    for r in prob:
        by_market.setdefault(r["market"], {}).update(
            probability_metrics={"n": r["n"], "brier": _r(r["brier"]), "log_loss": _r(r["log_loss"]),
                                 "hit_rate": _r(r["hit_rate"])})
    for r in mae:
        by_market.setdefault(r["market"], {}).update(mae={"n": r["n"], "value": _r(r["mae"], 3)})
    for r in calib:
        by_market.setdefault(r["market"], {}).setdefault("calibration", []).append(
            {"bucket": r["bucket"], "n": r["n"], "mean_predicted": _r(r["mean_predicted"]),
             "observed_rate": _r(r["observed_rate"])})
    if winner and winner["n"]:
        by_market.setdefault("moneyline", {})["winner_accuracy"] = {"n": winner["n"], "value": _r(winner["accuracy"])}
    return {"data": {"filters": {"kind": kind, "season": season, "category": category}, "markets": by_market,
                     "active_models": active},
            "meta": prediction_meta(extra={"note": "Backtest metrics use point-in-time forecasts regenerated after "
                                                   "the fact; live metrics accumulate as published forecasts are graded."})}


def _r(v, n: int = 4):
    return None if v is None else round(float(v), n)

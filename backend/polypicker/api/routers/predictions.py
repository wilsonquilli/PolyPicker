"""Forecasts, custom thresholds, archive and model performance (FR-02, FR-03, FR-06.4, FR-08.3, FR-09)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection

from polypicker.api.deps import cached, get_conn, q, q1
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
    odds = q1(conn, """SELECT provider, p_home, home_ml, away_ml, spread_home, total, is_closing, fetched_at
                       FROM nba_odds WHERE game_id = :g""", g=game_id)
    pmk = q1(conn, """SELECT slug, event_slug, p_home_live, p_home_pregame, pregame_at, volume_usd, closed, fetched_at
                      FROM polymarket_nba WHERE game_id = :g""", g=game_id)
    if pmk and (pmk["p_home_live"] is not None or pmk["p_home_pregame"] is not None):
        started = game["status"] != "scheduled"
        p = pmk["p_home_pregame"] if (started and pmk["p_home_pregame"] is not None) else pmk["p_home_live"]
        if p is not None:
            game_markets["polymarket"] = {
                "p_home": round(float(p), 4), "price_type": "pregame" if started else "live",
                "volume_usd": pmk["volume_usd"], "fetched_at": pmk["fetched_at"],
                "url": f"https://polymarket.com/event/{pmk['event_slug'] or pmk['slug']}",
                "note": "Polymarket price for this game's winner market (crowd-traded probability). "
                        "Shown for comparison; it is not a PolyPicker forecast."}
    if odds and odds["p_home"] is not None:
        game_markets["sportsbook"] = {**odds, "p_home": round(float(odds["p_home"]), 4),
                                      "note": "Sportsbook line from ESPN's odds feed, bookmaker margin removed. "
                                              "Shown for comparison; it is not a PolyPicker forecast."}
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
            "availability": "Player availability comes from ESPN's injury report when a player is listed, otherwise "
                            "it is inferred from recent box scores. Backtests use the players who actually played.",
            "market": "When a game has a betting line, the winner forecast also uses the sportsbook spread/total and "
                      "Polymarket's price. Backtests use closing lines; live forecasts use the line at publish time.",
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
                if r["details"].get("absences"):
                    ml["absences"] = r["details"]["absences"]
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
@cached(ttl=300)
def prediction_history(
    category: list[Category] | None = Query(None), kind: Kind | None = None,
    state: list[Literal["pending", "final", "void", "unavailable", "unevaluable"]] | None = Query(None),
    date_from: date | None = None, date_to: date | None = None, team: int | None = None,
    player_id: int | None = None, probabilities_only: bool = False,
    favorites_only: bool = Query(False, description="Winner market: only the favored side (one row per game)"),
    one_per_game: bool = Query(False, description="Only the final forecast (latest revision) per game, and one line "
                               "per game/player: the favored winner, or the line nearest the projection"),
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
    cols = """p.id AS prediction_id, p.game_id, g.game_date_et AS game_date, p.kind, p.revision, p.market,
               p.subject_type, p.subject_id, p.side, p.threshold_kind, p.threshold, p.label, p.probability,
               p.push_probability, p.projection, p.published_at, p.data_cutoff_utc,
               coalesce(o.graded_state, 'pending') AS graded_state, o.outcome, o.observed_value, o.brier,
               o.abs_error, o.graded_at, at.abbreviation || ' @ ' || ht.abbreviation AS matchup,
               g.home_score, g.away_score, coalesce(pl.full_name, st.abbreviation) AS subject_name, g.tipoff_utc"""
    if one_per_game:
        # A game is re-forecast many times; keep the last revision, then a single headline row per
        # game (or player): the favored winner, or the line closest to the projection.
        base = base.replace("WHERE ", """JOIN (SELECT game_id, kind, max(revision) r FROM predictions GROUP BY 1, 2) lr
                ON lr.game_id = p.game_id AND lr.kind = p.kind AND lr.r = p.revision
            WHERE ((p.market = 'moneyline' AND (p.probability > 0.5 OR (p.probability = 0.5 AND p.side = 'home')))
                   OR (p.market <> 'moneyline' AND p.threshold_kind IN ('handicap', 'over'))) AND """, 1)
        src = f"""(SELECT DISTINCT ON (p.game_id, p.kind, p.market, p.subject_id) {cols} {base}
                   ORDER BY p.game_id, p.kind, p.market, p.subject_id,
                            abs(p.threshold + CASE WHEN p.market = 'spread' THEN p.projection ELSE -p.projection END)
                              NULLS LAST, p.id) x"""
    else:
        src = f"(SELECT {cols} {base}) x"
    total = q1(conn, f"SELECT count(*) n FROM {src}", **params)["n"]
    rows = q(conn, f"SELECT * FROM {src} ORDER BY tipoff_utc DESC, game_id, prediction_id LIMIT :lim OFFSET :off",
             **params, lim=limit, off=offset)
    for r in rows:
        r.pop("tipoff_utc", None)
    return {"data": rows, "meta": prediction_meta(total=total, limit=limit, offset=offset)}


@router.get("/markets/polymarket")
def polymarket_prices(date_from: date | None = None, date_to: date | None = None, conn: Connection = Depends(get_conn)):
    """Polymarket winner-market prices per NBA game (live price before tip-off, pregame price after)."""
    rows = q(conn, """
        SELECT x.game_id, CASE WHEN g.status = 'scheduled' THEN x.p_home_live ELSE coalesce(x.p_home_pregame, x.p_home_live) END p_home,
               x.volume_usd, coalesce(x.event_slug, x.slug) slug, g.status
        FROM polymarket_nba x JOIN games g USING (game_id)
        WHERE g.game_date_et BETWEEN coalesce(:df, current_date - 1) AND coalesce(:dt, current_date + 7)""",
             df=date_from, dt=date_to)
    return {"data": [{**r, "p_home": _r(r["p_home"]), "url": f"https://polymarket.com/event/{r['slug']}"}
                     for r in rows if r["p_home"] is not None],
            "meta": prediction_meta(extra={"source": "Polymarket (gamma-api.polymarket.com, clob.polymarket.com)"})}


@router.get("/line-movement")
@cached(ttl=300)
def line_movement_nba(kind: str = Query("live", pattern="^(live|backtest)$"), conn: Connection = Depends(get_conn)):
    """Opening -> closing line moves vs our picks (see api/linemove.py)."""
    from polypicker.api.linemove import line_movement
    return {"data": line_movement(conn, "nba", kind), "meta": prediction_meta()}


@router.get("/models/performance")
@cached(ttl=300)
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
    # Score each game once: its latest revision per kind (the last forecast before tip-off for live).
    # Restrict the latest-revision scan to the requested kind (backtests are most of the table).
    latest = f"""JOIN (SELECT game_id, kind, max(revision) r FROM predictions {'WHERE kind = :k' if kind else ''} GROUP BY 1, 2) lr
                ON lr.game_id = p.game_id AND lr.kind = p.kind AND lr.r = p.revision"""
    base = f"FROM predictions p {latest} JOIN games g ON g.game_id = p.game_id " \
           f"JOIN prediction_outcomes o ON o.prediction_id = p.id WHERE {w}"
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
    # Player props of finished seasons live in archived_prediction_stats (polypicker/archive.py): fold them back in.
    from polypicker.archive import merge as merge_archived
    states, prob, mae, calib = merge_archived(conn, "nba", kind, season, params.get("m"), states, prob, mae, calib)
    active = q(conn, """SELECT id, target, version, algorithm, train_start, train_end, validation_start,
                               validation_end, metrics, trained_at FROM models
                       WHERE is_active AND target NOT LIKE 'nfl_%' AND target NOT LIKE 'soccer_%' ORDER BY target""")
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
    def versus(join_sql: str) -> dict | None:
        return q1(conn, f"""
        WITH ml AS (
            SELECT DISTINCT ON (p.game_id, p.kind) p.game_id, p.probability ph, (g.home_score > g.away_score) hw
            {base} AND p.market = 'moneyline' AND p.side = 'home' AND o.graded_state = 'final'
            ORDER BY p.game_id, p.kind, p.revision DESC),
        j AS ({join_sql})
        SELECT count(*) n,
               avg(((ph > 0.5) = hw)::int) model_acc, avg(((pm > 0.5) = hw)::int) market_acc,
               avg(-ln(greatest(CASE WHEN hw THEN ph ELSE 1 - ph END, 1e-9))) model_ll,
               avg(-ln(greatest(CASE WHEN hw THEN pm ELSE 1 - pm END, 1e-9))) market_ll,
               avg(power(ph - hw::int, 2)) model_brier, avg(power(pm - hw::int, 2)) market_brier,
               count(*) FILTER (WHERE (ph > 0.5) <> (pm > 0.5)) disagree_n,
               avg(((ph > 0.5) = hw)::int) FILTER (WHERE (ph > 0.5) <> (pm > 0.5)) disagree_model_right
        FROM j""", **params)

    def shape(r: dict | None, source: str) -> dict | None:
        if not r or not r["n"]:
            return None
        return {"n": r["n"], "source": source,
                "model": {"accuracy": _r(r["model_acc"]), "log_loss": _r(r["model_ll"]), "brier": _r(r["model_brier"])},
                "market": {"accuracy": _r(r["market_acc"]), "log_loss": _r(r["market_ll"]), "brier": _r(r["market_brier"])},
                "disagreements": {"n": r["disagree_n"], "model_right": _r(r["disagree_model_right"])}}

    pm = shape(versus("""SELECT ml.*, x.p_home_pregame pm FROM ml JOIN polymarket_nba x USING (game_id)
                         WHERE x.p_home_pregame IS NOT NULL"""),
               "Polymarket moneyline market, last traded price before tip-off")
    if pm:
        by_market.setdefault("moneyline", {})["vs_polymarket"] = pm
    market = q1(conn, f"""
        WITH ml AS (
            SELECT DISTINCT ON (p.game_id, p.kind) p.game_id, p.probability ph, (g.home_score > g.away_score) hw
            {base} AND p.market = 'moneyline' AND p.side = 'home' AND o.graded_state = 'final'
            ORDER BY p.game_id, p.kind, p.revision DESC),
        j AS (SELECT ml.*, od.p_home pm FROM ml JOIN nba_odds od USING (game_id)
              WHERE od.p_home IS NOT NULL AND od.is_closing)
        SELECT count(*) n,
               avg(((ph > 0.5) = hw)::int) model_acc, avg(((pm > 0.5) = hw)::int) market_acc,
               avg(-ln(greatest(CASE WHEN hw THEN ph ELSE 1 - ph END, 1e-9))) model_ll,
               avg(-ln(greatest(CASE WHEN hw THEN pm ELSE 1 - pm END, 1e-9))) market_ll,
               avg(power(ph - hw::int, 2)) model_brier, avg(power(pm - hw::int, 2)) market_brier,
               count(*) FILTER (WHERE (ph > 0.5) <> (pm > 0.5)) disagree_n,
               avg(((ph > 0.5) = hw)::int) FILTER (WHERE (ph > 0.5) <> (pm > 0.5)) disagree_model_right
        FROM j""", **params)
    if market and market["n"]:
        by_market.setdefault("moneyline", {})["vs_market"] = {
            "n": market["n"], "source": "Closing sportsbook moneyline (ESPN odds feed), bookmaker margin removed",
            "model": {"accuracy": _r(market["model_acc"]), "log_loss": _r(market["model_ll"]), "brier": _r(market["model_brier"])},
            "market": {"accuracy": _r(market["market_acc"]), "log_loss": _r(market["market_ll"]), "brier": _r(market["market_brier"])},
            "disagreements": {"n": market["disagree_n"], "model_right": _r(market["disagree_model_right"])}}
    if winner and winner["n"]:
        by_market.setdefault("moneyline", {})["winner_accuracy"] = {"n": winner["n"], "value": _r(winner["accuracy"])}
    return {"data": {"filters": {"kind": kind, "season": season, "category": category}, "markets": by_market,
                     "active_models": active},
            "meta": prediction_meta(extra={"note": "Backtest metrics use point-in-time forecasts regenerated after "
                                                   "the fact; live metrics accumulate as published forecasts are graded."})}


def _r(v, n: int = 4):
    return None if v is None else round(float(v), n)


# ---------------------------------------------------------------- value finder

def _american(dec: float | None) -> str | None:
    if not dec or dec <= 1:
        return None
    return f"+{round((dec - 1) * 100)}" if dec >= 2 else f"{round(-100 / (dec - 1))}"


@router.get("/value/nba")
def value_picks(conn: Connection = Depends(get_conn)):
    """Open value bets for upcoming NBA games: our probability vs the best price on offer."""
    rows = q(conn, """
        SELECT v.id, v.game_id, v.market, v.player_id, pl.full_name AS player_name, v.side, v.line,
               v.last_our_p AS our_p, v.last_price_dec AS price_dec, v.last_book AS book, v.last_ev AS ev, v.kelly,
               v.price_dec AS flagged_price_dec, v.flagged_at, v.last_seen_at, g.tipoff_utc,
               h.abbreviation home, a.abbreviation away, h.team_id home_id, a.team_id away_id, pt.abbreviation player_team
        FROM value_picks v JOIN games g USING (game_id)
        JOIN teams h ON h.team_id = g.home_team_id JOIN teams a ON a.team_id = g.away_team_id
        LEFT JOIN players pl ON pl.player_id = v.player_id
        LEFT JOIN LATERAL (SELECT t.abbreviation FROM player_game_stats p JOIN teams t ON t.team_id = p.team_id
                           JOIN games pg ON pg.game_id = p.game_id
                           WHERE p.player_id = v.player_id ORDER BY pg.tipoff_utc DESC LIMIT 1) pt ON TRUE
        WHERE g.status = 'scheduled' AND g.tipoff_utc > now() AND v.graded_at IS NULL
          AND v.last_seen_at > now() - interval '12 hours'
        ORDER BY v.last_ev DESC""")
    for r in rows:
        r["price_american"] = _american(r["price_dec"])
    return {"data": rows, "meta": prediction_meta(extra={
        "note": "Informational only. Expected value uses PolyPicker's probability (props: blended 30% model / 70% market) "
                "against the best price we found; stakes are quarter-Kelly as a share of bankroll."})}


@router.get("/value/nba/record")
@cached(ttl=300)
def value_record(conn: Connection = Depends(get_conn)):
    """Track record: live picks (graded) by EV threshold, closing-line value, plus stored backtests."""
    live = q(conn, """
        SELECT CASE WHEN market = 'moneyline' THEN 'winners' ELSE 'props' END AS market, count(*) n,
               avg((result = 'win')::int) FILTER (WHERE result IN ('win','loss')) win_rate,
               sum(profit) units, avg(profit) roi, avg(clv_prob) avg_clv, avg(beat_close::int) beat_close_rate
        FROM value_picks WHERE graded_at IS NOT NULL AND result <> 'void' GROUP BY 1""")
    recent = q(conn, """
        SELECT v.game_id, v.market, pl.full_name player_name, v.side, v.line, v.price_dec, v.book, v.ev, v.result,
               v.profit, v.clv_prob, v.beat_close, g.game_date_et, h.abbreviation home, a.abbreviation away
        FROM value_picks v JOIN games g USING (game_id) JOIN teams h ON h.team_id = g.home_team_id
        JOIN teams a ON a.team_id = g.away_team_id LEFT JOIN players pl ON pl.player_id = v.player_id
        WHERE v.graded_at IS NOT NULL ORDER BY v.graded_at DESC, v.ev DESC LIMIT 30""")
    backtests = q(conn, """SELECT DISTINCT ON (market, season) market, season, created_at, report
                           FROM value_backtests ORDER BY market, season, created_at DESC""")
    for r in recent:
        r["price_american"] = _american(r["price_dec"])
    return {"data": {"live": live, "recent": recent, "backtests": backtests}, "meta": prediction_meta()}

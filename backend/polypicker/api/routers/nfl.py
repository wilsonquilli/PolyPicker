"""NFL endpoints (/api/v1/nfl/...): schedule by week, game detail + box scores, forecasts, custom lines,
teams, standings, players, search, model performance and freshness.

Response shapes deliberately mirror the NBA endpoints (GameSummary, predictions payload, performance) so
the web app reuses its components. NFL additions: week labels, W-L-T records, venue (roof, surface).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection

from polypicker.api.deps import cached, get_conn, q, q1, today_et
from polypicker.api.freshness import stamp
from polypicker.api.queries import meta
from polypicker.config import DISCLAIMER
from polypicker.domain.markets import Market, ThresholdKind, round_probability_pair
from polypicker.ml.distributions import normal_over, spread_cover
from polypicker.nfl.markets import MARKET_STAT, NOUN, PLAYER_MARKETS, game_label, player_label

router = APIRouter(prefix="/nfl", tags=["nfl"])

NFL_ATTRIBUTION = ("NFL data from nflverse (nflverse.com, CC BY 4.0 where applicable); live scores from ESPN. "
                   "PolyPicker is not affiliated with the NFL or ESPN.")
WEEK_LABEL = {"WC": "Wild Card", "DIV": "Divisional Round", "CON": "Conference Championships", "SB": "Super Bowl"}
Kind = Literal["live", "backtest"]
CATEGORY = {"winner": ["moneyline", "team_points"], "spread": ["spread"], "total": ["total"],
            **{s: [m] for s, m in PLAYER_MARKETS.items()}}
Category = Literal["winner", "spread", "total", "passing_yards", "passing_tds", "rushing_yards", "receptions",
                   "receiving_yards", "scrimmage_tds"]

GAME_SELECT = """
SELECT g.game_id, g.nflverse_id, g.season, g.season_type, g.game_type, g.week, g.kickoff_utc, g.game_date_et, g.status,
       g.home_score, g.away_score, g.winner_team_id, g.home_team_id, g.away_team_id, g.overtime, g.neutral_site,
       g.div_game, g.roof, g.surface, g.stadium, g.home_rest, g.away_rest
FROM nfl_games g
"""


def _meta(**kw) -> dict:
    m = meta(**kw)
    m["attribution"] = NFL_ATTRIBUTION
    return m


def _pmeta(**kw) -> dict:
    m = _meta(**kw)
    m["disclaimer"] = DISCLAIMER
    return m


def week_label(game_type: str, week: int) -> str:
    return WEEK_LABEL.get(game_type, f"Week {week}")


def teams_by_id(conn: Connection) -> dict[int, dict]:
    out = {}
    for r in q(conn, "SELECT team_id, abbreviation, name, full_name, conference, division, color, color2 FROM nfl_teams"):
        r["city"] = r["full_name"].removesuffix(" " + r["name"]).strip()
        out[r["team_id"]] = r
    return out


def resolve_team(conn: Connection, ident: str) -> dict | None:
    if ident.isdigit():
        return q1(conn, "SELECT * FROM nfl_teams WHERE team_id = :i", i=int(ident))
    return q1(conn, "SELECT * FROM nfl_teams WHERE upper(abbreviation) = upper(:a)", a=ident)


def _record(w: int, l_: int, t: int) -> str:
    return f"{w}-{l_}" + (f"-{t}" if t else "")


def records_before(conn: Connection, games: list[dict]) -> dict[tuple[int, int], str]:
    """Each team's regular-season W-L(-T) entering each listed game."""
    if not games:
        return {}
    pairs = [(g["game_id"], g["home_team_id"]) for g in games] + [(g["game_id"], g["away_team_id"]) for g in games]
    rows = q(conn, """
        WITH target AS (
          SELECT t.game_id, t.team_id, g.season, g.kickoff_utc
          FROM unnest(CAST(:gids AS bigint[]), CAST(:tids AS int[])) AS t(game_id, team_id)
          JOIN nfl_games g ON g.game_id = t.game_id)
        SELECT t.game_id, t.team_id,
               count(*) FILTER (WHERE g2.winner_team_id = t.team_id) AS w,
               count(*) FILTER (WHERE g2.winner_team_id IS NOT NULL AND g2.winner_team_id <> t.team_id) AS l,
               count(*) FILTER (WHERE g2.game_id IS NOT NULL AND g2.winner_team_id IS NULL) AS t
        FROM target t
        LEFT JOIN nfl_games g2 ON g2.season = t.season AND g2.season_type = 'regular' AND g2.status = 'final'
             AND g2.kickoff_utc < t.kickoff_utc AND (g2.home_team_id = t.team_id OR g2.away_team_id = t.team_id)
        GROUP BY 1, 2""", gids=[p[0] for p in pairs], tids=[p[1] for p in pairs])
    return {(r["game_id"], r["team_id"]): _record(r["w"], r["l"], r["t"]) for r in rows}


def headlines(conn: Connection, game_ids: list[int]) -> dict[int, dict]:
    if not game_ids:
        return {}
    rows = q(conn, """
        WITH latest AS (
          SELECT DISTINCT ON (game_id) game_id, kind, revision FROM nfl_predictions
          WHERE game_id = ANY(:ids) AND market = 'moneyline' ORDER BY game_id, (kind = 'live') DESC, revision DESC)
        SELECT p.game_id, p.kind, p.revision, p.market, p.side, p.probability, p.push_probability, p.projection,
               p.published_at, p.data_cutoff_utc
        FROM nfl_predictions p JOIN latest l USING (game_id, kind, revision)
        WHERE p.market IN ('moneyline', 'team_points')""", ids=game_ids)
    out: dict[int, dict] = {}
    for r in rows:
        h = out.setdefault(r["game_id"], {"kind": r["kind"], "revision": r["revision"], "published_at": r["published_at"],
                                         "data_cutoff_utc": r["data_cutoff_utc"]})
        if r["market"] == "moneyline" and r["side"] == "home":
            h["p_home_win"], h["p_tie"] = r["probability"], r["push_probability"]
        elif r["market"] == "team_points":
            h[f"projected_{r['side']}_points"] = r["projection"]
    for h in out.values():
        p = h.get("p_home_win", 0.5)
        h["p_away_win"] = 1 - p
        h["p_home_pct"], h["p_away_pct"] = round_probability_pair(p)
        h["favorite"] = "home" if p >= 0.5 else "away"
        edge = abs(p - 0.5)
        h["favorite_label"] = "toss-up" if edge < 0.05 else "slight favorite" if edge < 0.15 else "favorite"
        h.setdefault("projected_home_points", None)
        h.setdefault("projected_away_points", None)
    stamp(out, "nfl", conn)
    return out


def game_summaries(conn: Connection, rows: list[dict]) -> list[dict]:
    teams = teams_by_id(conn)
    heads = headlines(conn, [r["game_id"] for r in rows])
    recs = records_before(conn, rows)
    from polypicker.api import reasons
    why = reasons.nfl(conn, rows, heads)
    out = []
    for r in rows:
        def side(tid, score):
            return {**teams[tid], "score": score, "record": recs.get((r["game_id"], tid))}
        out.append({
            "game_id": r["game_id"], "season": r["season"], "season_label": f"{r['season']} season",
            "season_type": r["season_type"], "is_cup": False, "week": r["week"], "game_type": r["game_type"],
            "label": week_label(r["game_type"], r["week"]), "sub_label": "Neutral site" if r["neutral_site"] else None,
            "tipoff_utc": r["kickoff_utc"], "game_date": r["game_date_et"], "status": r["status"],
            "home": side(r["home_team_id"], r["home_score"]), "away": side(r["away_team_id"], r["away_score"]),
            "winner_team_id": r["winner_team_id"], "overtime": r["overtime"],
            "prediction": heads.get(r["game_id"]), "reasons": why.get(r["game_id"]),
        })
    return out


def current_week(conn: Connection) -> dict | None:
    """The week of the next unfinished game (or the most recent week once the season is over)."""
    row = q1(conn, """SELECT season, week, game_type FROM nfl_games WHERE status <> 'final' AND game_date_et >= :d - 1
                      ORDER BY kickoff_utc LIMIT 1""", d=today_et())
    return row or q1(conn, "SELECT season, week, game_type FROM nfl_games ORDER BY kickoff_utc DESC LIMIT 1")


# ---------------------------------------------------------------- schedule

@router.get("/games")
def list_games(date_: date | None = Query(None, alias="date"), season: int | None = None, week: int | None = None,
               team: str | None = None, status: list[str] | None = Query(None), date_from: date | None = None,
               date_to: date | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
               conn: Connection = Depends(get_conn)):
    """Games by week (default: the current week), date, date range, team or season."""
    where, params = [], {}
    if not (date_ or season or week or team or date_from or date_to):
        cw = current_week(conn)
        if cw:
            season, week = cw["season"], cw["week"]
    if date_:
        where.append("g.game_date_et = :d"); params["d"] = date_
    if season is not None:
        where.append("g.season = :s"); params["s"] = season
    if week is not None:
        if season is None:
            cw = current_week(conn)
            where.append("g.season = :s"); params["s"] = cw["season"] if cw else None
        where.append("g.week = :w"); params["w"] = week
    if date_from:
        where.append("g.game_date_et >= :df"); params["df"] = date_from
    if date_to:
        where.append("g.game_date_et <= :dt"); params["dt"] = date_to
    if team:
        t = resolve_team(conn, team)
        if not t:
            raise HTTPException(404, f"Unknown team {team!r}")
        where.append("(g.home_team_id = :t OR g.away_team_id = :t)"); params["t"] = t["team_id"]
    if status:
        where.append("g.status = ANY(:st)"); params["st"] = list(status)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    total = q1(conn, f"SELECT count(*) n FROM nfl_games g {w}", **params)["n"]
    rows = q(conn, f"{GAME_SELECT} {w} ORDER BY g.kickoff_utc, g.game_id LIMIT :lim OFFSET :off", **params,
             lim=limit, off=offset)
    return {"data": game_summaries(conn, rows), "meta": _meta(total=total, limit=limit, offset=offset)}


@router.get("/games/weeks")
def game_weeks(season: int | None = None, conn: Connection = Depends(get_conn)):
    """Weeks of a season with date range and status counts -- drives the week picker."""
    cw = current_week(conn)
    s = season or (cw["season"] if cw else None)
    rows = q(conn, """
        SELECT season, week, min(game_type) game_type, min(game_date_et) start_date, max(game_date_et) end_date,
               count(*) games, count(*) FILTER (WHERE status = 'final') final,
               count(*) FILTER (WHERE status = 'scheduled') scheduled
        FROM nfl_games WHERE season = :s GROUP BY 1, 2 ORDER BY 2""", s=s)
    for r in rows:
        r["label"] = week_label(r["game_type"], r["week"])
    return {"data": rows, "meta": _meta(extra={"season": s, "current": cw, "today": today_et()})}


def _game_row(conn: Connection, game_id: int) -> dict:
    row = q1(conn, f"{GAME_SELECT} WHERE g.game_id = :g", g=game_id)
    if not row:
        raise HTTPException(404, f"Game {game_id} not found")
    return row


def team_form(conn: Connection, team_id: int, season: int, before, n: int) -> dict:
    rows = q(conn, """
        SELECT g.game_date_et, (g.home_team_id = :t) AS home, g.winner_team_id,
               CASE WHEN g.home_team_id = :t THEN g.home_score ELSE g.away_score END AS pts,
               CASE WHEN g.home_team_id = :t THEN g.away_score ELSE g.home_score END AS opp_pts,
               o.abbreviation AS opponent
        FROM nfl_games g JOIN nfl_teams o ON o.team_id = CASE WHEN g.home_team_id = :t THEN g.away_team_id ELSE g.home_team_id END
        WHERE g.status = 'final' AND g.season = :s AND g.kickoff_utc < :b AND (g.home_team_id = :t OR g.away_team_id = :t)
        ORDER BY g.kickoff_utc DESC LIMIT :n""", t=team_id, s=season, b=before, n=n)
    res = [{"date": r["game_date_et"], "opponent": r["opponent"], "home": r["home"],
            "result": "T" if r["winner_team_id"] is None else ("W" if r["winner_team_id"] == team_id else "L"),
            "points": r["pts"], "opponent_points": r["opp_pts"]} for r in rows]
    w = sum(x["result"] == "W" for x in res)
    t = sum(x["result"] == "T" for x in res)
    return {"games": len(res), "wins": w, "losses": len(res) - w - t, "ties": t,
            "avg_points": round(sum(x["points"] for x in res) / len(res), 1) if res else None,
            "avg_margin": round(sum(x["points"] - x["opponent_points"] for x in res) / len(res), 1) if res else None,
            "results": res}


@router.get("/games/{game_id}")
def get_game(game_id: int, conn: Connection = Depends(get_conn)):
    """Game detail: venue, line score, team box, recent form, rest, head-to-head."""
    g = _game_row(conn, game_id)
    summary = game_summaries(conn, [g])[0]
    stats = q(conn, """SELECT * FROM nfl_team_game_stats WHERE game_id = :g""", g=game_id)
    drop = {"source_id", "ingestion_run_id", "source_updated_at", "updated_at"}
    stats = [{k: v for k, v in s.items() if k not in drop} for s in stats]
    line = [{k: s[k] for k in ("team_id", "q1_points", "q2_points", "q3_points", "q4_points", "ot_points", "points")}
            for s in stats if s.get("q1_points") is not None] or None
    if line:
        line.sort(key=lambda x: x["team_id"] != g["away_team_id"])
    ctx = {}
    for side, tid, rest in (("home", g["home_team_id"], g["home_rest"]), ("away", g["away_team_id"], g["away_rest"])):
        ctx[side] = {"last5": team_form(conn, tid, g["season"], g["kickoff_utc"], 5), "rest_days": rest}
    h2h = q(conn, """
        SELECT game_id, game_date_et AS date, season, season_type, home_team_id, away_team_id, home_score, away_score,
               winner_team_id
        FROM nfl_games WHERE status = 'final' AND kickoff_utc < :k AND
             ((home_team_id = :a AND away_team_id = :b) OR (home_team_id = :b AND away_team_id = :a))
        ORDER BY kickoff_utc DESC LIMIT 6""", k=g["kickoff_utc"], a=g["home_team_id"], b=g["away_team_id"])
    ctx["head_to_head"] = h2h
    return {"data": {**summary, "venue": {"stadium": g["stadium"], "roof": g["roof"], "surface": g["surface"],
                                          "neutral_site": g["neutral_site"]},
                     "div_game": g["div_game"], "line_score": line, "team_stats": stats, "context": ctx},
            "meta": _meta()}


@router.get("/games/{game_id}/boxscore")
def boxscore(game_id: int, conn: Connection = Depends(get_conn)):
    _game_row(conn, game_id)
    players = q(conn, """
        SELECT s.player_id, p.full_name, coalesce(s.position, p.position) AS position, s.team_id, s.participation,
               s.status_comment, s.offense_snaps, s.offense_pct, s.completions, s.pass_attempts, s.passing_yards,
               s.passing_tds, s.interceptions, s.sacks, s.carries, s.rushing_yards, s.rushing_tds, s.targets,
               s.receptions, s.receiving_yards, s.receiving_tds, s.fumbles_lost
        FROM nfl_player_game_stats s JOIN nfl_players p USING (player_id)
        WHERE s.game_id = :g ORDER BY s.team_id, s.participation DESC, coalesce(s.offense_snaps, 0) DESC""", g=game_id)
    return {"data": {"players": players, "complete": bool(players)}, "meta": _meta()}


# ---------------------------------------------------------------- forecasts

def _latest_revision(conn: Connection, game_id: int, kind: str | None, revision: int | None) -> dict | None:
    where = "game_id = :g" + (" AND kind = :k" if kind else "") + (" AND revision = :r" if revision else "")
    return q1(conn, f"""SELECT kind, revision, max(published_at) published_at, max(data_cutoff_utc) data_cutoff_utc
                       FROM nfl_predictions WHERE {where} GROUP BY kind, revision
                       ORDER BY (kind = 'live') DESC, revision DESC LIMIT 1""", g=game_id, k=kind, r=revision)


def freshness(conn: Connection) -> dict:
    ing = q1(conn, """SELECT finished_at, source_updated_at FROM ingestion_runs
                      WHERE status = 'succeeded' AND job IN ('nfl_ingest', 'nfl_espn_sync') ORDER BY finished_at DESC LIMIT 1""")
    pred = q1(conn, """SELECT finished_at FROM prediction_runs WHERE status = 'succeeded' AND kind = 'live'
                       AND stats->>'league' = 'nfl' ORDER BY finished_at DESC LIMIT 1""")
    return {"last_data_sync_utc": ing and ing["finished_at"], "source_updated_utc": ing and ing["source_updated_at"],
            "last_live_forecast_run_utc": pred and pred["finished_at"]}


def _outcome(r: dict) -> dict | None:
    if not r["graded_state"]:
        return None
    return {"state": r["graded_state"], "result": r["outcome"], "observed": r["observed_value"]}


POS_ORDER = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FB": 4}


@router.get("/games/{game_id}/predictions")
def game_predictions(game_id: int, category: list[Category] | None = Query(None), kind: Kind | None = None,
                     revision: int | None = None, conn: Connection = Depends(get_conn)):
    """Latest forecast for a game: winner, projected score, spread/total, player props."""
    game = q1(conn, "SELECT * FROM nfl_games WHERE game_id = :g", g=game_id)
    if not game:
        raise HTTPException(404, f"Game {game_id} not found")
    fresh = freshness(conn)
    rev = _latest_revision(conn, game_id, kind, revision)
    if rev is None:
        st = "pending" if game["status"] == "scheduled" else "unavailable"
        return {"data": {"game_id": game_id, "forecast_status": st, "markets": None, "players": []},
                "meta": _pmeta(extra={"freshness": fresh})}
    status = "backtest" if rev["kind"] == "backtest" else (
        "stale" if game["status"] == "scheduled" and fresh["last_data_sync_utc"] and rev["data_cutoff_utc"] < fresh["last_data_sync_utc"]
        else "current")
    markets = sum((CATEGORY[c] for c in (category or CATEGORY)), [])
    rows = q(conn, """
        SELECT p.id, p.market, p.subject_type, p.subject_id, p.side, p.threshold_kind, p.threshold, p.probability,
               p.push_probability, p.projection, p.label, p.details, mo.version AS model_version,
               o.graded_state, o.outcome, o.observed_value
        FROM nfl_predictions p JOIN models mo ON mo.id = p.model_id
        LEFT JOIN nfl_prediction_outcomes o ON o.prediction_id = p.id
        WHERE p.game_id = :g AND p.kind = :k AND p.revision = :r AND p.market = ANY(:m)
        ORDER BY p.market, p.subject_id, p.threshold NULLS FIRST""", g=game_id, k=rev["kind"], r=rev["revision"], m=markets)
    teams = teams_by_id(conn)
    gm: dict = {}
    players: dict[str, dict] = {}
    excluded: dict[str, dict] = {}
    versions: dict[str, str] = {}
    for r in rows:
        mk = r["market"]
        versions[mk] = r["model_version"]
        if mk == "moneyline":
            ml = gm.setdefault("winner", {"label": "Game winner (a tie is a push)"})
            tid = int(r["subject_id"])
            ml[r["side"]] = {"team_id": tid, "abbreviation": teams[tid]["abbreviation"], "probability": r["probability"],
                             "outcome": _outcome(r)}
            ml["tie_probability"] = r["push_probability"]
            if r["side"] == "home" and r["details"]:
                ml["factors"] = r["details"].get("factors")
                ml["qb_out"] = r["details"].get("qb_out")
                d = r["details"].get("distribution") or {}
                if d:
                    ml["distribution"] = {"type": "normal", "mean": d.get("mu_margin_home"), "sd": d.get("sigma_win")}
        elif mk == "team_points":
            sc = gm.setdefault("projected_score", {})
            sc[r["side"]] = round(r["projection"], 1)
            sc[f"{r['side']}_outcome"] = _outcome(r)
        elif mk in ("spread", "total"):
            m = gm.setdefault(mk, {"lines": []})
            if r["threshold_kind"] is None:
                d = r["details"] or {}
                m.update(projection=round(r["projection"], 1), projection_label=r["label"],
                         probabilities_available=bool(d.get("lines_supported")),
                         distribution={"type": "normal",
                                       "mean": d.get("mu_margin_home") if mk == "spread" else d.get("mu_total"),
                                       "sd": d.get("sigma_margin") if mk == "spread" else d.get("sigma_total")},
                         projection_outcome=_outcome(r))
            else:
                m["lines"].append({"line": r["threshold"], "side": r["side"], "label": r["label"],
                                   "probability": r["probability"], "push_probability": r["push_probability"],
                                   "complement_probability": max(0.0, 1 - r["probability"] - (r["push_probability"] or 0)),
                                   "outcome": _outcome(r)})
        else:
            stat = MARKET_STAT[mk]
            d = r["details"] or {}
            if d.get("excluded"):
                ex = excluded.setdefault(r["subject_id"], {"player_id": r["subject_id"], "team_id": d.get("team_id"),
                                                           "position": d.get("position"), "reason": r["label"], "markets": []})
                ex["markets"].append(mk)
                continue
            p = players.setdefault(r["subject_id"], {"player_id": r["subject_id"], "markets": {}})
            entry = p["markets"].setdefault(stat, {"thresholds": [], "label": NOUN[stat]})
            if r["threshold_kind"] is None:
                p.update(team_id=d.get("team_id"), position=d.get("position"), availability=d.get("availability"))
                entry.update(expected=round(r["projection"], 2), rollout=d.get("rollout"), explanation=d.get("explanation"),
                             recent=d.get("recent"), thresholds_available=d.get("thresholds_supported"),
                             distribution=d.get("distribution"), outcome=_outcome(r))
            else:
                entry["thresholds"].append({
                    "kind": r["threshold_kind"], "threshold": r["threshold"], "label": r["label"],
                    "probability": r["probability"], "push_probability": r["push_probability"],
                    "complement_probability": max(0.0, 1 - r["probability"] - (r["push_probability"] or 0)),
                    "outcome": _outcome(r)})
    if "winner" in gm:
        gm["winner"]["home_pct"], gm["winner"]["away_pct"] = round_probability_pair(gm["winner"]["home"]["probability"])
    ids = list(players) + list(excluded)
    if ids:
        names = {r["player_id"]: r for r in q(conn, "SELECT player_id, full_name, position, headshot_url FROM nfl_players "
                                                    "WHERE player_id = ANY(:ids)", ids=ids)}
        for p in list(players.values()) + list(excluded.values()):
            n = names.get(p["player_id"], {})
            p["full_name"] = n.get("full_name")
            p["position"] = p.get("position") or n.get("position")
    plist = sorted(players.values(), key=lambda p: (POS_ORDER.get(p.get("position") or "", 9),
                                                    -max((m.get("expected") or 0) for m in p["markets"].values())))
    revisions = q(conn, """SELECT kind, revision, min(published_at) published_at FROM nfl_predictions
                           WHERE game_id = :g GROUP BY 1, 2 ORDER BY 1, 2""", g=game_id)
    return {"data": {
        "game_id": game_id, "game_status": game["status"], "forecast_status": status, "kind": rev["kind"],
        "revision": rev["revision"], "published_at": rev["published_at"], "data_cutoff_utc": rev["data_cutoff_utc"],
        "model_versions": versions, "markets": gm, "players": plist, "excluded_players": list(excluded.values()),
        "revisions": revisions,
        "notes": {
            "kind": "Backtest forecasts were regenerated after the fact using only data available before each game; "
                    "they were not published live." if rev["kind"] == "backtest" else None,
            "availability": "Player availability comes from the official NFL injury report and weekly roster moves "
                            "(injured reserve, releases, trades). Game-day inactive lists come out ~90 minutes before "
                            "kickoff and are not reflected until the next update.",
            "thresholds": "'60+' means at least 60. 'Over 59.5' means 60 or more. Ties settle the winner market as a push.",
        }}, "meta": _pmeta(extra={"freshness": fresh})}


@router.get("/games/{game_id}/predictions/custom")
def custom_threshold(game_id: int,
                     market: Literal["spread", "total", "player_passing_yards", "player_passing_tds",
                                     "player_rushing_yards", "player_receptions", "player_receiving_yards",
                                     "player_anytime_td"],
                     threshold: float = Query(..., ge=-80, le=700), threshold_kind: Literal["at_least", "over"] = "over",
                     side: Literal["home", "away"] = "home", player_id: str | None = None,
                     conn: Connection = Depends(get_conn)):
    """Probability for a user-chosen line from the stored predictive distribution (no model inference)."""
    from polypicker.nfl.ml.player_model import threshold_prob
    rev = _latest_revision(conn, game_id, None, None)
    if not rev:
        raise HTTPException(404, "No forecast for this game")
    if market.startswith("player_"):
        if not player_id:
            raise HTTPException(400, "player_id is required for player markets")
        if threshold < 0 or (threshold_kind == "at_least" and not float(threshold).is_integer()):
            raise HTTPException(400, "'at_least' thresholds must be non-negative integers")
        r = q1(conn, """SELECT details FROM nfl_predictions WHERE game_id = :g AND kind = :k AND revision = :rv
                        AND market = :m AND subject_id = :p AND threshold_kind IS NULL""",
               g=game_id, k=rev["kind"], rv=rev["revision"], m=market, p=player_id)
        if not r or not r["details"] or r["details"].get("excluded"):
            raise HTTPException(404, "No forecast for this player in this game")
        if not r["details"].get("thresholds_supported"):
            raise HTTPException(409, "Threshold probabilities are not published for this market (calibration gate)")
        dist = r["details"]["distribution"]
        kind = ThresholdKind(threshold_kind)
        tp = threshold_prob(dist, kind, threshold)
        lbl, expected = player_label(MARKET_STAT[market], kind, threshold), dist["mean"]
    else:
        r = q1(conn, """SELECT details FROM nfl_predictions WHERE game_id = :g AND kind = :k AND revision = :rv
                        AND market = :m AND threshold_kind IS NULL""", g=game_id, k=rev["kind"], rv=rev["revision"], m=market)
        d = r and r["details"]
        if not d or not d.get("lines_supported"):
            raise HTTPException(409, "Spread/total probabilities are not published for this game")
        if market == "spread":
            mu = d["mu_margin_home"] if side == "home" else -d["mu_margin_home"]
            tp, expected = spread_cover(mu, d["sigma_margin"], threshold), mu
            lbl = game_label(Market.SPREAD, ThresholdKind.HANDICAP, threshold, side)
        else:
            tp, expected = normal_over(d["mu_total"], d["sigma_total"], threshold), d["mu_total"]
            lbl = game_label(Market.TOTAL, ThresholdKind.OVER, threshold, "over")
    return {"data": {"game_id": game_id, "market": market, "player_id": player_id, "threshold": threshold, "label": lbl,
                     "expected": round(expected, 2), "p_hit": tp.p_hit, "p_push": tp.p_push, "p_miss": tp.p_miss,
                     "kind": rev["kind"], "revision": rev["revision"], "derived": True},
            "meta": _pmeta()}


# ---------------------------------------------------------------- teams, standings, players

def _standings(conn: Connection, season: int) -> list[dict]:
    rows = q(conn, """
        SELECT t.team_id, t.abbreviation, t.name, t.full_name, t.conference, t.division,
               count(g.game_id) FILTER (WHERE g.winner_team_id = t.team_id) AS wins,
               count(g.game_id) FILTER (WHERE g.winner_team_id IS NOT NULL AND g.winner_team_id <> t.team_id) AS losses,
               count(g.game_id) FILTER (WHERE g.game_id IS NOT NULL AND g.winner_team_id IS NULL) AS ties,
               coalesce(sum(CASE WHEN g.home_team_id = t.team_id THEN g.home_score ELSE g.away_score END), 0) AS points_for,
               coalesce(sum(CASE WHEN g.home_team_id = t.team_id THEN g.away_score ELSE g.home_score END), 0) AS points_against,
               count(g.game_id) FILTER (WHERE g.div_game AND g.winner_team_id = t.team_id) AS div_wins,
               count(g.game_id) FILTER (WHERE g.div_game AND g.winner_team_id IS NOT NULL AND g.winner_team_id <> t.team_id) AS div_losses
        FROM nfl_teams t
        LEFT JOIN nfl_games g ON g.season = :s AND g.season_type = 'regular' AND g.status = 'final'
             AND (g.home_team_id = t.team_id OR g.away_team_id = t.team_id)
        GROUP BY t.team_id""", s=season)
    for r in rows:
        gp = r["wins"] + r["losses"] + r["ties"]
        r["games"] = gp
        r["win_pct"] = round((r["wins"] + 0.5 * r["ties"]) / gp, 3) if gp else None
        r["point_diff"] = r["points_for"] - r["points_against"]
        r["record"] = _record(r["wins"], r["losses"], r["ties"])
        r["city"] = r["full_name"].removesuffix(" " + r["name"]).strip()
    return sorted(rows, key=lambda r: (-(r["win_pct"] or 0), -r["point_diff"]))


def _season(conn: Connection, season: int | None) -> int:
    if season:
        return season
    cw = current_week(conn)
    return cw["season"] if cw else q1(conn, "SELECT max(season) s FROM nfl_games")["s"]


@router.get("/teams")
def list_teams(season: int | None = None, conn: Connection = Depends(get_conn)):
    s = _season(conn, season)
    return {"data": _standings(conn, s), "meta": _meta(extra={"season": s})}


@router.get("/standings")
def standings(season: int | None = None, conn: Connection = Depends(get_conn)):
    """Regular-season standings by conference and division (W-L-T; a tie counts half a win)."""
    s = _season(conn, season)
    rows = _standings(conn, s)
    out: dict[str, dict[str, list]] = {}
    for r in rows:
        out.setdefault(r["conference"], {}).setdefault(r["division"], []).append(r)
    for conf in out.values():
        for div_rows in conf.values():
            leader = div_rows[0]
            for r in div_rows:
                r["games_behind"] = ((leader["wins"] - r["wins"]) + (r["losses"] - leader["losses"])) / 2
    return {"data": out, "meta": _meta(extra={"season": s})}


@router.get("/teams/{ident}")
def team_detail(ident: str, season: int | None = None, conn: Connection = Depends(get_conn)):
    t = resolve_team(conn, ident)
    if not t:
        raise HTTPException(404, f"Unknown team {ident!r}")
    s = _season(conn, season)
    st = next((r for r in _standings(conn, s) if r["team_id"] == t["team_id"]), None)
    games = q(conn, f"{GAME_SELECT} WHERE g.season = :s AND (g.home_team_id = :t OR g.away_team_id = :t) ORDER BY g.kickoff_utc",
              s=s, t=t["team_id"])
    summ = game_summaries(conn, games)
    leaders = q(conn, """
        SELECT s.player_id, p.full_name, coalesce(s.position, p.position) position, count(*) FILTER (WHERE s.participation='played') games,
               sum(s.passing_yards) passing_yards, sum(s.passing_tds) passing_tds, sum(s.rushing_yards) rushing_yards,
               sum(s.receptions) receptions, sum(s.receiving_yards) receiving_yards, sum(s.scrimmage_tds) scrimmage_tds
        FROM nfl_player_game_stats s JOIN nfl_games g USING (game_id) JOIN nfl_players p USING (player_id)
        WHERE g.season = :s AND s.team_id = :t GROUP BY 1, 2, 3
        HAVING count(*) FILTER (WHERE s.participation='played') > 0
        ORDER BY coalesce(sum(s.passing_yards), 0) + coalesce(sum(s.rushing_yards), 0) + coalesce(sum(s.receiving_yards), 0) DESC
        LIMIT 12""", s=s, t=t["team_id"])
    drop = {"source_id", "ingestion_run_id", "source_updated_at", "updated_at"}
    return {"data": {"team": {k: v for k, v in t.items() if k not in drop}, "season": s, "standing": st,
                     "games": summ, "leaders": leaders}, "meta": _meta()}


@router.get("/players/{player_id}")
def player_detail(player_id: str, season: int | None = None, conn: Connection = Depends(get_conn)):
    p = q1(conn, """SELECT player_id, full_name, first_name, last_name, position, jersey, birth_date, height_inches,
                           weight_lbs, college, headshot_url FROM nfl_players WHERE player_id = :p""", p=player_id)
    if not p:
        raise HTTPException(404, f"Player {player_id} not found")
    s = _season(conn, season)
    log = q(conn, """
        SELECT g.game_id, g.game_date_et AS date, g.week, g.game_type, s.team_id, s.opponent_team_id, s.is_home,
               o.abbreviation AS opponent, s.participation, s.status_comment, s.offense_snaps, s.offense_pct,
               s.completions, s.pass_attempts, s.passing_yards, s.passing_tds, s.interceptions, s.carries, s.rushing_yards,
               s.rushing_tds, s.targets, s.receptions, s.receiving_yards, s.receiving_tds, s.scrimmage_tds,
               g.home_score, g.away_score, g.winner_team_id
        FROM nfl_player_game_stats s JOIN nfl_games g USING (game_id) JOIN nfl_teams o ON o.team_id = s.opponent_team_id
        WHERE s.player_id = :p AND g.season = :s ORDER BY g.kickoff_utc DESC""", p=player_id, s=s)
    played = [r for r in log if r["participation"] == "played"]
    keys = ["pass_attempts", "passing_yards", "passing_tds", "carries", "rushing_yards", "targets", "receptions",
            "receiving_yards", "scrimmage_tds"]
    avg = {k: round(sum(r[k] or 0 for r in played) / len(played), 1) if played else None for k in keys}
    team = q1(conn, """SELECT t.team_id, t.abbreviation, t.full_name FROM nfl_player_game_stats s JOIN nfl_games g USING (game_id)
                       JOIN nfl_teams t ON t.team_id = s.team_id WHERE s.player_id = :p ORDER BY g.kickoff_utc DESC LIMIT 1""",
               p=player_id)
    return {"data": {"player": p, "current_team": team, "season": s, "games_played": len(played),
                     "season_averages": avg, "game_log": log}, "meta": _meta()}


@router.get("/search")
def search(q_: str = Query(..., alias="q", min_length=2), limit: int = Query(8, ge=1, le=25),
           conn: Connection = Depends(get_conn)):
    like = f"%{q_.lower()}%"
    teams = q(conn, """SELECT 'team' AS type, team_id::text AS id, full_name AS name, abbreviation AS subtitle FROM nfl_teams
                       WHERE lower(full_name) LIKE :l OR lower(abbreviation) = lower(:q) ORDER BY full_name LIMIT :n""",
              l=like, q=q_, n=limit)
    players = q(conn, """
        SELECT 'player' AS type, p.player_id AS id, p.full_name AS name, coalesce(p.position, '') AS subtitle
        FROM nfl_players p WHERE lower(p.full_name) LIKE :l AND EXISTS (
            SELECT 1 FROM nfl_player_game_stats s JOIN nfl_games g USING (game_id)
            WHERE s.player_id = p.player_id AND g.season >= (SELECT max(season) - 1 FROM nfl_games))
        ORDER BY p.full_name LIMIT :n""", l=like, n=limit)
    return {"data": (teams + players)[:limit], "meta": _meta()}


# ---------------------------------------------------------------- accuracy

@router.get("/line-movement")
@cached(ttl=300)
def line_movement_nfl(kind: str = Query("live", pattern="^(live|backtest)$"), conn: Connection = Depends(get_conn)):
    """Opening -> closing line moves vs our picks (see api/linemove.py)."""
    from polypicker.api.linemove import line_movement
    return {"data": line_movement(conn, "nfl", kind), "meta": _meta()}


@router.get("/models/performance")
@cached(ttl=300)
def model_performance(kind: Kind | None = None, season: int | None = None, category: list[Category] | None = Query(None),
                      conn: Connection = Depends(get_conn)):
    """Accuracy, Brier, calibration and MAE from graded NFL snapshots, the betting-market benchmark on the same
    games (benchmark; also the market model input when posted), and the held-out validation metrics of the active models."""
    where, params = ["1=1"], {}
    if kind:
        where.append("p.kind = :k"); params["k"] = kind
    if season:
        where.append("g.season = :s"); params["s"] = season
    if category:
        where.append("p.market = ANY(:m)"); params["m"] = sum((CATEGORY[c] for c in category), [])
    w = " AND ".join(where)
    # Restrict the latest-revision scan to the requested kind (backtests are most of the table).
    latest = f"""JOIN (SELECT game_id, kind, max(revision) r FROM nfl_predictions {'WHERE kind = :k' if kind else ''} GROUP BY 1, 2) lr
                ON lr.game_id = p.game_id AND lr.kind = p.kind AND lr.r = p.revision"""
    base = f"FROM nfl_predictions p {latest} JOIN nfl_games g ON g.game_id = p.game_id " \
           f"JOIN nfl_prediction_outcomes o ON o.prediction_id = p.id WHERE {w}"
    states = q(conn, f"SELECT p.market, o.graded_state, count(*) n {base} GROUP BY 1, 2 ORDER BY 1, 2", **params)
    prob = q(conn, f"""SELECT p.market, count(*) n, avg(o.brier) brier, avg(o.log_loss) log_loss,
                              avg(CASE WHEN o.outcome = 'hit' THEN 1.0 ELSE 0.0 END) hit_rate, avg(p.probability) mean_p
                       {base} AND o.graded_state = 'final' AND p.probability IS NOT NULL AND o.outcome IN ('hit','miss')
                       GROUP BY 1 ORDER BY 1""", **params)
    winner = q1(conn, f"""SELECT count(*) n, avg(CASE WHEN o.outcome = 'hit' THEN 1.0 ELSE 0.0 END) accuracy
                          {base} AND p.market = 'moneyline' AND o.graded_state = 'final' AND o.outcome IN ('hit','miss')
                          AND p.probability > 0.5""", **params)
    mae = q(conn, f"""SELECT p.market, count(*) n, avg(o.abs_error) mae {base} AND o.graded_state = 'final'
                      AND p.threshold_kind IS NULL AND o.abs_error IS NOT NULL GROUP BY 1 ORDER BY 1""", **params)
    calib = q(conn, f"""SELECT p.market, width_bucket(p.probability, 0, 1, 10) AS bucket, count(*) n,
                               avg(p.probability) mean_predicted, avg(CASE WHEN o.outcome = 'hit' THEN 1.0 ELSE 0.0 END) observed_rate
                        {base} AND o.graded_state = 'final' AND p.probability IS NOT NULL AND o.outcome IN ('hit','miss')
                        GROUP BY 1, 2 ORDER BY 1, 2""", **params)
    market = q1(conn, f"""
        SELECT count(*) n,
               avg(power((1 - (0.5 + 0.5 * erf(-g.market_spread_home / (13.5 * sqrt(2))))) - (g.home_score > g.away_score)::int, 2)) brier,
               avg(((g.market_spread_home > 0) = (g.home_score > g.away_score))::int) accuracy,
               avg(abs(g.market_spread_home - (g.home_score - g.away_score))) margin_mae,
               avg(abs(g.market_total - (g.home_score + g.away_score))) total_mae
        FROM nfl_games g WHERE g.status = 'final' AND g.home_score <> g.away_score AND g.market_spread_home IS NOT NULL
          AND g.market_spread_home <> 0 AND g.game_id IN (
            SELECT p.game_id {base} AND p.market = 'moneyline' AND o.graded_state = 'final')""", **params)
    # Player props of finished seasons live in archived_prediction_stats (polypicker/archive.py): fold them back in.
    from polypicker.archive import merge as merge_archived
    states, prob, mae, calib = merge_archived(conn, "nfl", kind, season, params.get("m"), states, prob, mae, calib)
    active = q(conn, """SELECT id, target, version, algorithm, train_start, train_end, validation_start, validation_end,
                               metrics, trained_at FROM models WHERE is_active AND target LIKE 'nfl_%' ORDER BY target""")
    by: dict[str, dict] = {}
    for r in states:
        by.setdefault(r["market"], {"graded_states": {}})["graded_states"][r["graded_state"]] = r["n"]
    for r in prob:
        by.setdefault(r["market"], {}).update(probability_metrics={
            "n": r["n"], "brier": _r(r["brier"]), "log_loss": _r(r["log_loss"]), "hit_rate": _r(r["hit_rate"]),
            "mean_predicted": _r(r["mean_p"])})
    for r in mae:
        by.setdefault(r["market"], {}).update(mae={"n": r["n"], "value": _r(r["mae"], 3)})
    for r in calib:
        by.setdefault(r["market"], {}).setdefault("calibration", []).append(
            {"bucket": r["bucket"], "n": r["n"], "mean_predicted": _r(r["mean_predicted"]), "observed_rate": _r(r["observed_rate"])})
    if winner and winner["n"]:
        by.setdefault("moneyline", {})["winner_accuracy"] = {"n": winner["n"], "value": _r(winner["accuracy"])}
    bench = None
    if market and market["n"]:
        bench = {"n": market["n"], "brier": _r(market["brier"]), "accuracy": _r(market["accuracy"]),
                 "margin_mae": _r(market["margin_mae"], 3), "total_mae": _r(market["total_mae"], 3),
                 "note": "Closing betting-market lines on the same games. When a game has a posted line, the model uses "
                         "it as an input. PolyPicker does not offer or facilitate wagering."}
    return {"data": {"filters": {"kind": kind, "season": season, "category": category}, "markets": by,
                     "market_benchmark": bench, "active_models": active},
            "meta": _pmeta(extra={"note": "Backtest metrics use point-in-time forecasts regenerated after the fact; "
                                          "live metrics accumulate as published forecasts are graded."})}


@router.get("/status")
def status(conn: Connection = Depends(get_conn)):
    return {"data": {"freshness": freshness(conn), "current_week": current_week(conn),
                     "active_models": q(conn, "SELECT target, version, trained_at FROM models WHERE is_active "
                                              "AND target LIKE 'nfl_%' ORDER BY target")}, "meta": _meta()}


def _r(v, n: int = 4):
    return None if v is None else round(float(v), n)

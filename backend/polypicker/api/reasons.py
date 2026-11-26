"""Plain-language reasons behind each upcoming forecast ("Why we pick the Lions").

Each reason is a code + params, not a sentence, so the web app can phrase and translate it (lib/reasons.ts):
``{"code": "form_hot", "side": "home", "favors": "home", "params": {"w": 8, "n": 10}}``. ``side`` is the team the
reason is about, ``favors`` the side it helps (None = neutral, e.g. weather). At most MAX reasons per game, most
important first: missing players, form, rest / schedule, table position, weather, venue.

Only data we already hold is used (forecast details, results, rest days, weather); everything is batched per sport
so a game list costs a handful of queries.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import Connection

from polypicker.api.deps import q

MAX = 4
OTHER = {"home": "away", "away": "home"}


def _r(code: str, side: str | None, favors: str | None, **params) -> dict:
    return {"code": code, "side": side, "favors": favors, "params": params}


def _details(conn: Connection, table: str, market: str, ids: list[int]) -> dict[int, dict]:
    rows = q(conn, f"""SELECT DISTINCT ON (game_id) game_id, details FROM {table}
                       WHERE game_id = ANY(:ids) AND market = :m AND side = 'home' AND kind = 'live'
                       ORDER BY game_id, revision DESC""", ids=ids, m=market)
    return {r["game_id"]: r["details"] or {} for r in rows}


def _form_us(conn: Connection, table: str, time_col: str, team_ids: list[int], n: int) -> dict[int, list[bool]]:
    """Last ``n`` results (True = win) per team, newest first. Ties count as not-a-win."""
    if not team_ids:
        return {}
    rows = q(conn, f"""
        SELECT team_id, array_agg(w ORDER BY t DESC) AS ws FROM (
          SELECT x.team_id, g.{time_col} t, g.winner_team_id = x.team_id AS w,
                 row_number() OVER (PARTITION BY x.team_id ORDER BY g.{time_col} DESC) rn
          FROM unnest(CAST(:tids AS bigint[])) AS x(team_id)
          JOIN {table} g ON (g.home_team_id = x.team_id OR g.away_team_id = x.team_id)
           AND g.status = 'final' AND g.{time_col} > now() - interval '400 days') s
        WHERE rn <= :n GROUP BY team_id""", tids=team_ids, n=n)
    return {r["team_id"]: [bool(v) for v in r["ws"]] for r in rows}


def _form_reasons(out: list, g: dict, form: dict[int, list[bool]], n: int, hot: int, cold: int) -> None:
    for side in ("home", "away"):
        ws = form.get(g[f"{side}_team_id"]) or []
        if len(ws) < n:
            continue
        w = sum(ws)
        if w >= hot:
            out.append(_r("form_hot", side, side, w=w, n=n))
        elif w <= cold:
            out.append(_r("form_cold", side, OTHER[side], w=w, n=n))


def _finish(out: list, pick: str | None) -> list[dict]:
    """Reasons backing the pick first (in priority order), then the ones against it, capped at MAX."""
    for_pick = [x for x in out if x["favors"] in (pick, None)]
    against = [x for x in out if x["favors"] not in (pick, None)]
    return (for_pick + against)[:MAX]


# ---------------------------------------------------------------- NBA

def nba(conn: Connection, games: list[dict], heads: dict[int, dict]) -> dict[int, list[dict]]:
    games = [g for g in games if g["status"] == "scheduled" and g["game_id"] in heads]
    if not games:
        return {}
    ids = [g["game_id"] for g in games]
    det = _details(conn, "predictions", "moneyline", ids)
    teams = list({t for g in games for t in (g["home_team_id"], g["away_team_id"])})
    form = _form_us(conn, "games", "tipoff_utc", teams, 10)
    # back-to-backs: a game the day before
    days = q(conn, """SELECT home_team_id, away_team_id, game_date_et FROM games
                      WHERE game_date_et BETWEEN :a AND :b AND (home_team_id = ANY(:t) OR away_team_id = ANY(:t))""",
             a=min(g["game_date_et"] for g in games) - timedelta(days=1), b=max(g["game_date_et"] for g in games), t=teams)
    played = {(r[k], r["game_date_et"]) for r in days for k in ("home_team_id", "away_team_id")}
    res = {}
    for g in games:
        d, pick = det.get(g["game_id"], {}), heads[g["game_id"]].get("favorite")
        out = []
        for side in ("home", "away"):
            ps = [p for p in ((d.get("absences") or {}).get(side) or {}).get("players") or []
                  if (p.get("status") or "").lower() == "out" and (p.get("usual_minutes") or 0) >= 20]
            ps.sort(key=lambda p: -(p.get("usual_minutes") or 0))
            if ps:
                out.append(_r("missing_players", side, OTHER[side], names=[p["name"] for p in ps[:2]], n=len(ps)))
        _form_reasons(out, g, form, 10, hot=8, cold=2)
        for side in ("home", "away"):
            if (g[f"{side}_team_id"], g["game_date_et"] - timedelta(days=1)) in played:
                out.append(_r("back_to_back", side, OTHER[side]))
        out.append(_r("home_court", "home", "home"))
        res[g["game_id"]] = _finish(out, pick)
    return res


# ---------------------------------------------------------------- NFL

def nfl(conn: Connection, games: list[dict], heads: dict[int, dict]) -> dict[int, list[dict]]:
    games = [g for g in games if g["status"] == "scheduled" and g["game_id"] in heads]
    if not games:
        return {}
    ids = [g["game_id"] for g in games]
    det = _details(conn, "nfl_predictions", "moneyline", ids)
    extra = {r["game_id"]: r for r in q(conn, """
        SELECT g.game_id, g.home_rest, g.away_rest, g.roof, g.neutral_site, w.wind_kmh, w.snow_cm, w.temp_c, w.precip_mm, w.indoor
        FROM nfl_games g LEFT JOIN game_weather w ON w.sport = 'nfl' AND w.game_id = g.game_id
        WHERE g.game_id = ANY(:ids)""", ids=ids)}
    form = _form_us(conn, "nfl_games", "kickoff_utc", list({t for g in games for t in (g["home_team_id"], g["away_team_id"])}), 5)
    res = {}
    for g in games:
        d, x, pick = det.get(g["game_id"], {}), extra.get(g["game_id"], {}), heads[g["game_id"]].get("favorite")
        out = []
        for side in ("home", "away"):
            if (d.get("qb_out") or {}).get(side):
                out.append(_r("qb_out", side, OTHER[side]))
        _form_reasons(out, g, form, 5, hot=4, cold=1)
        for side in ("home", "away"):
            rest, opp = x.get(f"{side}_rest"), x.get(f"{OTHER[side]}_rest")
            if rest is not None and rest >= 13:
                out.append(_r("off_bye", side, side))
            elif rest is not None and opp is not None and rest <= 5 and opp >= 7:
                out.append(_r("short_week", side, OTHER[side], days=int(rest)))
        indoor = x.get("indoor") or x.get("roof") in ("dome", "closed")
        if not indoor and x.get("temp_c") is not None:
            if (x.get("snow_cm") or 0) >= 0.5:
                out.append(_r("snow", None, None))
            elif (x.get("wind_kmh") or 0) >= 30:
                out.append(_r("wind", None, None, kmh=round(x["wind_kmh"])))
            elif x["temp_c"] <= -5:
                out.append(_r("cold", None, None, c=round(x["temp_c"])))
            elif (x.get("precip_mm") or 0) >= 2:
                out.append(_r("rain", None, None))
        out.append(_r("neutral_site", None, None) if x.get("neutral_site") else _r("home_field", "home", "home"))
        res[g["game_id"]] = _finish(out, pick)
    return res


# ---------------------------------------------------------------- soccer

def soccer(conn: Connection, games: list[dict], heads: dict[int, dict]) -> dict[int, list[dict]]:
    from polypicker.soccer.leagues import BY_COMPETITION
    games = [g for g in games if g["status"] == "scheduled" and g["game_id"] in heads]
    if not games:
        return {}
    teams = list({t for g in games for t in (g["home_team_id"], g["away_team_id"])})
    # last 5 results in any competition + the date of each team's previous match
    rows = q(conn, """
        SELECT team_id, array_agg(res ORDER BY t DESC) FILTER (WHERE rn <= 5) AS form, max(t) AS last_t FROM (
          SELECT x.team_id, g.kickoff_utc t,
                 CASE WHEN g.result = 'D' THEN 'D' WHEN (g.result = 'H') = (g.home_team_id = x.team_id) THEN 'W' ELSE 'L' END res,
                 row_number() OVER (PARTITION BY x.team_id ORDER BY g.kickoff_utc DESC) rn
          FROM unnest(CAST(:tids AS bigint[])) AS x(team_id)
          JOIN soccer_games g ON (g.home_team_id = x.team_id OR g.away_team_id = x.team_id)
           AND g.status = 'final' AND g.result IS NOT NULL AND g.kickoff_utc > now() - interval '200 days') s
        GROUP BY team_id""", tids=teams)
    form = {r["team_id"]: r["form"] or [] for r in rows}
    last = {r["team_id"]: r["last_t"] for r in rows}
    # league position, for league competitions
    pos: dict[tuple[str, int], dict[int, int]] = {}
    from polypicker.api.routers.soccer import _table
    for comp, season in {(g["competition"], g["season"]) for g in games}:
        lg = BY_COMPETITION.get(comp)
        if lg and lg.kind != "cup" and not lg.calendar_year:
            tbl = _table(conn, comp, season)
            if tbl and min(r["played"] for r in tbl) >= 5:
                pos[(comp, season)] = {r["team_id"]: r["position"] for r in tbl}
    res = {}
    for g in games:
        h, gid = heads[g["game_id"]], g["game_id"]
        pick = h.get("pick")
        out = []   # no "bookmakers give X Y%" reason: it would just repeat our own percentage back
        for side in ("home", "away"):
            f = form.get(g[f"{side}_team_id"]) or []
            if len(f) == 5:
                w, l_ = f.count("W"), f.count("L")
                if w >= 4:
                    out.append(_r("form_soccer_hot", side, side, form="".join(f), w=w))
                elif w == 0 and l_ >= 3:
                    out.append(_r("form_soccer_cold", side, OTHER[side], form="".join(f)))
        p = pos.get((g["competition"], g["season"]))
        if p and g["home_team_id"] in p and g["away_team_id"] in p:
            hp, ap = p[g["home_team_id"]], p[g["away_team_id"]]
            if abs(hp - ap) >= 6:
                better = "home" if hp < ap else "away"
                out.append(_r("table_gap", better, better, pos=min(hp, ap), opp_pos=max(hp, ap)))
        for side in ("home", "away"):
            t, ot = last.get(g[f"{side}_team_id"]), last.get(g[f"{OTHER[side]}_team_id"])
            if t and ot:
                rest, orest = (g["kickoff_utc"] - t).days, (g["kickoff_utc"] - ot).days
                if rest <= 3 and orest >= 5:
                    out.append(_r("congestion", side, OTHER[side], days=rest))
        neutral = g.get("neutral")
        out.append(_r("neutral_site", None, None) if neutral else _r("home_field", "home", "home"))
        res[gid] = _finish(out, pick)
    return res

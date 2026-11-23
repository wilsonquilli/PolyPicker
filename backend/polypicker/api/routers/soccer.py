"""Soccer endpoints (/api/v1/soccer/{league}/...). League slugs map to ESPN competitions via
``polypicker.soccer.leagues`` (epl -> eng.1, ucl -> uefa.champions, ...).

Matches by date, match detail (half-time, team stats, form, head-to-head), forecasts (home / draw / away,
expected goals, likely scores, totals, both teams to score), table, standings (groups / league phase / split
seasons + knockout ties), teams, search, accuracy and freshness. Forecasts and the ``result`` field are for 90
minutes; cup matches settled in extra time or on penalties also report the final score and shootout.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection

from polypicker.api.deps import cached, get_conn, q, q1, today_et
from polypicker.api.queries import meta
from polypicker.config import DISCLAIMER
from polypicker.soccer.leagues import BY_COMPETITION
from polypicker.soccer.leagues import LEAGUES as _LEAGUES

router = APIRouter(prefix="/soccer/{league}", tags=["soccer"])

LEAGUES = {slug: {"slug": slug, "competition": lg.competition, "name": lg.name, "short": lg.short, "country": lg.country,
                  "kind": lg.kind, "calendar_year": lg.calendar_year, "target": lg.target} for slug, lg in _LEAGUES.items()}
ATTRIBUTION = ("Soccer data from ESPN, openfootball (public domain) and, where configured, "
               "Football data provided by the Football-Data.org API. Not affiliated with any league or club.")
GAME_SELECT = """
SELECT g.game_id, g.competition, g.season, g.kickoff_utc, g.game_date, g.status, g.home_team_id, g.away_team_id,
       g.home_score, g.away_score, g.home_ht_score, g.away_ht_score, g.result, g.venue, g.attendance, g.of_verified,
       g.fd_match_id, g.stage, g.leg, g.neutral, g.decided, g.home_score_90, g.away_score_90, g.home_shootout,
       g.away_shootout, g.note
FROM soccer_games g
"""


def lg_kind(comp: str) -> str:
    lg = BY_COMPETITION.get(comp)
    return lg.kind if lg else "league"


def _league(league: str) -> dict:
    if league not in LEAGUES:
        raise HTTPException(404, f"Unknown league {league!r}")
    return LEAGUES[league]


def _meta(**kw) -> dict:
    m = meta(**kw)
    m["attribution"] = ATTRIBUTION
    return m


def season_label(s: int, comp: str = "eng.1") -> str:
    lg = BY_COMPETITION.get(comp)
    return lg.season_label(s) if lg else f"{s}-{str(s + 1)[2:]}"


STAGE_LABELS = {"league-phase": "League phase", "group-stage": "Group stage", "knockout-round-playoffs": "Knockout playoffs",
                "round-of-32": "Round of 32", "round-of-16": "Round of 16", "quarterfinals": "Quarter-finals",
                "semifinals": "Semi-finals", "final": "Final", "first-stage": "First stage", "second-stage": "Second stage",
                "third-stage": "Third stage", "final-stage": "Finals", "playoffs": "Playoffs", "playoff-round": "Playoff round"}


def stage_label(stage: str | None, comp: str) -> str | None:
    """Readable stage for cups and split-season leagues; None for ordinary league seasons."""
    lg = BY_COMPETITION.get(comp)
    if not stage or not lg or (lg.kind == "league" and not lg.calendar_year) or stage[:1].isdigit():
        return None
    return STAGE_LABELS.get(stage, stage.replace("-", " ").capitalize())


def is_knockout(stage: str | None, comp: str) -> bool:
    lg = BY_COMPETITION.get(comp)
    if lg and lg.kind == "cup":
        return not (stage and ("group" in stage or "league-phase" in stage))
    return bool(stage) and ("final" in stage or "playoff" in stage)


def teams_by_id(conn: Connection) -> dict[int, dict]:
    return {r["team_id"]: r for r in q(conn, "SELECT team_id, name, short_name, abbreviation, color, alt_color FROM soccer_teams")}


def current_season(conn: Connection, comp: str) -> int:
    row = q1(conn, """SELECT season FROM soccer_games WHERE competition = :c AND game_date <= :d
                      ORDER BY game_date DESC LIMIT 1""", c=comp, d=today_et())
    if row:
        return row["season"]
    s = q1(conn, "SELECT max(season) s FROM soccer_games WHERE competition = :c", c=comp)["s"]
    if s is None:   # nothing stored yet (a newly added competition before its backfill)
        lg = BY_COMPETITION.get(comp)
        s = lg.current_season(today_et()) if lg else today_et().year
    return s


def records_before(conn: Connection, games: list[dict]) -> dict[tuple[int, int], str]:
    """Each team's W-D-L in the league season entering each listed match."""
    if not games:
        return {}
    pairs = [(g["game_id"], g["home_team_id"]) for g in games] + [(g["game_id"], g["away_team_id"]) for g in games]
    rows = q(conn, """
        WITH t AS (SELECT x.game_id, x.team_id, g.season, g.competition, g.kickoff_utc
                   FROM unnest(CAST(:gids AS bigint[]), CAST(:tids AS int[])) AS x(game_id, team_id)
                   JOIN soccer_games g ON g.game_id = x.game_id)
        SELECT t.game_id, t.team_id,
               count(*) FILTER (WHERE (g2.home_team_id = t.team_id AND g2.result = 'H') OR (g2.away_team_id = t.team_id AND g2.result = 'A')) w,
               count(*) FILTER (WHERE g2.result = 'D') d,
               count(*) FILTER (WHERE (g2.home_team_id = t.team_id AND g2.result = 'A') OR (g2.away_team_id = t.team_id AND g2.result = 'H')) l
        FROM t LEFT JOIN soccer_games g2 ON g2.competition = t.competition AND g2.season = t.season AND g2.status = 'final'
             AND g2.kickoff_utc < t.kickoff_utc AND (g2.home_team_id = t.team_id OR g2.away_team_id = t.team_id)
        GROUP BY 1, 2""", gids=[p[0] for p in pairs], tids=[p[1] for p in pairs])
    return {(r["game_id"], r["team_id"]): f"{r['w']}-{r['d']}-{r['l']}" for r in rows}


def headlines(conn: Connection, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    rows = q(conn, """
        WITH latest AS (SELECT DISTINCT ON (game_id) game_id, kind, revision FROM soccer_predictions
                        WHERE game_id = ANY(:ids) AND market = 'match_result'
                        ORDER BY game_id, (kind = 'live') DESC, revision DESC)
        SELECT p.game_id, p.kind, p.revision, p.market, p.side, p.probability, p.projection, p.published_at
        FROM soccer_predictions p JOIN latest l USING (game_id, kind, revision)
        WHERE p.market IN ('match_result', 'team_goals')""", ids=ids)
    out: dict[int, dict] = {}
    for r in rows:
        h = out.setdefault(r["game_id"], {"kind": r["kind"], "revision": r["revision"], "published_at": r["published_at"]})
        if r["market"] == "match_result":
            h[f"p_{r['side']}"] = r["probability"]
        else:
            h[f"xg_{r['side']}"] = r["projection"]
    for h in out.values():
        trio = {"home": h.get("p_home", 0), "draw": h.get("p_draw", 0), "away": h.get("p_away", 0)}
        h["pick"] = max(trio, key=trio.get)
        h["pick_probability"] = trio[h["pick"]]
    return out


def summaries(conn: Connection, rows: list[dict]) -> list[dict]:
    teams, heads, recs = teams_by_id(conn), headlines(conn, [r["game_id"] for r in rows]), records_before(conn, rows)
    out = []
    for r in rows:
        def side(tid, score):
            return {**teams[tid], "score": score, "record": recs.get((r["game_id"], tid))}
        out.append({"game_id": r["game_id"], "league": BY_COMPETITION[r["competition"]].slug, "season": r["season"],
                    "season_label": season_label(r["season"], r["competition"]),
                    "stage": r.get("stage"), "stage_label": stage_label(r.get("stage"), r["competition"]), "leg": r.get("leg"),
                    "neutral": bool(r.get("neutral")), "note": r.get("note"), "decided": r.get("decided"),
                    "score_90": ({"home": r["home_score_90"], "away": r["away_score_90"]}
                                 if r.get("decided") and r.get("home_score_90") is not None else None),
                    "shootout": ({"home": r["home_shootout"], "away": r["away_shootout"]}
                                 if r.get("decided") == "pen" else None),
                    "tipoff_utc": r["kickoff_utc"], "game_date": r["game_date"], "status": r["status"],
                    "home": side(r["home_team_id"], r["home_score"]), "away": side(r["away_team_id"], r["away_score"]),
                    "result": r["result"], "prediction": heads.get(r["game_id"])})
    return out


@router.get("/games")
def list_games(league: str, date_: date | None = Query(None, alias="date"), date_from: date | None = None,
               date_to: date | None = None, status: list[str] | None = Query(None), team: int | None = None,
               season: int | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
               conn: Connection = Depends(get_conn)):
    comp = _league(league)["competition"]
    where, params = ["g.competition = :c"], {"c": comp}
    if date_ or not (date_from or date_to or season or team):
        where.append("g.game_date = :d"); params["d"] = date_ or today_et()
    if date_from:
        where.append("g.game_date >= :df"); params["df"] = date_from
    if date_to:
        where.append("g.game_date <= :dt"); params["dt"] = date_to
    if season is not None:
        where.append("g.season = :s"); params["s"] = season
    if team is not None:
        where.append("(g.home_team_id = :t OR g.away_team_id = :t)"); params["t"] = team
    if status:
        where.append("g.status = ANY(:st)"); params["st"] = list(status)
    w = " AND ".join(where)
    total = q1(conn, f"SELECT count(*) n FROM soccer_games g WHERE {w}", **params)["n"]
    rows = q(conn, f"{GAME_SELECT} WHERE {w} ORDER BY g.kickoff_utc, g.game_id LIMIT :lim OFFSET :off", **params,
             lim=limit, off=offset)
    return {"data": summaries(conn, rows), "meta": _meta(total=total, limit=limit, offset=offset)}


@router.get("/games/dates")
def game_dates(league: str, season: int | None = None, conn: Connection = Depends(get_conn)):
    comp = _league(league)["competition"]
    s = season or current_season(conn, comp)
    rows = q(conn, """SELECT game_date AS date, count(*) games, count(*) FILTER (WHERE status = 'final') final,
                             count(*) FILTER (WHERE status = 'scheduled') scheduled
                      FROM soccer_games WHERE competition = :c AND season = :s GROUP BY 1 ORDER BY 1""", c=comp, s=s)
    return {"data": rows, "meta": _meta(extra={"season": s, "season_label": season_label(s, comp), "today": today_et()})}


def form(conn: Connection, team_id: int, comp: str, before, n: int = 5) -> list[dict]:
    rows = q(conn, """
        SELECT g.game_id, g.game_date, (g.home_team_id = :t) home, g.result, g.home_score, g.away_score, o.abbreviation opponent
        FROM soccer_games g JOIN soccer_teams o ON o.team_id = CASE WHEN g.home_team_id = :t THEN g.away_team_id ELSE g.home_team_id END
        WHERE g.competition = :c AND g.status = 'final' AND g.kickoff_utc < :b AND (g.home_team_id = :t OR g.away_team_id = :t)
        ORDER BY g.kickoff_utc DESC LIMIT :n""", t=team_id, c=comp, b=before, n=n)
    out = []
    for r in rows:
        gf, ga = (r["home_score"], r["away_score"]) if r["home"] else (r["away_score"], r["home_score"])
        out.append({"game_id": r["game_id"], "date": r["game_date"], "opponent": r["opponent"], "home": r["home"],
                    "result": "W" if gf > ga else "L" if gf < ga else "D", "goals_for": gf, "goals_against": ga})
    return out


@router.get("/games/{game_id}")
def get_game(league: str, game_id: int, conn: Connection = Depends(get_conn)):
    comp = _league(league)["competition"]
    g = q1(conn, f"{GAME_SELECT} WHERE g.game_id = :g AND g.competition = :c", g=game_id, c=comp)
    if not g:
        raise HTTPException(404, f"Match {game_id} not found")
    s = summaries(conn, [g])[0]
    stats = q(conn, """SELECT team_id, is_home, goals, shots, shots_on_target, possession, corners, fouls, yellow_cards,
                              red_cards, offsides, saves, passes, pass_pct FROM soccer_team_game_stats WHERE game_id = :g""", g=game_id)
    h2h = q(conn, """SELECT game_id, game_date AS date, season, home_team_id, away_team_id, home_score, away_score, result
                     FROM soccer_games WHERE status = 'final' AND kickoff_utc < :k AND
                       ((home_team_id = :a AND away_team_id = :b) OR (home_team_id = :b AND away_team_id = :a))
                     ORDER BY kickoff_utc DESC LIMIT 6""", k=g["kickoff_utc"], a=g["home_team_id"], b=g["away_team_id"])
    return {"data": {**s, "venue": g["venue"], "attendance": g["attendance"],
                     "half_time": ({"home": g["home_ht_score"], "away": g["away_ht_score"]}
                                   if g["home_ht_score"] is not None else None),
                     "verified_by_openfootball": g["of_verified"], "team_stats": stats,
                     "context": {"home_form": form(conn, g["home_team_id"], comp, g["kickoff_utc"]),
                                 "away_form": form(conn, g["away_team_id"], comp, g["kickoff_utc"]), "head_to_head": h2h}},
            "meta": _meta()}


def freshness(conn: Connection, comp: str) -> dict:
    # Runs from before LaLiga was added carry no competition; they were all Premier League.
    ing = q1(conn, """SELECT finished_at FROM ingestion_runs WHERE status = 'succeeded'
                      AND job IN ('soccer_espn_sync', 'soccer_espn_ingest', 'soccer_football_data_sync')
                      AND coalesce(params->>'competition', 'eng.1') = :c
                      ORDER BY finished_at DESC LIMIT 1""", c=comp)
    pred = q1(conn, """SELECT finished_at FROM prediction_runs WHERE status = 'succeeded' AND kind = 'live'
                       AND stats->>'league' = 'soccer' AND coalesce(stats->>'competition', 'eng.1') = :c
                       ORDER BY finished_at DESC LIMIT 1""", c=comp)
    return {"last_data_sync_utc": ing and ing["finished_at"], "last_live_forecast_run_utc": pred and pred["finished_at"]}


def _outcome(r: dict) -> dict | None:
    return {"state": r["graded_state"], "result": r["outcome"], "observed": r["observed_value"]} if r["graded_state"] else None


@router.get("/games/{game_id}/predictions")
def game_predictions(league: str, game_id: int, kind: str | None = None, conn: Connection = Depends(get_conn)):
    comp = _league(league)["competition"]
    g = q1(conn, "SELECT * FROM soccer_games WHERE game_id = :g AND competition = :c", g=game_id, c=comp)
    if not g:
        raise HTTPException(404, f"Match {game_id} not found")
    fresh = freshness(conn, comp)
    rev = q1(conn, """SELECT kind, revision, max(published_at) published_at, max(data_cutoff_utc) data_cutoff_utc
                      FROM soccer_predictions WHERE game_id = :g""" + (" AND kind = :k" if kind else "") + """
                      GROUP BY kind, revision ORDER BY (kind = 'live') DESC, revision DESC LIMIT 1""", g=game_id, k=kind)
    m_ = {"disclaimer": DISCLAIMER, "extra": {"freshness": fresh}}
    if not rev:
        return {"data": {"game_id": game_id, "forecast_status": "pending" if g["status"] == "scheduled" else "unavailable",
                         "markets": None}, "meta": _meta(**m_)}
    status = "backtest" if rev["kind"] == "backtest" else (
        "stale" if g["status"] == "scheduled" and fresh["last_data_sync_utc"] and rev["data_cutoff_utc"] < fresh["last_data_sync_utc"]
        else "current")
    rows = q(conn, """
        SELECT p.market, p.side, p.subject_id, p.threshold_kind, p.threshold, p.probability, p.projection, p.label, p.details,
               mo.version model_version, o.graded_state, o.outcome, o.observed_value
        FROM soccer_predictions p JOIN models mo ON mo.id = p.model_id
        LEFT JOIN soccer_prediction_outcomes o ON o.prediction_id = p.id
        WHERE p.game_id = :g AND p.kind = :k AND p.revision = :r ORDER BY p.market, p.threshold NULLS FIRST""",
             g=game_id, k=rev["kind"], r=rev["revision"])
    mk: dict = {"result": {}, "goals": {}, "totals": {"lines": []}, "btts": None}
    version = None
    for r in rows:
        if r["market"] == "match_result":
            mk["result"][r["side"]] = {"probability": r["probability"], "outcome": _outcome(r)}
            if r["side"] == "home" and r["details"]:
                d = r["details"]
                version = d.get("model_version")
                mk["factors"], mk["top_scores"] = d.get("factors"), d.get("top_scores")
                mk["distribution"] = d.get("distribution")
        elif r["market"] == "team_goals":
            mk["goals"][r["side"]] = {"expected": round(r["projection"], 2), "outcome": _outcome(r)}
        elif r["market"] == "total_goals":
            if r["threshold_kind"] is None:
                mk["totals"].update(expected=round(r["projection"], 2), outcome=_outcome(r),
                                    probabilities_available=bool((r["details"] or {}).get("lines_supported")))
            else:
                mk["totals"]["lines"].append({"line": r["threshold"], "label": r["label"], "probability": r["probability"],
                                              "outcome": _outcome(r)})
        elif r["market"] == "btts":
            mk["btts"] = {"probability": r["probability"], "outcome": _outcome(r)}
    return {"data": {"game_id": game_id, "game_status": g["status"], "forecast_status": status, "kind": rev["kind"],
                     "revision": rev["revision"], "published_at": rev["published_at"], "data_cutoff_utc": rev["data_cutoff_utc"],
                     "model_version": version, "markets": mk,
                     "notes": {"kind": "Backtest forecasts were regenerated after the fact using only data available before "
                                       "each match day; they were not published live." if rev["kind"] == "backtest" else None,
                               "result": "Result after 90 minutes plus stoppage time. Extra time and penalty shootouts "
                                         "don't count, so a knockout tie can be forecast as a draw." if lg_kind(comp) == "cup"
                                         else "Result after 90 minutes plus stoppage time (league matches have no extra time).",
                               "model": "Expected goals come from recent scoring, shots on target and shots for and against, "
                                        "season record, Elo rating, home advantage, rest and promotion status. A Dixon-Coles "
                                        "adjustment corrects the frequency of low scores such as 0-0 and 1-1."}},
            "meta": _meta(**m_)}


def _table(conn: Connection, comp: str, season: int) -> list[dict]:
    rows = q(conn, """
        WITH r AS (
          SELECT home_team_id team_id, home_score gf, away_score ga, result = 'H' w, result = 'D' d, result = 'A' l, kickoff_utc
          FROM soccer_games WHERE competition = :c AND season = :s AND status = 'final'
          UNION ALL
          SELECT away_team_id, away_score, home_score, result = 'A', result = 'D', result = 'H', kickoff_utc
          FROM soccer_games WHERE competition = :c AND season = :s AND status = 'final'),
        teams AS (SELECT DISTINCT unnest(ARRAY[home_team_id, away_team_id]) team_id FROM soccer_games WHERE competition = :c AND season = :s)
        SELECT t.team_id, x.name, x.short_name, x.abbreviation, x.color, x.alt_color,
               count(r.team_id) played, count(*) FILTER (WHERE r.w) won, count(*) FILTER (WHERE r.d) drawn,
               count(*) FILTER (WHERE r.l) lost, coalesce(sum(r.gf), 0) gf, coalesce(sum(r.ga), 0) ga,
               (array_agg(CASE WHEN r.w THEN 'W' WHEN r.d THEN 'D' WHEN r.l THEN 'L' END ORDER BY r.kickoff_utc DESC)
                 FILTER (WHERE r.team_id IS NOT NULL))[1:5] form
        FROM teams t JOIN soccer_teams x ON x.team_id = t.team_id LEFT JOIN r ON r.team_id = t.team_id
        GROUP BY t.team_id, x.name, x.short_name, x.abbreviation, x.color, x.alt_color""", c=comp, s=season)
    for r in rows:
        r["gd"], r["points"] = r["gf"] - r["ga"], 3 * r["won"] + r["drawn"]
        r["form"] = r["form"] or []
    rows.sort(key=lambda r: (-r["points"], -r["gd"], -r["gf"], r["name"]))
    for i, r in enumerate(rows, 1):
        r["position"] = i
    return rows


@router.get("/table")
def table(league: str, season: int | None = None, conn: Connection = Depends(get_conn)):
    """League table (3 points for a win, 1 for a draw; ties broken by goal difference, then goals scored)."""
    comp = _league(league)["competition"]
    s = season or current_season(conn, comp)
    return {"data": _table(conn, comp, s), "meta": _meta(extra={"season": s, "season_label": season_label(s, comp)})}


def _rows_from_games(games: list[dict], teams: dict[int, dict], members: set[int] | None = None) -> list[dict]:
    """A table (3 points a win) from a list of matches; ``members`` adds teams that haven't played yet."""
    acc: dict[int, dict] = {}

    def row(tid):
        return acc.setdefault(tid, {**teams[tid], "played": 0, "won": 0, "drawn": 0, "lost": 0, "gf": 0, "ga": 0, "form": []})
    for tid in members or ():
        if tid in teams:
            row(tid)
    for g in games:
        if g["status"] != "final" or g["home_score"] is None or not g["result"]:
            continue
        for tid, gf, ga, res in ((g["home_team_id"], g["home_score"], g["away_score"], g["result"]),
                                 (g["away_team_id"], g["away_score"], g["home_score"], {"H": "A", "A": "H", "D": "D"}[g["result"]])):
            r = row(tid)
            r["played"] += 1
            r["gf"] += gf
            r["ga"] += ga
            k = {"H": "won", "D": "drawn", "A": "lost"}[res]
            r[k] += 1
            r["form"].insert(0, {"won": "W", "drawn": "D", "lost": "L"}[k])
        for tid in (g["home_team_id"], g["away_team_id"]):
            row(tid)
    out = list(acc.values())
    for r in out:
        r["gd"], r["points"], r["form"] = r["gf"] - r["ga"], 3 * r["won"] + r["drawn"], r["form"][:5]
    out.sort(key=lambda r: (-r["points"], -r["gd"], -r["gf"], r["name"]))
    for i, r in enumerate(out, 1):
        r["position"] = i
    return out


def _tie_winner(legs: list[dict], a: int, b: int, agg: dict[int, int], teams: dict[int, dict]) -> int | None:
    last = legs[-1]
    if any(g["status"] != "final" for g in legs):
        return None
    if last["decided"] == "pen" and last["home_shootout"] is not None:
        return last["home_team_id"] if last["home_shootout"] > last["away_shootout"] else last["away_team_id"]
    if agg[a] != agg[b]:
        return a if agg[a] > agg[b] else b
    note = (last["note"] or "").lower()   # level on aggregate: away goals (before 2021) etc. -- trust ESPN's note
    for tid in (a, b):
        names = {teams[tid]["name"].lower(), (teams[tid]["short_name"] or "").lower()} - {""}
        if any(f"{n} advance" in note or f"{n} win" in note for n in names):
            return tid
    return None


# Ecuador LigaPro final stage: what each group of the split season plays for.
GROUP_LABELS = {"Group 1": "Title hexagonal", "Group 2": "Sudamericana quadrangular", "Group 3": "Relegation hexagonal"}


def _league_table(games: list[dict], teams: dict[int, dict], groups: dict[int, str]) -> dict:
    """One current table for a league season. Every match counts once; stage labels don't split it."""
    final_stage = [g for g in games if g["stage"] == "final-stage"]
    if not (final_stage and groups):
        return {"name": "Table", "stage": None, "stage_label": "Table", "rows": _rows_from_games(games, teams)}
    # Split season (Ecuador): clubs carry their first-stage points into their final-stage group; positions are
    # within each group, which the web app shows as one table with a divider per group.
    rows = []
    for gname in sorted(set(groups.values())):
        members = {t for t, gn in groups.items() if gn == gname}
        played = [g for g in games if g["home_team_id"] in members and g["away_team_id"] in members or
                  (g["stage"] != "final-stage" and (g["home_team_id"] in members or g["away_team_id"] in members))]
        part = [r for r in _rows_from_games(played, teams, members) if r["team_id"] in members]
        part.sort(key=lambda r: (-r["points"], -r["gd"], -r["gf"], r["name"]))
        for i, r in enumerate(part, 1):
            rows.append({**r, "position": i, "group": GROUP_LABELS.get(gname, gname)})
    return {"name": "Table", "stage": "final-stage", "stage_label": "Final stage · first-stage points carried over", "rows": rows}


def standings_data(conn: Connection, comp: str, season: int) -> dict:
    """Tables (one per group / league phase / stage, plus an overall table for split-season leagues) and knockout
    ties (legs, aggregate, who went through) for one season."""
    teams = teams_by_id(conn)
    games = q(conn, f"{GAME_SELECT} WHERE g.competition = :c AND g.season = :s ORDER BY g.kickoff_utc, g.game_id", c=comp, s=season)
    groups = {r["team_id"]: r["group_name"] for r in q(conn, """SELECT team_id, group_name FROM soccer_groups
                                                                WHERE competition = :c AND season = :s""", c=comp, s=season)}
    if lg_kind(comp) == "league":
        # Domestic leagues: exactly one current table. (Ecuador's split season: one table of the final stage's
        # groups, first-stage points carried over, once the final stage has started.)
        return {"tables": [_league_table(games, teams, groups)], "knockout": []}
    table_games = [g for g in games if not is_knockout(g["stage"], comp)]
    ko_games = [g for g in games if is_knockout(g["stage"], comp)]
    stages = list(dict.fromkeys(g["stage"] for g in table_games))
    tables = []
    for st in stages:
        sg = [g for g in table_games if g["stage"] == st]
        same = [g for g in sg if groups.get(g["home_team_id"]) and groups.get(g["home_team_id"]) == groups.get(g["away_team_id"])]
        names = sorted({groups[g["home_team_id"]] for g in same})
        label = stage_label(st, comp) or "Table"
        if len(names) > 1 and len(same) >= 0.8 * len(sg):
            for n in names:
                members = {t for t, gn in groups.items() if gn == n}
                tables.append({"name": n, "stage": st, "stage_label": label,
                               "rows": _rows_from_games([g for g in same if groups[g["home_team_id"]] == n], teams, members)})
        else:
            tables.append({"name": names[0] if len(names) == 1 and names[0] != "Table" else label, "stage": st,
                           "stage_label": label, "rows": _rows_from_games(sg, teams)})
    knockout = []
    for st in dict.fromkeys(g["stage"] for g in ko_games):
        ties: dict[frozenset, list[dict]] = {}
        for g in (g for g in ko_games if g["stage"] == st):
            ties.setdefault(frozenset((g["home_team_id"], g["away_team_id"])), []).append(g)
        out = []
        for legs in ties.values():
            a, b = legs[0]["home_team_id"], legs[0]["away_team_id"]
            agg = {a: 0, b: 0}
            for g in legs:
                if g["status"] == "final" and g["home_score"] is not None:
                    agg[g["home_team_id"]] += g["home_score"]
                    agg[g["away_team_id"]] += g["away_score"]
            out.append({"team_a": teams[a], "team_b": teams[b], "aggregate": {"a": agg[a], "b": agg[b]},
                        "winner_id": _tie_winner(legs, a, b, agg, teams), "note": legs[-1]["note"],
                        "complete": all(g["status"] == "final" for g in legs),
                        "legs": [{"game_id": g["game_id"], "date": g["game_date"], "leg": g["leg"], "status": g["status"],
                                  "home_team_id": g["home_team_id"], "away_team_id": g["away_team_id"],
                                  "home_score": g["home_score"], "away_score": g["away_score"], "decided": g["decided"],
                                  "shootout": ({"home": g["home_shootout"], "away": g["away_shootout"]}
                                               if g["decided"] == "pen" else None)} for g in legs]})
        knockout.append({"stage": st, "label": stage_label(st, comp) or st, "ties": out})
    return {"tables": tables, "knockout": knockout}


@router.get("/standings")
def standings(league: str, season: int | None = None, conn: Connection = Depends(get_conn)):
    """Group / league-phase / stage tables and knockout ties. For a plain league: one table (same as /table)."""
    comp = _league(league)["competition"]
    s = season or current_season(conn, comp)
    seasons = [r["season"] for r in q(conn, "SELECT DISTINCT season FROM soccer_games WHERE competition = :c ORDER BY 1 DESC", c=comp)]
    return {"data": standings_data(conn, comp, s),
            "meta": _meta(extra={"season": s, "season_label": season_label(s, comp),
                                 "seasons": [{"season": x, "label": season_label(x, comp)} for x in seasons]})}


@router.get("/teams")
def teams(league: str, season: int | None = None, conn: Connection = Depends(get_conn)):
    comp = _league(league)["competition"]
    s = season or current_season(conn, comp)
    return {"data": sorted(_table(conn, comp, s), key=lambda r: r["name"]), "meta": _meta(extra={"season": s})}


@router.get("/teams/{team_id}")
def team_detail(league: str, team_id: int, season: int | None = None, conn: Connection = Depends(get_conn)):
    comp = _league(league)["competition"]
    t = q1(conn, "SELECT team_id, name, short_name, abbreviation, color, alt_color FROM soccer_teams WHERE team_id = :t", t=team_id)
    if not t:
        raise HTTPException(404, f"Unknown team {team_id}")
    s = season or current_season(conn, comp)
    # A cup's whole-season aggregate isn't a real table, so cups report no position.
    standing = None if lg_kind(comp) == "cup" else next((r for r in _table(conn, comp, s) if r["team_id"] == team_id), None)
    games = q(conn, f"{GAME_SELECT} WHERE g.competition = :c AND g.season = :s AND (g.home_team_id = :t OR g.away_team_id = :t) "
                    "ORDER BY g.kickoff_utc", c=comp, s=s, t=team_id)
    avg = q1(conn, """SELECT count(*) n, round(avg(st.shots)::numeric, 1) shots, round(avg(st.shots_on_target)::numeric, 1) shots_on_target,
                             round(avg(st.possession)::numeric, 1) possession,
                             round(avg(o.shots)::numeric, 1) shots_against, round(avg(o.shots_on_target)::numeric, 1) shots_on_target_against
                      FROM soccer_team_game_stats st JOIN soccer_games g USING (game_id)
                      JOIN soccer_team_game_stats o ON o.game_id = st.game_id AND o.team_id <> st.team_id
                      WHERE st.team_id = :t AND g.competition = :c AND g.season = :s""", t=team_id, c=comp, s=s)
    return {"data": {"team": t, "season": s, "season_label": season_label(s, comp), "standing": standing,
                     "averages": avg, "games": summaries(conn, games)}, "meta": _meta()}


@router.get("/search")
def search(league: str, q_: str = Query(..., alias="q", min_length=2), limit: int = Query(8, ge=1, le=25),
           conn: Connection = Depends(get_conn)):
    comp = _league(league)["competition"]
    rows = q(conn, """SELECT 'team' AS type, t.team_id AS id, t.name, t.abbreviation AS subtitle FROM soccer_teams t
                      WHERE (lower(t.name) LIKE :l OR lower(coalesce(t.short_name, '')) LIKE :l OR lower(t.abbreviation) = lower(:q))
                        AND EXISTS (SELECT 1 FROM soccer_games g WHERE g.competition = :c
                                    AND (g.home_team_id = t.team_id OR g.away_team_id = t.team_id)
                                    AND g.season >= (SELECT max(season) - 2 FROM soccer_games WHERE competition = :c))
                      ORDER BY t.name LIMIT :n""", l=f"%{q_.lower()}%", q=q_, c=comp, n=limit)
    return {"data": rows, "meta": _meta()}


@router.get("/models/performance")
@cached(ttl=300)
def performance(league: str, kind: str | None = None, season: int | None = None, conn: Connection = Depends(get_conn)):
    """Three-way accuracy (pick = most likely of home / draw / away), ranked probability score, log loss, draw
    calibration, totals / both-teams-to-score metrics, and the held-out validation of the active model."""
    lg = _league(league)
    comp = lg["competition"]
    where, params = ["g.competition = :c"], {"c": comp}
    if kind:
        where.append("p.kind = :k"); params["k"] = kind
    if season:
        where.append("g.season = :s"); params["s"] = season
    w = " AND ".join(where)
    latest = """JOIN (SELECT game_id, kind, max(revision) r FROM soccer_predictions GROUP BY 1, 2) lr
                ON lr.game_id = p.game_id AND lr.kind = p.kind AND lr.r = p.revision"""
    base = f"FROM soccer_predictions p {latest} JOIN soccer_games g ON g.game_id = p.game_id " \
           f"JOIN soccer_prediction_outcomes o ON o.prediction_id = p.id WHERE {w} AND o.graded_state = 'final'"
    trio = q1(conn, f"""
        WITH x AS (SELECT p.game_id, p.kind,
                          max(p.probability) FILTER (WHERE p.side = 'home') ph, max(p.probability) FILTER (WHERE p.side = 'draw') pd,
                          max(p.probability) FILTER (WHERE p.side = 'away') pa, max(g.result) res
                   {base} AND p.market = 'match_result' GROUP BY 1, 2)
        SELECT count(*) n,
               avg(CASE WHEN (CASE WHEN ph >= pd AND ph >= pa THEN 'H' WHEN pa >= pd THEN 'A' ELSE 'D' END) = res THEN 1.0 ELSE 0 END) accuracy,
               avg((power((ph) - (res = 'H')::int, 2) + power((ph + pd) - (res IN ('H','D'))::int, 2)) / 2) rps,
               avg(-ln(greatest(CASE res WHEN 'H' THEN ph WHEN 'D' THEN pd ELSE pa END, 1e-9))) log_loss,
               avg(pd) mean_draw, avg((res = 'D')::int) draw_rate, avg((res = 'H')::int) home_rate,
               avg(CASE WHEN res = 'H' THEN 1.0 ELSE 0 END) home_win_rate
        FROM x""", **params)
    calib = q(conn, f"""SELECT p.market, p.side, width_bucket(p.probability, 0, 1, 10) bucket, count(*) n,
                               avg(p.probability) mean_predicted, avg((o.outcome = 'hit')::int) observed_rate
                        {base} AND p.probability IS NOT NULL GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""", **params)
    lines = q(conn, f"""SELECT p.market, p.threshold, count(*) n, avg(p.probability) mean_predicted,
                               avg((o.outcome = 'hit')::int) hit_rate, avg(o.brier) brier
                        {base} AND p.market IN ('total_goals', 'btts') AND p.probability IS NOT NULL
                        GROUP BY 1, 2 ORDER BY 1, 2""", **params)
    mae = q(conn, f"""SELECT p.market, count(*) n, avg(o.abs_error) mae {base} AND o.abs_error IS NOT NULL GROUP BY 1""", **params)
    model = q1(conn, """SELECT id, version, validation_start, validation_end, metrics, trained_at FROM models
                        WHERE is_active AND target = :t""", t=lg["target"])
    cal: dict = {}
    for r in calib:
        cal.setdefault(f"{r['market']}:{r['side'] or ''}", []).append(
            {"bucket": r["bucket"], "n": r["n"], "mean_predicted": float(r["mean_predicted"]), "observed_rate": float(r["observed_rate"])})
    f = lambda v, n=4: None if v is None else round(float(v), n)  # noqa: E731
    return {"data": {"match_result": {k: (f(v) if k != "n" else v) for k, v in (trio or {}).items()} if trio and trio["n"] else None,
                     "calibration": cal,
                     "lines": [{**r, "mean_predicted": f(r["mean_predicted"]), "hit_rate": f(r["hit_rate"]), "brier": f(r["brier"])} for r in lines],
                     "mae": {r["market"]: {"n": r["n"], "value": f(r["mae"], 3)} for r in mae},
                     "active_model": model},
            "meta": _meta(disclaimer=DISCLAIMER)}


@router.get("/status")
def status(league: str, conn: Connection = Depends(get_conn)):
    comp = _league(league)["competition"]
    checks = q1(conn, """SELECT count(*) FILTER (WHERE of_verified) verified, count(*) FILTER (WHERE of_verified = false) disagree,
                                count(*) FILTER (WHERE fd_match_id IS NOT NULL) fd_linked
                         FROM soccer_games WHERE competition = :c""", c=comp)
    return {"data": {"freshness": freshness(conn, comp), "season": current_season(conn, comp), "source_checks": checks},
            "meta": _meta()}

"""Teams: records, recent form, splits, head-to-head, standings (FR-04.3, FR-05.1, FR-07.3)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection

from polypicker.api.deps import current_season, get_conn, q
from polypicker.api.queries import COMPETITIVE_SQL, GAME_SELECT, RECORD_TYPES_SQL, game_summaries, meta, resolve_team
from polypicker.api.routers.games import head_to_head, home_away_record, team_form
from polypicker.domain.games import season_label

router = APIRouter(tags=["teams"])


def _standings(conn: Connection, season: int) -> list[dict]:
    return q(conn, f"""
        SELECT t.team_id, t.abbreviation, t.full_name, t.conference, t.division,
               count(s.game_id) FILTER (WHERE s.win) AS wins, count(s.game_id) FILTER (WHERE NOT s.win) AS losses,
               round(avg(s.points)::numeric, 1) AS points_per_game,
               round(avg(s.points - s.plus_minus)::numeric, 1) AS opp_points_per_game,
               round(avg(s.plus_minus)::numeric, 1) AS avg_margin
        FROM teams t
        LEFT JOIN team_game_stats s ON s.team_id = t.team_id AND s.game_id IN (
            SELECT game_id FROM games WHERE season = :s AND status = 'final' AND season_type IN {RECORD_TYPES_SQL})
        GROUP BY t.team_id ORDER BY t.conference,
                 count(s.game_id) FILTER (WHERE s.win)::float / greatest(count(s.game_id), 1) DESC""", s=season)


@router.get("/teams")
def list_teams(season: int | None = None, conn: Connection = Depends(get_conn)):
    s = season or current_season(conn)
    return {"data": _standings(conn, s), "meta": meta(extra={"season": s, "season_label": season_label(s)})}


@router.get("/standings")
def standings(season: int | None = None, conn: Connection = Depends(get_conn)):
    """Regular-season standings by conference (NBA Cup final excluded, as in official standings)."""
    s = season or current_season(conn)
    rows = _standings(conn, s)
    out: dict[str, list] = {}
    for r in rows:
        conf = out.setdefault(r["conference"], [])
        leader = conf[0] if conf else r
        r["games_behind"] = ((leader["wins"] - r["wins"]) + (r["losses"] - leader["losses"])) / 2
        r["win_pct"] = round(r["wins"] / (r["wins"] + r["losses"]), 3) if (r["wins"] + r["losses"]) else None
        conf.append(r)
    return {"data": out, "meta": meta(extra={"season": s, "season_label": season_label(s)})}


def _team(conn: Connection, ident: str) -> dict:
    t = resolve_team(conn, ident)
    if not t:
        raise HTTPException(404, f"Unknown team {ident!r}")
    return {k: t[k] for k in ("team_id", "abbreviation", "city", "name", "full_name", "conference", "division")}


@router.get("/teams/{team}")
def team_detail(team: str, season: int | None = None, conn: Connection = Depends(get_conn)):
    """Record, home/away split, last 5/10, season averages, recent and upcoming games (FR-04.3, FR-05.1)."""
    t = _team(conn, team)
    s = season or current_season(conn)
    now = datetime.now(timezone.utc)
    tid = t["team_id"]
    avgs = q(conn, f"""
        SELECT count(*) games, count(*) FILTER (WHERE s.win) wins, count(*) FILTER (WHERE NOT s.win) losses,
               round(avg(s.points)::numeric,1) pts, round(avg(s.points - s.plus_minus)::numeric,1) opp_pts,
               round(avg(s.ast)::numeric,1) ast, round(avg(s.reb)::numeric,1) reb, round(avg(s.tov)::numeric,1) tov,
               round((sum(s.fgm)::numeric / nullif(sum(s.fga),0)),3) fg_pct,
               round((sum(s.fg3m)::numeric / nullif(sum(s.fg3a),0)),3) fg3_pct,
               round((sum(s.ftm)::numeric / nullif(sum(s.fta),0)),3) ft_pct,
               round(avg(s.fga - s.oreb + s.tov + 0.44 * s.fta)::numeric, 1) possessions
        FROM team_game_stats s JOIN games g USING (game_id)
        WHERE s.team_id = :t AND g.season = :s AND g.status = 'final' AND g.season_type IN {RECORD_TYPES_SQL}""",
              t=tid, s=s)[0]
    if avgs["possessions"]:
        avgs["offensive_rating"] = round(float(avgs["pts"]) / float(avgs["possessions"]) * 100, 1)
        avgs["defensive_rating"] = round(float(avgs["opp_pts"]) / float(avgs["possessions"]) * 100, 1)
    recent = q(conn, f"""{GAME_SELECT} WHERE (g.home_team_id = :t OR g.away_team_id = :t) AND g.season = :s
                         AND g.status = 'final' ORDER BY g.tipoff_utc DESC LIMIT 10""", t=tid, s=s)
    upcoming = q(conn, f"""{GAME_SELECT} WHERE (g.home_team_id = :t OR g.away_team_id = :t)
                           AND g.status = 'scheduled' AND g.tipoff_utc > :now ORDER BY g.tipoff_utc LIMIT 5""",
                 t=tid, now=now)
    return {"data": {
        "team": t, "season": s, "season_label": season_label(s), "season_stats": avgs,
        "home_away_record": home_away_record(conn, tid, s, now),
        "last5": team_form(conn, tid, s, now, 5), "last10": team_form(conn, tid, s, now, 10),
        "recent_games": game_summaries(conn, recent), "upcoming_games": game_summaries(conn, upcoming)},
        "meta": meta()}


@router.get("/teams/{team}/games")
def team_games(team: str, season: int | None = None, limit: int = Query(100, ge=1, le=200),
               offset: int = Query(0, ge=0), conn: Connection = Depends(get_conn)):
    t = _team(conn, team)
    s = season or current_season(conn)
    rows = q(conn, f"""{GAME_SELECT} WHERE (g.home_team_id = :t OR g.away_team_id = :t) AND g.season = :s
                       ORDER BY g.tipoff_utc LIMIT :lim OFFSET :off""", t=t["team_id"], s=s, lim=limit, off=offset)
    return {"data": game_summaries(conn, rows), "meta": meta(limit=limit, offset=offset)}


@router.get("/teams/{team}/head-to-head/{opponent}")
def h2h(team: str, opponent: str, seasons: int = Query(3, ge=1, le=10), conn: Connection = Depends(get_conn)):
    """Results between two teams over recent seasons (FR-05.1, FR-07.3)."""
    a, b = _team(conn, team), _team(conn, opponent)
    games = head_to_head(conn, a["team_id"], b["team_id"], datetime.now(timezone.utc), seasons)
    wins_a = sum(1 for g in games if g["winner_team_id"] == a["team_id"])
    return {"data": {"team": a, "opponent": b, "games": games,
                     "summary": {"games": len(games), f"{a['abbreviation']}_wins": wins_a,
                                 f"{b['abbreviation']}_wins": len(games) - wins_a}},
            "meta": meta()}

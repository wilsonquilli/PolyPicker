"""Schedule, game detail and box scores (FR-01, FR-04, FR-05.1, FR-08.1)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection

from polypicker.api.deps import current_season, get_conn, q, q1, today_et
from polypicker.api.queries import (COMPETITIVE_SQL, GAME_SELECT, RECORD_TYPES_SQL, game_summaries, meta,
                                    resolve_team)
from polypicker.api.schemas import Envelope, GameDetail, GameSummary

router = APIRouter(tags=["games"])

Status = Literal["scheduled", "live", "final", "postponed", "canceled", "unknown"]
SeasonType = Literal["preseason", "regular", "playoffs", "play_in", "cup_final", "all_star"]


@router.get("/games", response_model=Envelope[list[GameSummary]])
def list_games(
    date_: date | None = Query(None, alias="date", description="US-Eastern game date; defaults to today"),
    date_from: date | None = None, date_to: date | None = None,
    team: str | None = Query(None, description="Team id or abbreviation, e.g. BOS"),
    status: list[Status] | None = Query(None), season: int | None = None,
    season_type: list[SeasonType] | None = Query(None),
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
    conn: Connection = Depends(get_conn),
):
    """Browse games by day, date range, team, status or season (FR-01.1-01.4, FR-08.1)."""
    where, params = [], {}
    if date_ or not (date_from or date_to or season or team):
        where.append("g.game_date_et = :d"); params["d"] = date_ or today_et()
    if date_from:
        where.append("g.game_date_et >= :df"); params["df"] = date_from
    if date_to:
        where.append("g.game_date_et <= :dt"); params["dt"] = date_to
    if season is not None:
        where.append("g.season = :s"); params["s"] = season
    if team:
        t = resolve_team(conn, team)
        if not t:
            raise HTTPException(404, f"Unknown team {team!r}")
        where.append("(g.home_team_id = :t OR g.away_team_id = :t)"); params["t"] = t["team_id"]
    if status:
        where.append("g.status = ANY(:st)"); params["st"] = list(status)
    if season_type:
        where.append("g.season_type = ANY(:sty)"); params["sty"] = list(season_type)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    total = q1(conn, f"SELECT count(*) n FROM games g {w}", **params)["n"]
    rows = q(conn, f"{GAME_SELECT} {w} ORDER BY g.tipoff_utc, g.game_id LIMIT :lim OFFSET :off",
             **params, lim=limit, off=offset)
    return {"data": game_summaries(conn, rows), "meta": meta(total=total, limit=limit, offset=offset)}


@router.get("/games/dates")
def game_dates(season: int | None = None, conn: Connection = Depends(get_conn)):
    """Dates that have games, with counts by status -- drives the day picker (FR-01.3)."""
    s = season or current_season(conn)
    rows = q(conn, """
        SELECT game_date_et AS date, count(*) AS games,
               count(*) FILTER (WHERE status = 'final') AS final,
               count(*) FILTER (WHERE status = 'scheduled') AS scheduled
        FROM games WHERE season = :s GROUP BY 1 ORDER BY 1""", s=s)
    return {"data": rows, "meta": meta(extra={"season": s, "today": today_et()})}


def _game_row(conn: Connection, game_id: int) -> dict:
    row = q1(conn, f"{GAME_SELECT} WHERE g.game_id = :g", g=game_id)
    if not row:
        raise HTTPException(404, f"Game {game_id} not found")
    return row


def team_form(conn: Connection, team_id: int, season: int, before, n: int) -> dict:
    rows = q(conn, f"""
        SELECT s.win, s.points, s.plus_minus, g.game_date_et, s.is_home,
               s.points - coalesce(s.plus_minus, 0) AS opp_points, o.abbreviation AS opponent
        FROM team_game_stats s JOIN games g USING (game_id) JOIN teams o ON o.team_id = s.opponent_team_id
        WHERE s.team_id = :t AND g.season = :s AND g.status = 'final' AND g.season_type IN {COMPETITIVE_SQL}
          AND g.tipoff_utc < :before
        ORDER BY g.tipoff_utc DESC LIMIT :n""", t=team_id, s=season, before=before, n=n)
    w = sum(r["win"] for r in rows)
    return {"games": len(rows), "wins": w, "losses": len(rows) - w,
            "avg_points": round(sum(r["points"] for r in rows) / len(rows), 1) if rows else None,
            "avg_margin": round(sum((r["plus_minus"] or 0) for r in rows) / len(rows), 1) if rows else None,
            "results": [{"date": r["game_date_et"], "opponent": r["opponent"], "home": r["is_home"],
                         "result": "W" if r["win"] else "L", "points": r["points"],
                         "opponent_points": r["opp_points"]} for r in rows]}


def home_away_record(conn: Connection, team_id: int, season: int, before) -> dict:
    r = q1(conn, f"""
        SELECT count(*) FILTER (WHERE s.is_home AND s.win) hw, count(*) FILTER (WHERE s.is_home AND NOT s.win) hl,
               count(*) FILTER (WHERE NOT s.is_home AND s.win) aw, count(*) FILTER (WHERE NOT s.is_home AND NOT s.win) al
        FROM team_game_stats s JOIN games g USING (game_id)
        WHERE s.team_id = :t AND g.season = :s AND g.status = 'final' AND g.season_type IN {RECORD_TYPES_SQL}
          AND g.tipoff_utc < :before""", t=team_id, s=season, before=before)
    return {"home": f"{r['hw']}-{r['hl']}", "away": f"{r['aw']}-{r['al']}"}


def head_to_head(conn: Connection, a: int, b: int, before, seasons: int = 3) -> list[dict]:
    return q(conn, f"""
        SELECT g.game_id, g.game_date_et AS date, g.season, g.season_type, g.home_team_id, g.away_team_id,
               g.home_score, g.away_score, g.winner_team_id
        FROM games g
        WHERE g.status = 'final' AND g.season_type IN {COMPETITIVE_SQL} AND g.tipoff_utc < :before
          AND ((g.home_team_id = :a AND g.away_team_id = :b) OR (g.home_team_id = :b AND g.away_team_id = :a))
          AND g.season > (SELECT max(season) FROM games) - :n
        ORDER BY g.tipoff_utc DESC""", a=a, b=b, before=before, n=seasons)


def _rest_days(conn: Connection, team_id: int, tipoff) -> int | None:
    """Calendar days since the team's previous scheduled game (1 = back-to-back)."""
    return q1(conn, """
        SELECT (CAST(:tip AS timestamptz) AT TIME ZONE 'America/New_York')::date - max(game_date_et) AS n
        FROM games WHERE (home_team_id = :t OR away_team_id = :t) AND tipoff_utc < :tip
          AND season_type <> 'preseason' AND status <> 'canceled'""", t=team_id, tip=tipoff)["n"]


@router.get("/games/{game_id}", response_model=Envelope[GameDetail])
def game_detail(game_id: int, conn: Connection = Depends(get_conn)):
    """Teams, status, final score, line score and pregame context (FR-01.5, FR-01.6, FR-05.1)."""
    row = _game_row(conn, game_id)
    summary = game_summaries(conn, [row])[0]
    line = None
    if row["status"] == "final":
        line = q(conn, """SELECT team_id, q1_points, q2_points, q3_points, q4_points, ot_points, points
                          FROM team_game_stats WHERE game_id = :g ORDER BY is_home""", g=game_id) or None
    ctx = {}
    for side, tid, oid in (("home", row["home_team_id"], row["away_team_id"]),
                           ("away", row["away_team_id"], row["home_team_id"])):
        ctx[side] = {"last5": team_form(conn, tid, row["season"], row["tipoff_utc"], 5),
                     "last10": team_form(conn, tid, row["season"], row["tipoff_utc"], 10),
                     "home_away_record": home_away_record(conn, tid, row["season"], row["tipoff_utc"]),
                     "rest_days": _rest_days(conn, tid, row["tipoff_utc"])}
    ctx["head_to_head"] = head_to_head(conn, row["home_team_id"], row["away_team_id"], row["tipoff_utc"])
    detail = {**summary, "arena": {"name": row["arena_name"], "city": row["arena_city"], "state": row["arena_state"]},
              "periods": row["periods"], "attendance": row["attendance"], "line_score": line, "context": ctx}
    return {"data": detail, "meta": meta()}


@router.get("/games/{game_id}/boxscore")
def boxscore(game_id: int, conn: Connection = Depends(get_conn)):
    """Team and player box scores. Non-appearances are listed with their status, not zeros (NFR-08.2)."""
    row = _game_row(conn, game_id)
    if row["status"] != "final":
        return {"data": None, "meta": meta(extra={"reason": f"game status is {row['status']}"})}
    teams = q(conn, """SELECT * FROM team_game_stats WHERE game_id = :g ORDER BY is_home DESC""", g=game_id)
    players = q(conn, """
        SELECT s.player_id, p.full_name, s.team_id, s.participation, s.status_comment, s.starter,
               s.starting_position, s.minutes, s.points, s.assists, s.rebounds, s.oreb, s.dreb, s.fgm, s.fga,
               s.fg3m, s.fg3a, s.ftm, s.fta, s.steals, s.blocks, s.turnovers, s.fouls, s.plus_minus
        FROM player_game_stats s JOIN players p USING (player_id)
        WHERE s.game_id = :g
        ORDER BY s.team_id, (s.participation = 'played') DESC, s.starter DESC NULLS LAST, s.minutes DESC NULLS LAST""",
              g=game_id)
    for t in teams:
        for k in ("source_id", "ingestion_run_id", "updated_at"):
            t.pop(k, None)
    return {"data": {"teams": teams, "players": players,
                     "player_stats_complete": bool(players)},
            "meta": meta(extra={"source_updated_at": teams[0]["source_updated_at"] if teams else None})}

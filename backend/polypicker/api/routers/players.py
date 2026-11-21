"""Players: profile, season averages, game logs, trends (FR-03.5, FR-04.3, FR-05.2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection

from polypicker.api.deps import current_season, get_conn, q, q1
from polypicker.api.queries import COMPETITIVE_SQL, meta
from polypicker.domain.games import season_label

router = APIRouter(tags=["players"])

_AGG = """
  count(*) FILTER (WHERE participation = 'played') AS games_played,
  round(avg(minutes) FILTER (WHERE participation = 'played')::numeric, 1) AS minutes,
  round(avg(points) FILTER (WHERE participation = 'played')::numeric, 1) AS points,
  round(avg(assists) FILTER (WHERE participation = 'played')::numeric, 1) AS assists,
  round(avg(rebounds) FILTER (WHERE participation = 'played')::numeric, 1) AS rebounds,
  round((sum(fgm)::numeric / nullif(sum(fga), 0)), 3) AS fg_pct,
  round((sum(fg3m)::numeric / nullif(sum(fg3a), 0)), 3) AS fg3_pct,
  round((sum(ftm)::numeric / nullif(sum(fta), 0)), 3) AS ft_pct,
  count(*) FILTER (WHERE participation IN ('dnp', 'inactive')) AS games_missed
"""


def _player(conn: Connection, player_id: int) -> dict:
    p = q1(conn, """SELECT player_id, first_name, last_name, full_name, birth_date, height_inches, weight_lbs,
                           position, jersey, country, school, draft_year, draft_round, draft_number
                    FROM players WHERE player_id = :p""", p=player_id)
    if not p:
        raise HTTPException(404, f"Player {player_id} not found")
    return p


def _log_rows(conn: Connection, player_id: int, season: int, limit: int, offset: int) -> list[dict]:
    return q(conn, f"""
        SELECT s.game_id, g.game_date_et AS date, g.season_type, s.team_id, t.abbreviation AS team,
               o.abbreviation AS opponent, s.is_home, (CASE WHEN tg.win THEN 'W' ELSE 'L' END) AS result,
               s.participation, s.status_comment, s.starter, s.minutes, s.points, s.assists, s.rebounds,
               s.fgm, s.fga, s.fg3m, s.fg3a, s.ftm, s.fta, s.steals, s.blocks, s.turnovers, s.plus_minus
        FROM player_game_stats s JOIN games g USING (game_id)
        JOIN teams t ON t.team_id = s.team_id JOIN teams o ON o.team_id = s.opponent_team_id
        LEFT JOIN team_game_stats tg ON tg.game_id = s.game_id AND tg.team_id = s.team_id
        WHERE s.player_id = :p AND g.season = :s AND g.season_type IN {COMPETITIVE_SQL}
        ORDER BY g.tipoff_utc DESC LIMIT :lim OFFSET :off""", p=player_id, s=season, lim=limit, off=offset)


def _aggregates(conn: Connection, player_id: int, season: int) -> dict:
    out = {}
    for name, n in (("last5", 5), ("last10", 10), ("season", 10_000)):
        out[name] = q1(conn, f"""
            SELECT {_AGG} FROM (
              SELECT s.* FROM player_game_stats s JOIN games g USING (game_id)
              WHERE s.player_id = :p AND g.season = :s AND g.season_type IN {COMPETITIVE_SQL}
                AND s.participation = 'played'
              ORDER BY g.tipoff_utc DESC LIMIT :n) x""", p=player_id, s=season, n=n)
    return out


@router.get("/players")
def search_players(q_: str | None = Query(None, alias="q", min_length=2, max_length=60),
                   team: int | None = None, limit: int = Query(25, ge=1, le=100), conn: Connection = Depends(get_conn)):
    """Find players by name and/or current team (FR-08.2)."""
    if not q_ and not team:
        raise HTTPException(400, "Provide q (name search) or team")
    s = current_season(conn)
    rows = q(conn, """
        SELECT p.player_id, p.full_name, p.position, h.team_id, t.abbreviation AS team
        FROM players p
        LEFT JOIN LATERAL (SELECT team_id FROM player_team_history WHERE player_id = p.player_id
                           ORDER BY last_game_utc DESC LIMIT 1) h ON true
        LEFT JOIN teams t ON t.team_id = h.team_id
        WHERE (CAST(:q AS text) IS NULL OR lower(p.full_name) LIKE '%' || lower(CAST(:q AS text)) || '%')
          AND (CAST(:t AS bigint) IS NULL OR h.team_id = CAST(:t AS bigint))
          AND EXISTS (SELECT 1 FROM player_team_history x WHERE x.player_id = p.player_id AND x.season >= :s - 1)
        ORDER BY p.full_name LIMIT :lim""", q=q_, t=team, s=s, lim=limit)
    return {"data": rows, "meta": meta()}


@router.get("/players/{player_id}")
def player_detail(player_id: int, season: int | None = None, conn: Connection = Depends(get_conn)):
    """Profile, current team, season averages and last-5/10 summaries (FR-05.2)."""
    p = _player(conn, player_id)
    s = season or current_season(conn)
    team = q1(conn, """SELECT h.team_id, t.abbreviation, t.full_name FROM player_team_history h
                       JOIN teams t USING (team_id) WHERE h.player_id = :p ORDER BY h.last_game_utc DESC LIMIT 1""",
              p=player_id)
    seasons = q(conn, f"""
        SELECT g.season, {_AGG} FROM player_game_stats s JOIN games g USING (game_id)
        WHERE s.player_id = :p AND g.season_type IN {COMPETITIVE_SQL} GROUP BY g.season ORDER BY g.season DESC""",
                p=player_id)
    for r in seasons:
        r["season_label"] = season_label(r["season"])
    return {"data": {"player": p, "current_team": team, "season": s, "season_label": season_label(s),
                     "summary": _aggregates(conn, player_id, s), "seasons": seasons},
            "meta": meta()}


@router.get("/players/{player_id}/gamelog")
def player_gamelog(player_id: int, season: int | None = None, limit: int = Query(82, ge=1, le=120),
                   offset: int = Query(0, ge=0), conn: Connection = Depends(get_conn)):
    """Game-by-game log plus last-5 / last-10 / season aggregates (FR-03.5, FR-04.3, FR-05.2).

    Rows with participation 'dnp' / 'inactive' / 'unknown' have NULL stats -- not zeros (NFR-08.2)."""
    _player(conn, player_id)
    s = season or current_season(conn)
    return {"data": {"player_id": player_id, "season": s, "season_label": season_label(s),
                     "aggregates": _aggregates(conn, player_id, s),
                     "games": _log_rows(conn, player_id, s, limit, offset)},
            "meta": meta(limit=limit, offset=offset)}

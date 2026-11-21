"""Health, data freshness / attribution, and global search (FR-07.4, FR-08.2, NFR-10.3)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection

from polypicker.api.deps import current_season, get_conn, q
from polypicker.api.queries import freshness, meta
from polypicker.api.schemas import Envelope, SearchResult
from polypicker.config import DISCLAIMER

router = APIRouter(tags=["meta"])


@router.get("/health")
def health(conn: Connection = Depends(get_conn)):
    q(conn, "SELECT 1")
    return {"status": "ok"}


@router.get("/status")
def status(conn: Connection = Depends(get_conn)):
    """Last data sync, last forecast run, active model versions, data sources and disclaimer."""
    models = q(conn, "SELECT target, version, trained_at FROM models WHERE is_active ORDER BY target")
    sources = q(conn, "SELECT code, name, url, license_note, approved_for FROM data_sources ORDER BY id")
    return {"data": {"freshness": freshness(conn), "current_season": current_season(conn),
                     "active_models": models, "data_sources": sources, "disclaimer": DISCLAIMER,
                     "read_only": True, "wagering": False},
            "meta": meta()}


@router.get("/search", response_model=Envelope[list[SearchResult]])
def search(q_: str = Query(..., alias="q", min_length=2, max_length=60), limit: int = Query(10, ge=1, le=50),
           conn: Connection = Depends(get_conn)):
    """Search teams (name, city, abbreviation) and recent players by name (FR-08.2)."""
    s = current_season(conn)
    teams = q(conn, """
        SELECT 'team' AS type, team_id AS id, full_name AS name, abbreviation AS subtitle FROM teams
        WHERE lower(full_name) LIKE '%' || lower(:q) || '%' OR lower(abbreviation) = lower(:q)
        ORDER BY full_name LIMIT :lim""", q=q_, lim=limit)
    players = q(conn, """
        SELECT 'player' AS type, p.player_id AS id, p.full_name AS name, t.abbreviation AS subtitle
        FROM players p
        JOIN LATERAL (SELECT team_id FROM player_team_history WHERE player_id = p.player_id
                      ORDER BY last_game_utc DESC LIMIT 1) h ON true
        JOIN teams t ON t.team_id = h.team_id
        WHERE lower(p.full_name) LIKE '%' || lower(:q) || '%'
          AND EXISTS (SELECT 1 FROM player_team_history x WHERE x.player_id = p.player_id AND x.season >= :s - 1)
        ORDER BY (lower(p.full_name) LIKE lower(:q) || '%') DESC, p.full_name LIMIT :lim""", q=q_, s=s, lim=limit)
    return {"data": (teams + players)[:limit], "meta": meta()}

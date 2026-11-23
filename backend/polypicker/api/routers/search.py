"""One search across every sport and competition PolyPicker covers (the header search box)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection

from polypicker.api.deps import current_season, get_conn, q
from polypicker.api.queries import meta
from polypicker.soccer.leagues import LEAGUES

router = APIRouter(tags=["search"])

DOMESTIC = [lg.competition for lg in LEAGUES.values() if lg.kind == "league"]
SLUG = {lg.competition: lg.slug for lg in LEAGUES.values()}
SHORT = {lg.competition: lg.short for lg in LEAGUES.values()}


@router.get("/search/all")
def search_all(q_: str = Query(..., alias="q", min_length=2, max_length=60), per_group: int = Query(5, ge=1, le=10),
               conn: Connection = Depends(get_conn)):
    """Teams and recent players for NBA and NFL, and clubs from every soccer competition (current or last season).
    Names starting with the query rank first. Each result carries the link the web app opens."""
    like, prefix = f"%{q_.lower()}%", f"{q_.lower()}%"
    s = current_season(conn)
    nba_teams = q(conn, """
        SELECT 'nba' sport, 'team' AS type, team_id::text AS id, full_name AS name, abbreviation AS abbr,
               conference || ' · ' || coalesce(division, '') AS subtitle, '/nba/teams/' || abbreviation AS href
        FROM teams WHERE is_active AND (lower(full_name) LIKE :l OR lower(abbreviation) = lower(:q))
        ORDER BY (lower(name) LIKE :p OR lower(city) LIKE :p) DESC, full_name LIMIT :n""", l=like, p=prefix, q=q_, n=per_group)
    nba_players = q(conn, """
        SELECT 'nba' sport, 'player' AS type, p.player_id::text AS id, p.full_name AS name, t.abbreviation AS abbr,
               t.abbreviation || coalesce(' · ' || p.position, '') AS subtitle, '/nba/players/' || p.player_id AS href
        FROM players p
        JOIN LATERAL (SELECT team_id FROM player_team_history WHERE player_id = p.player_id
                      ORDER BY last_game_utc DESC LIMIT 1) h ON true
        JOIN teams t ON t.team_id = h.team_id
        WHERE lower(p.full_name) LIKE :l
          AND EXISTS (SELECT 1 FROM player_team_history x WHERE x.player_id = p.player_id AND x.season >= :s - 1)
        ORDER BY (lower(p.full_name) LIKE :p OR lower(p.full_name) LIKE '% ' || :p) DESC, p.full_name LIMIT :n""",
                    l=like, p=prefix, s=s, n=per_group)
    nfl_teams = q(conn, """
        SELECT 'nfl' sport, 'team' AS type, team_id::text AS id, full_name AS name, abbreviation AS abbr,
               division AS subtitle, '/nfl/teams/' || abbreviation AS href
        FROM nfl_teams WHERE is_active AND (lower(full_name) LIKE :l OR lower(abbreviation) = lower(:q))
        ORDER BY (lower(name) LIKE :p) DESC, full_name LIMIT :n""", l=like, p=prefix, q=q_, n=per_group)
    nfl_players = q(conn, """
        SELECT 'nfl' sport, 'player' AS type, p.player_id AS id, p.full_name AS name, t.abbreviation AS abbr,
               coalesce(t.abbreviation || ' · ', '') || coalesce(p.position, '') AS subtitle, '/nfl/players/' || p.player_id AS href
        FROM nfl_players p
        LEFT JOIN LATERAL (SELECT s.team_id FROM nfl_player_game_stats s JOIN nfl_games g USING (game_id)
                           WHERE s.player_id = p.player_id ORDER BY g.kickoff_utc DESC LIMIT 1) lt ON true
        LEFT JOIN nfl_teams t ON t.team_id = lt.team_id
        WHERE lower(p.full_name) LIKE :l AND EXISTS (
            SELECT 1 FROM nfl_player_game_stats s JOIN nfl_games g USING (game_id)
            WHERE s.player_id = p.player_id AND g.season >= (SELECT max(season) - 1 FROM nfl_games))
        ORDER BY (lower(p.full_name) LIKE :p OR lower(p.full_name) LIKE '% ' || :p) DESC, p.full_name LIMIT :n""",
                    l=like, p=prefix, n=per_group)
    # A club's link goes to its domestic league when we cover one, else the most recent cup it played in.
    clubs = q(conn, """
        SELECT 'soccer' sport, 'team' AS type, t.team_id::text AS id, t.name, t.abbreviation AS abbr, t.color, h.competition
        FROM soccer_teams t
        JOIN LATERAL (SELECT g.competition FROM soccer_games g
                      WHERE (g.home_team_id = t.team_id OR g.away_team_id = t.team_id)
                        AND g.kickoff_utc > now() - interval '18 months'
                      ORDER BY (g.competition = ANY(:dom)) DESC, g.kickoff_utc DESC LIMIT 1) h ON true
        WHERE lower(t.name) LIKE :l OR lower(coalesce(t.short_name, '')) LIKE :l OR lower(t.abbreviation) = lower(:q)
        ORDER BY (lower(t.name) LIKE :p OR lower(coalesce(t.short_name, '')) LIKE :p) DESC, t.name LIMIT :n""",
                 l=like, p=prefix, q=q_, dom=DOMESTIC, n=per_group)
    for c in clubs:
        comp = c.pop("competition")
        c.update(league=SLUG[comp], subtitle=SHORT[comp], href=f"/soccer/{SLUG[comp]}/teams/{c['id']}")
    items = nba_teams + nba_players + nfl_teams + nfl_players + clubs
    # Best matches first across sports: name starts with the query > a word starts with it > it appears inside a
    # word; exact abbreviation counts as a start; teams beat players on ties. Sports then appear in order of their
    # best result (the web app groups by sport in the order received).
    ql = q_.lower()
    def score(x: dict) -> float:
        name = x["name"].lower()
        s = 3 if name.startswith(ql) or (x.get("abbr") or "").lower() == ql else 2 if any(w.startswith(ql) for w in name.split()) else 1
        return s + (0.5 if x["type"] == "team" else 0)
    for x in items:
        x["score"] = score(x)
    best = {}
    for x in items:
        best[x["sport"]] = max(best.get(x["sport"], 0), x["score"])
    items.sort(key=lambda x: (-best[x["sport"]], -x["score"]))
    return {"data": items, "meta": meta()}

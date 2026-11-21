"""Shared SQL used by several routers."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Connection

from polypicker.api.deps import q, q1
from polypicker.config import ATTRIBUTION, DISCLAIMER
from polypicker.domain.games import season_label
from polypicker.domain.markets import round_probability_pair

COMPETITIVE_SQL = "('regular','playoffs','play_in','cup_final')"
RECORD_TYPES_SQL = "('regular')"  # standings / W-L records follow NBA rules: regular season only

GAME_SELECT = """
SELECT g.game_id, g.season, g.season_type, g.is_cup, g.game_label, g.game_sub_label, g.tipoff_utc,
       g.game_date_et, g.status, g.home_score, g.away_score, g.winner_team_id, g.home_team_id, g.away_team_id,
       g.arena_name, g.arena_city, g.arena_state, g.periods, g.attendance
FROM games g
"""


def meta(**kw) -> dict:
    return {"generated_at": datetime.now(timezone.utc), "attribution": ATTRIBUTION, **kw}


def prediction_meta(**kw) -> dict:
    return meta(disclaimer=DISCLAIMER, **kw)


def teams_by_id(conn: Connection) -> dict[int, dict]:
    return {r["team_id"]: r for r in q(conn, """
        SELECT team_id, abbreviation, city, name, full_name, conference, division FROM teams""")}


def resolve_team(conn: Connection, ident: str) -> dict | None:
    if ident.isdigit():
        return q1(conn, "SELECT * FROM teams WHERE team_id = :i", i=int(ident))
    return q1(conn, "SELECT * FROM teams WHERE upper(abbreviation) = upper(:a)", a=ident)


def headlines(conn: Connection, game_ids: list[int]) -> dict[int, dict]:
    """Latest moneyline + projected score per game. Live forecasts win over backtests."""
    if not game_ids:
        return {}
    rows = q(conn, """
        WITH latest AS (
          SELECT DISTINCT ON (game_id) game_id, kind, revision
          FROM predictions WHERE game_id = ANY(:ids) AND market = 'moneyline'
          ORDER BY game_id, (kind = 'live') DESC, revision DESC)
        SELECT p.game_id, p.kind, p.revision, p.market, p.side, p.probability, p.projection,
               p.published_at, p.data_cutoff_utc
        FROM predictions p JOIN latest l USING (game_id, kind, revision)
        WHERE p.market IN ('moneyline', 'team_points')""", ids=game_ids)
    out: dict[int, dict] = {}
    for r in rows:
        h = out.setdefault(r["game_id"], {"kind": r["kind"], "revision": r["revision"],
                                         "published_at": r["published_at"], "data_cutoff_utc": r["data_cutoff_utc"]})
        if r["market"] == "moneyline" and r["side"] == "home":
            h["p_home_win"] = r["probability"]
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
    return out


def records_before(conn: Connection, games: list[dict]) -> dict[tuple[int, int], str]:
    """Each team's regular-season W-L entering each listed game."""
    if not games:
        return {}
    pairs = [(g["game_id"], g["home_team_id"]) for g in games] + [(g["game_id"], g["away_team_id"]) for g in games]
    rows = q(conn, f"""
        WITH target AS (
          SELECT t.game_id, t.team_id, g.season, g.tipoff_utc
          FROM unnest(CAST(:gids AS bigint[]), CAST(:tids AS bigint[])) AS t(game_id, team_id)
          JOIN games g ON g.game_id = t.game_id)
        SELECT t.game_id, t.team_id,
               count(*) FILTER (WHERE s.win) AS w, count(*) FILTER (WHERE NOT s.win) AS l
        FROM target t
        LEFT JOIN games g2 ON g2.season = t.season AND g2.season_type IN {RECORD_TYPES_SQL}
             AND g2.status = 'final' AND g2.tipoff_utc < t.tipoff_utc
             AND (g2.home_team_id = t.team_id OR g2.away_team_id = t.team_id)
        LEFT JOIN team_game_stats s ON s.game_id = g2.game_id AND s.team_id = t.team_id
        GROUP BY 1, 2""", gids=[p[0] for p in pairs], tids=[p[1] for p in pairs])
    return {(r["game_id"], r["team_id"]): f"{r['w']}-{r['l']}" for r in rows}


def game_summaries(conn: Connection, rows: list[dict]) -> list[dict]:
    teams = teams_by_id(conn)
    heads = headlines(conn, [r["game_id"] for r in rows])
    recs = records_before(conn, rows)
    out = []
    for r in rows:
        def side(tid, score):
            t = teams[tid]
            return {**t, "score": score, "record": recs.get((r["game_id"], tid))}
        out.append({
            "game_id": r["game_id"], "season": r["season"], "season_label": season_label(r["season"]),
            "season_type": r["season_type"], "is_cup": r["is_cup"], "label": r["game_label"],
            "sub_label": r["game_sub_label"], "tipoff_utc": r["tipoff_utc"], "game_date": r["game_date_et"],
            "status": r["status"], "home": side(r["home_team_id"], r["home_score"]),
            "away": side(r["away_team_id"], r["away_score"]), "winner_team_id": r["winner_team_id"],
            "prediction": heads.get(r["game_id"]),
        })
    return out


def freshness(conn: Connection) -> dict:
    ing = q1(conn, """SELECT finished_at, source_updated_at FROM ingestion_runs
                      WHERE status = 'succeeded' AND job = 'ingest' ORDER BY finished_at DESC LIMIT 1""")
    pred = q1(conn, """SELECT finished_at, data_cutoff_utc FROM prediction_runs
                       WHERE status = 'succeeded' AND kind = 'live' ORDER BY finished_at DESC LIMIT 1""")
    return {"last_data_sync_utc": ing and ing["finished_at"],
            "source_updated_utc": ing and ing["source_updated_at"],
            "last_live_forecast_run_utc": pred and pred["finished_at"]}

"""Idempotent upserts into PostgreSQL (FR-04.5, FR-10.5, NFR-08.3).

Rows are keyed on their natural primary key. On conflict a row is only rewritten when a
data column actually changed (``IS DISTINCT FROM``), so re-running an import is a no-op,
and a provider stat correction shows up as an "updated" row with fresh provenance.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

import polars as pl
from sqlalchemy import Connection, Table, func, literal_column, text, tuple_
from sqlalchemy.dialects.postgresql import insert

from polypicker.db import models as m

PROVENANCE = {"source_id", "ingestion_run_id", "source_updated_at", "updated_at"}
MAX_PARAMS = 60000  # Postgres allows 65535 bind parameters per statement


def _chunks(rows: list[dict], n: int) -> Iterable[list[dict]]:
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def upsert(conn: Connection, table: Table, df: pl.DataFrame, *, provenance: dict | None = None) -> dict[str, int]:
    """Insert or update rows; returns {'inserted': n, 'updated': n, 'unchanged': n}."""
    if df.is_empty():
        return {"inserted": 0, "updated": 0, "unchanged": 0}
    pk = [c.name for c in table.primary_key.columns]
    data_cols = [c for c in df.columns if c not in pk and c not in PROVENANCE]
    has_prov = provenance is not None and "source_id" in table.c
    rows = df.to_dicts()
    if has_prov:
        for r in rows:
            r.update(provenance)
    inserted = updated = 0
    chunk_size = max(1, MAX_PARAMS // len(rows[0]))
    for chunk in _chunks(rows, chunk_size):
        stmt = insert(table).values(chunk)
        set_ = {c: stmt.excluded[c] for c in data_cols}
        if has_prov:
            set_.update({c: stmt.excluded[c] for c in provenance})
            set_["updated_at"] = func.now()
        where = None
        if data_cols:
            where = tuple_(*[table.c[c] for c in data_cols]).is_distinct_from(
                tuple_(*[stmt.excluded[c] for c in data_cols]))
        stmt = (stmt.on_conflict_do_update(index_elements=pk, set_=set_, where=where) if data_cols
                else stmt.on_conflict_do_nothing(index_elements=pk))
        # xmax = 0 identifies freshly inserted rows; unchanged rows are not returned at all.
        res = conn.execute(stmt.returning(literal_column("(xmax = 0)").label("ins")))
        flags = [r.ins for r in res]
        inserted += sum(flags)
        updated += len(flags) - sum(flags)
    return {"inserted": inserted, "updated": updated, "unchanged": len(rows) - inserted - updated}


def upsert_seasons(conn: Connection, games: pl.DataFrame) -> dict[str, int]:
    """Create seasons and widen their date range. Never narrows it, so a source that only
    sends a few games (e.g. last night's results) can't shrink a season to those dates."""
    from polypicker.domain.games import season_label
    if games.is_empty():
        return {"upserted": 0}
    s = (games.group_by("season").agg(pl.col("game_date_et").min().alias("start_date"),
                                      pl.col("game_date_et").max().alias("end_date"))
         .with_columns(pl.col("season").map_elements(season_label, return_dtype=pl.String).alias("label")))
    t = m.Season.__table__
    stmt = insert(t).values(s.to_dicts())
    stmt = stmt.on_conflict_do_update(index_elements=["season"], set_={
        "label": stmt.excluded.label,
        "start_date": func.least(t.c.start_date, stmt.excluded.start_date),
        "end_date": func.greatest(t.c.end_date, stmt.excluded.end_date)})
    conn.execute(stmt)
    return {"upserted": s.height}


def drop_result_downgrades(games: pl.DataFrame, final_ids: set[int]) -> pl.DataFrame:
    """Rows that would turn a game with a final result back into scheduled/unknown/live."""
    if games.is_empty() or not final_ids:
        return games.clear()
    return games.filter((pl.col("status") != "final") & pl.col("game_id").is_in(list(final_ids)))


def protect_final_results(conn: Connection, games: pl.DataFrame, table: str = "games") -> tuple[pl.DataFrame, int]:
    """Never let a source without a result overwrite one we already have (e.g. re-importing an older
    dataset download after the live feed recorded the final). Returns (games to load, rows skipped)."""
    if games.is_empty():
        return games, 0
    assert table in ("games", "nfl_games", "soccer_games")
    finals = {r[0] for r in conn.execute(text(
        f"SELECT game_id FROM {table} WHERE status = 'final' AND game_id = ANY(:ids)"),
        {"ids": games["game_id"].to_list()})}
    bad = drop_result_downgrades(games, finals)
    if bad.is_empty():
        return games, 0
    return games.filter(~pl.col("game_id").is_in(bad["game_id"].implode())), bad.height


def upsert_id_mappings(conn: Connection, source_id: int, data) -> int:
    """The research source already uses NBA IDs, which we adopt as canonical: identity mappings.
    A future provider with its own IDs writes real mappings here instead (FR-10.3)."""
    frames = [
        data.teams.select(pl.lit("team").alias("entity_type"), pl.col("team_id").alias("canonical_id")),
        data.players.select(pl.lit("player").alias("entity_type"), pl.col("player_id").alias("canonical_id")),
        data.games.select(pl.lit("game").alias("entity_type"), pl.col("game_id").alias("canonical_id")),
    ]
    df = pl.concat(frames).with_columns(pl.col("canonical_id").cast(pl.String).alias("source_key"),
                                        pl.lit(source_id).alias("source_id"))
    return upsert(conn, m.SourceIdMapping.__table__, df.select("source_id", "entity_type", "source_key",
                                                               "canonical_id"))["inserted"]


def refresh_player_team_history(conn: Connection) -> None:
    conn.execute(text("""
        INSERT INTO player_team_history (player_id, team_id, season, first_game_utc, last_game_utc, games)
        SELECT p.player_id, p.team_id, g.season, min(g.tipoff_utc), max(g.tipoff_utc), count(*)
        FROM player_game_stats p JOIN games g USING (game_id)
        WHERE p.participation = 'played'
        GROUP BY 1, 2, 3
        ON CONFLICT (player_id, team_id, season) DO UPDATE
          SET first_game_utc = excluded.first_game_utc, last_game_utc = excluded.last_game_utc,
              games = excluded.games
          WHERE (player_team_history.first_game_utc, player_team_history.last_game_utc, player_team_history.games)
                IS DISTINCT FROM (excluded.first_game_utc, excluded.last_game_utc, excluded.games)
    """))


def mark_stale_scheduled(conn: Connection, now: datetime) -> int:
    """Games whose tipoff passed without a result being ingested -> 'unknown' (not guessed as postponed)."""
    res = conn.execute(text("""
        UPDATE games SET status = 'unknown', updated_at = now()
        WHERE status = 'scheduled' AND tipoff_utc < :now - interval '6 hours'
    """), {"now": now})
    return res.rowcount

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from sqlalchemy import Connection, event, text

from polypicker.db.session import api_engine
from polypicker.domain.games import ET

_hooked = False


def _engine():
    global _hooked
    eng = api_engine()
    if not _hooked:
        # Defense in depth on top of the SELECT-only role: every session is read-only (FR-11.2).
        @event.listens_for(eng, "connect")
        def _ro(dbapi_conn, _):
            with dbapi_conn.cursor() as cur:
                cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
                cur.execute("SET statement_timeout = '5s'")
            dbapi_conn.commit()
        _hooked = True
    return eng


def get_conn() -> Iterator[Connection]:
    with _engine().connect() as conn:
        yield conn


def q(conn: Connection, sql: str, **params):
    """Parameterized query helper -> list of dict rows (NFR-02.3)."""
    return [dict(r._mapping) for r in conn.execute(text(sql), params)]


def q1(conn: Connection, sql: str, **params) -> dict | None:
    rows = q(conn, sql, **params)
    return rows[0] if rows else None


def today_et():
    return datetime.now(ET).date()


def current_season(conn: Connection) -> int:
    row = q1(conn, """SELECT season FROM games WHERE game_date_et <= :d ORDER BY game_date_et DESC LIMIT 1""",
             d=today_et())
    return row["season"] if row else q1(conn, "SELECT max(season) s FROM games")["s"]

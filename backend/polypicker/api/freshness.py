"""When a game's numbers were last refreshed, for the "Updated 4 min ago" labels on game cards and the grader.

Two times per game:

* ``checked_at``: the last successful live forecast run for the game's league. Runs only write a new forecast
  revision when an input changed, so ``published_at`` alone would make an up-to-date forecast look stale.
* ``odds_at``: the latest fetch of any market price we hold for the game (sportsbooks, Polymarket, Kalshi,
  The Odds API).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Connection

from polypicker.api.deps import q

# per sport: the tables holding market prices for a game, each with game_id + fetched_at
ODDS_TABLES = {"nba": ("nba_odds", "polymarket_nba", "kalshi_nba"),
               "nfl": ("nfl_market_prices",),
               "soccer": ("soccer_odds", "soccer_odds_snapshots")}


def checked_at(conn: Connection) -> dict[str, datetime]:
    """{'nba' | 'nfl' | soccer competition code: finish time of its latest successful live forecast run}."""
    rows = q(conn, """
        SELECT CASE WHEN stats->>'league' = 'soccer' THEN stats->>'competition'
                    ELSE coalesce(stats->>'league', 'nba') END AS k, max(finished_at) AS t
        FROM prediction_runs
        WHERE kind = 'live' AND status = 'succeeded' AND finished_at > now() - interval '30 days'
        GROUP BY 1""")
    return {r["k"]: r["t"] for r in rows if r["k"]}


def odds_at(conn: Connection, sport: str, ids: list[int]) -> dict[int, datetime]:
    if not ids:
        return {}
    union = " UNION ALL ".join(f"SELECT game_id, fetched_at FROM {t} WHERE game_id = ANY(:ids)" for t in ODDS_TABLES[sport])
    return {r["game_id"]: r["t"] for r in q(conn, f"SELECT game_id, max(fetched_at) AS t FROM ({union}) x GROUP BY 1", ids=ids)}


def stamp(heads: dict[int, dict], sport: str, conn: Connection, league_of: dict[int, str] | None = None) -> None:
    """Add ``checked_at`` / ``odds_at`` to each headline in place. ``league_of`` maps game -> soccer competition."""
    if not heads:
        return
    runs = checked_at(conn)
    odds = odds_at(conn, sport, list(heads))
    for gid, h in heads.items():
        key = (league_of or {}).get(gid, sport)
        chk = runs.get(key)
        pub = h.get("published_at")
        h["checked_at"] = max(t for t in (chk, pub) if t is not None) if (chk or pub) else None
        h["odds_at"] = odds.get(gid)

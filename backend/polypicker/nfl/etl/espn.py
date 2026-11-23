"""ESPN NFL scoreboard: live status, final scores and quarter scores for games that have kicked off.

One request per game date covers every game that day (a Sunday slate is a single call). ESPN event
ids are the canonical NFL game ids, so rows update in place; full box scores arrive with the next
nflverse import (usually overnight). Unofficial endpoint: failures are logged and retried next run.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import polars as pl
from sqlalchemy import Engine, text

from polypicker.etl import load
from polypicker.etl.issues import IssueLog
from polypicker.nfl import models as nm
from polypicker.nfl.etl.runner import tracked

log = logging.getLogger(__name__)
BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PolyPicker/1.0)", "Accept": "application/json"}

STATUS = {"STATUS_FINAL": "final", "STATUS_FINAL_OVERTIME": "final", "STATUS_IN_PROGRESS": "live",
          "STATUS_HALFTIME": "live", "STATUS_END_PERIOD": "live", "STATUS_END_OF_REGULATION": "live",
          "STATUS_OVERTIME": "live", "STATUS_DELAYED": "live", "STATUS_RAIN_DELAY": "live",
          "STATUS_POSTPONED": "postponed", "STATUS_CANCELED": "canceled", "STATUS_SUSPENDED": "postponed",
          "STATUS_SCHEDULED": "scheduled"}

Fetcher = Callable[[date], dict]


class FeedUnavailable(RuntimeError):
    pass


def fetch_scoreboard(day: date, timeout: float = 20) -> dict:
    import requests
    r = requests.get(f"{BASE}/scoreboard", params={"dates": day.strftime("%Y%m%d")}, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _int(v) -> int | None:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def parse_event(ev: dict, db_game: dict) -> tuple[dict | None, list[dict]]:
    """ESPN event -> (game update, team rows). Returns (None, []) if the event doesn't match the schedule."""
    comp = (ev.get("competitions") or [{}])[0]
    sides = {c.get("homeAway"): c for c in comp.get("competitors") or []}
    if "home" not in sides or "away" not in sides:
        return None, []
    status_name = ((ev.get("status") or comp.get("status") or {}).get("type") or {}).get("name", "")
    status = STATUS.get(status_name, "scheduled")
    g = {"game_id": db_game["game_id"], "status": status}
    if status not in ("final", "live"):
        return g, []
    hs, as_ = _int(sides["home"].get("score")), _int(sides["away"].get("score"))
    g.update(home_score=hs, away_score=as_)
    lines = {k: [_int(x.get("value")) for x in (sides[k].get("linescores") or [])] for k in ("home", "away")}
    periods = max(len(lines["home"]), len(lines["away"]))
    if status == "final":
        g["winner_team_id"] = (db_game["home_team_id"] if hs > as_ else db_game["away_team_id"]) if hs != as_ else None
        g["overtime"] = periods > 4 or "OVERTIME" in status_name
    teams = []
    for side, opp, tid, oid in (("home", "away", db_game["home_team_id"], db_game["away_team_id"]),
                                ("away", "home", db_game["away_team_id"], db_game["home_team_id"])):
        q = lines[side] + [None] * 4
        pts, opp_pts = (hs, as_) if side == "home" else (as_, hs)
        teams.append({"game_id": db_game["game_id"], "team_id": tid, "opponent_team_id": oid, "is_home": side == "home",
                      "points": pts,
                      "win": (None if status != "final" or pts == opp_pts else pts > opp_pts),
                      "q1_points": q[0], "q2_points": q[1], "q3_points": q[2], "q4_points": q[3],
                      "ot_points": (sum(x or 0 for x in lines[side][4:]) if len(lines[side]) >= 4 else None)})
    return g, teams


class EspnScoreboardSource:
    code = "espn_nfl"

    def __init__(self, engine: Engine, *, fetch: Fetcher | None = None, now: datetime | None = None,
                 lookback_days: int = 21, pause_seconds: float = 0.5, backfill_quarters: bool = False):
        self.engine, self.fetch = engine, fetch or fetch_scoreboard
        self.backfill_quarters = backfill_quarters
        self.now = now or datetime.now(timezone.utc)
        self.lookback, self.pause = timedelta(days=lookback_days), pause_seconds
        self.summary: dict = {}

    def pending(self) -> list[dict]:
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(text("""
                SELECT game_id, game_date_et, home_team_id, away_team_id, status FROM nfl_games
                WHERE kickoff_utc <= :now AND kickoff_utc >= :since
                  AND (status IN ('scheduled', 'unknown', 'live')
                       OR (:bq AND status = 'final' AND game_id IN (
                           SELECT game_id FROM nfl_team_game_stats WHERE q1_points IS NULL)))
                ORDER BY kickoff_utc"""), {"now": self.now + timedelta(minutes=5), "since": self.now - self.lookback,
                                           "bq": self.backfill_quarters})]

    def extract(self, issues: IssueLog) -> tuple[list[dict], list[dict]]:
        pend = self.pending()
        by_id = {g["game_id"]: g for g in pend}
        days = sorted({g["game_date_et"] for g in pend})
        games, teams, failed = [], [], 0
        for i, d in enumerate(days):
            if i and self.pause:
                time.sleep(self.pause)
            try:
                board = self.fetch(d)
            except Exception as e:  # noqa: BLE001
                failed += 1
                issues.add("nfl_game", str(d), "espn_fetch_failed", "warning", "skipped", error=str(e)[:300])
                continue
            for ev in board.get("events") or []:
                gid = _int(ev.get("id"))
                if gid not in by_id:
                    continue
                g, t = parse_event(ev, by_id[gid])
                if g is None:
                    issues.add("nfl_game", gid, "espn_event_unparsed", "warning", "skipped")
                    continue
                games.append(g)
                teams += t
        self.summary = {"pending": len(pend), "dates": len(days), "dates_failed": failed,
                        "final": sum(g["status"] == "final" for g in games),
                        "live": sum(g["status"] == "live" for g in games)}
        if days and failed == len(days) and failed >= 2:
            raise FeedUnavailable(f"all {failed} ESPN scoreboard requests failed")
        return games, teams



def sync_espn(engine: Engine, *, lookback_days: int = 21, retries: int = 2, fetch: Fetcher | None = None,
              backfill_quarters: bool = False) -> dict:
    """``backfill_quarters`` also re-reads final games missing quarter scores (their result rows are left as is)."""
    src = EspnScoreboardSource(engine, fetch=fetch, lookback_days=lookback_days, backfill_quarters=backfill_quarters)

    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        games, teams = src.extract(issues)
        now = datetime.now(timezone.utc)
        prov = {"source_id": source_id, "ingestion_run_id": run_id, "source_updated_at": now}
        st = {"source": src.summary}
        with engine.begin() as conn:
            # Only the result columns ESPN reported; a game that is already final is never touched.
            updated = 0
            for g in games:
                sets = ", ".join(f"{c} = :{c}" for c in g if c != "game_id")
                updated += conn.execute(text(
                    f"UPDATE nfl_games SET {sets}, source_id = :sid, ingestion_run_id = :rid, updated_at = now() "
                    "WHERE game_id = :game_id AND status IS DISTINCT FROM 'final'"),
                    {**g, "sid": source_id, "rid": run_id}).rowcount
            st["games_updated"] = updated
            if teams:
                df = pl.DataFrame(teams, schema={"game_id": pl.Int64, "team_id": pl.Int64, "opponent_team_id": pl.Int64,
                                                 "is_home": pl.Boolean, "points": pl.Int64, "win": pl.Boolean,
                                                 "q1_points": pl.Int64, "q2_points": pl.Int64, "q3_points": pl.Int64,
                                                 "q4_points": pl.Int64, "ot_points": pl.Int64})
                st["team_game_stats"] = load.upsert(conn, nm.NflTeamGameStats.__table__, df, provenance=prov)
        st["_source_updated_at"] = now
        return st

    return tracked(engine, src.code, "nfl_espn_sync", {"lookback_days": lookback_days}, run, retries=retries,
                   backoff_seconds=15)

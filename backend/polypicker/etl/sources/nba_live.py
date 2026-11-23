"""Adapter for NBA.com's live data feed (cdn.nba.com) via the ``nba_api`` package.

Keeps results current without re-downloading the research dataset: for every game that has
tipped off but has no final result yet, fetch its box score and emit canonical rows.

* Game, team and player IDs are NBA.com IDs, the same ones the Kaggle dataset uses, so rows
  update existing games in place.
* Only games already in the database are touched (the schedule comes from the base dataset).
  Team and player rows are re-emitted from the database unchanged, so this partial source
  never blanks columns it doesn't know (birth dates, arenas, ...). Unknown players (call-ups,
  two-way signings) are created from box-score names.
* Stat conventions match the base dataset: team rebounds = OREB + DREB (no "team" rebounds),
  team turnovers = player turnovers, team plus/minus = final margin.
* Uses the live CDN, not stats.nba.com, which doesn't answer many cloud/datacenter IPs.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import polars as pl
from sqlalchemy import Engine, text

from polypicker.domain.games import GameStatus
from polypicker.etl.issues import IssueLog
from polypicker.etl.sources.base import (GAME_COLUMNS, PLAYER_COLUMNS, PLAYER_GAME_COLUMNS,
                                         TEAM_COLUMNS, TEAM_GAME_COLUMNS, CanonicalData,
                                         SourceAdapter)
from polypicker.etl.sources.kaggle_csv import _conform, classify_participation

log = logging.getLogger(__name__)

Fetcher = Callable[[int], dict]

PENDING_STATUSES = (GameStatus.SCHEDULED.value, GameStatus.UNKNOWN.value, GameStatus.LIVE.value)


class FeedUnavailable(RuntimeError):
    """Every box-score request failed for several games (blocked, offline or feed down)."""


def fetch_boxscore(game_id: int, timeout: float = 30) -> dict:
    """Live box score JSON for an NBA game id (``{"meta": ..., "game": {...}}``)."""
    from nba_api.live.nba.endpoints import boxscore  # imported lazily: pulls in pandas/requests
    try:
        return boxscore.BoxScore(game_id=f"{game_id:010d}", timeout=timeout).get_dict()
    except ValueError as e:
        # nba_api turns an HTTP 403/404 (blocked IP, or no box score published yet) into a JSON decode error.
        raise RuntimeError(f"no box score for {game_id} (HTTP error or non-JSON response)") from e


# ----------------------------------------------------------------------------- parsing helpers

_ISO_MIN = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?$")


def parse_iso_minutes(v: str | None) -> float | None:
    """'PT25M01.00S' -> 25.0167; 'PT240M00.00S' -> 240.0; blank/garbage -> None."""
    if not v:
        return None
    m = _ISO_MIN.match(v.strip())
    if not m or not any(m.groups()):
        return None
    h, mi, s = (float(g) if g else 0.0 for g in m.groups())
    return h * 60 + mi + s / 60


_PREFIX = {"DNP": "DNP", "DND": "DND", "NWT": "NWT", "INACTIVE": "Inactive"}
_REASON = {"COACH": "Coach's Decision", "INJURY": "Injury/Illness", "ILLNESS": "Injury/Illness",
           "REST": "Rest", "PERSONAL": "Personal", "SUSPENSION": "League Suspension",
           "GLEAGUE": "G League - Two-Way", "TWOWAY": "G League - Two-Way", "NWT": "Not With Team"}


def not_playing_comment(status: str | None, played: str | None, reason: str | None) -> str | None:
    """Live-feed reason codes ('DNP_COACH', 'INACTIVE_INJURY', ...) -> the base dataset's comment style
    ("DNP - Coach's Decision", 'Inactive - Injury/Illness'), so participation is classified the same way."""
    if played == "1":
        return None
    if reason:
        head, _, rest = reason.upper().partition("_")
        prefix = _PREFIX.get(head)
        if prefix:
            detail = next((v for k, v in _REASON.items() if k in rest), rest.replace("_", " ").title())
            return f"{prefix} - {detail}" if detail else prefix
        return reason.replace("_", " ").title()
    # Active but didn't play, no reason given: the base dataset records this as a coach's decision.
    return "Inactive" if (status or "").upper() == "INACTIVE" else "DNP - Coach's Decision"


def _starting_position(pos: str | None) -> str | None:
    """'PG'/'SG' -> 'G', 'SF'/'PF' -> 'F', 'C' -> 'C' (the base dataset's G/F/C convention)."""
    if not pos:
        return None
    last = pos.strip().upper()[-1:]
    return last if last in ("G", "F", "C") else None


def game_status(game: dict) -> str:
    text_ = (game.get("gameStatusText") or "").lower()
    if "ppd" in text_ or "postpon" in text_:
        return GameStatus.POSTPONED.value
    if "cancel" in text_:
        return GameStatus.CANCELED.value
    return {3: GameStatus.FINAL.value, 2: GameStatus.LIVE.value}.get(game.get("gameStatus"), GameStatus.SCHEDULED.value)


def _i(v) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def parse_boxscore(payload: dict, db_game: dict) -> tuple[dict, list[dict], list[dict], list[dict]]:
    """One live box score -> (game row, team rows, player-game rows, player name rows).

    ``db_game`` is the game's current database row; its schedule fields (season, labels, arena...)
    are kept and only result fields are overwritten. Stats rows are only produced for final games.
    """
    g = payload["game"]
    home, away = g["homeTeam"], g["awayTeam"]
    if (home.get("teamId"), away.get("teamId")) != (db_game["home_team_id"], db_game["away_team_id"]):
        raise ValueError(f"teams in feed ({home.get('teamId')} vs {away.get('teamId')}) don't match the schedule")

    status = game_status(g)
    game = dict(db_game)
    game["status"] = status
    arena = g.get("arena") or {}
    for col, key in (("arena_name", "arenaName"), ("arena_city", "arenaCity"), ("arena_state", "arenaState")):
        game[col] = arena.get(key) or game.get(col)
    if g.get("gameTimeUTC"):
        game["tipoff_utc"] = datetime.fromisoformat(g["gameTimeUTC"].replace("Z", "+00:00"))
    if status not in (GameStatus.FINAL.value, GameStatus.LIVE.value):
        return game, [], [], []

    hs, as_ = _i(home.get("score")), _i(away.get("score"))
    game.update(home_score=hs, away_score=as_, periods=_i(g.get("period")))
    if status == GameStatus.LIVE.value:
        return game, [], [], []

    game["winner_team_id"] = (db_game["home_team_id"] if hs > as_ else db_game["away_team_id"]) if hs != as_ else None
    game["attendance"] = _i(g.get("attendance")) or game.get("attendance")

    teams, players, names = [], [], []
    for side, opp, is_home in ((home, away, True), (away, home, False)):
        st = side.get("statistics") or {}
        q = {p.get("period"): _i(p.get("score")) for p in side.get("periods") or []}
        ot = sum(v or 0 for k, v in q.items() if isinstance(k, int) and k > 4)
        oreb, dreb = _i(st.get("reboundsOffensive")), _i(st.get("reboundsDefensive"))
        pts, opp_pts = _i(side.get("score")), _i(opp.get("score"))
        teams.append({
            "game_id": game["game_id"], "team_id": side["teamId"], "opponent_team_id": opp["teamId"],
            "is_home": is_home, "win": pts > opp_pts, "points": pts,
            "minutes": parse_iso_minutes(st.get("minutes")),
            "fgm": _i(st.get("fieldGoalsMade")), "fga": _i(st.get("fieldGoalsAttempted")),
            "fg3m": _i(st.get("threePointersMade")), "fg3a": _i(st.get("threePointersAttempted")),
            "ftm": _i(st.get("freeThrowsMade")), "fta": _i(st.get("freeThrowsAttempted")),
            "oreb": oreb, "dreb": dreb, "reb": (oreb or 0) + (dreb or 0) if oreb is not None else None,
            "ast": _i(st.get("assists")), "stl": _i(st.get("steals")), "blk": _i(st.get("blocks")),
            "tov": _i(st.get("turnovers")), "pf": _i(st.get("foulsPersonal")),
            "plus_minus": pts - opp_pts,
            "q1_points": q.get(1), "q2_points": q.get(2), "q3_points": q.get(3), "q4_points": q.get(4),
            "ot_points": ot if q.get(1) is not None else None,
        })
        for p in side.get("players") or []:
            ps = p.get("statistics") or {}
            played = p.get("played") == "1"
            players.append({
                "game_id": game["game_id"], "player_id": p["personId"], "team_id": side["teamId"],
                "opponent_team_id": opp["teamId"], "is_home": is_home,
                "status_comment": not_playing_comment(p.get("status"), p.get("played"), p.get("notPlayingReason")),
                "starter": p.get("starter") == "1" and played,
                "starting_position": _starting_position(p.get("position")) if p.get("starter") == "1" else None,
                "minutes": parse_iso_minutes(ps.get("minutes")),
                "points": _i(ps.get("points")), "assists": _i(ps.get("assists")),
                "rebounds": _i(ps.get("reboundsTotal")), "oreb": _i(ps.get("reboundsOffensive")),
                "dreb": _i(ps.get("reboundsDefensive")), "fgm": _i(ps.get("fieldGoalsMade")),
                "fga": _i(ps.get("fieldGoalsAttempted")), "fg3m": _i(ps.get("threePointersMade")),
                "fg3a": _i(ps.get("threePointersAttempted")), "ftm": _i(ps.get("freeThrowsMade")),
                "fta": _i(ps.get("freeThrowsAttempted")), "steals": _i(ps.get("steals")),
                "blocks": _i(ps.get("blocks")), "turnovers": _i(ps.get("turnovers")),
                "fouls": _i(ps.get("foulsPersonal")), "plus_minus": _i(ps.get("plusMinusPoints")),
            })
            names.append({"player_id": p["personId"], "first_name": p.get("firstName") or None,
                          "last_name": p.get("familyName") or None, "jersey": p.get("jerseyNum") or None})
    return game, teams, players, names


def finish_player_rows(rows: list[dict]) -> pl.DataFrame:
    """Classify participation like the base dataset and keep non-appearances free of zero stats."""
    if not rows:
        return pl.DataFrame(schema=PLAYER_GAME_COLUMNS)
    df = _conform(pl.DataFrame(rows, infer_schema_length=None), PLAYER_GAME_COLUMNS)
    stat_names = [c for c in PLAYER_GAME_COLUMNS if c not in (
        "game_id", "player_id", "team_id", "opponent_team_id", "is_home", "participation", "status_comment",
        "starter", "starting_position")]
    any_stat = pl.any_horizontal([pl.col(c).fill_null(0) != 0 for c in
                                  ("points", "assists", "rebounds", "fga", "fta", "turnovers", "fouls",
                                   "steals", "blocks")])
    df = df.with_columns(classify_participation(pl.col("status_comment").fill_null(""), pl.col("minutes"), any_stat)
                         .alias("participation"))
    df = df.with_columns([pl.when(pl.col("participation") == "played").then(pl.col(c)).otherwise(None).alias(c)
                          for c in stat_names])
    return df.with_columns((pl.col("starter") & (pl.col("participation") == "played")).alias("starter"))


# ----------------------------------------------------------------------------- adapter

class NbaLiveSource(SourceAdapter):
    code = "nba_live"

    def __init__(self, engine: Engine, *, fetch: Fetcher | None = None, now: datetime | None = None,
                 lookback_days: int = 14, pause_seconds: float = 0.6):
        self.engine = engine
        self.fetch = fetch or fetch_boxscore
        self.now = now or datetime.now(timezone.utc)
        self.lookback = timedelta(days=lookback_days)
        self.pause = pause_seconds
        self._fetched_at: datetime | None = None
        self.summary: dict = {}

    def source_updated_at(self) -> datetime | None:
        return self._fetched_at

    def _rows(self, sql: str, **params) -> list[dict]:
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(text(sql), params)]

    def pending_games(self, first_season: int) -> list[dict]:
        cols = ", ".join(GAME_COLUMNS)
        return self._rows(f"""
            SELECT {cols} FROM games
            WHERE status = ANY(:st) AND season >= :fs AND tipoff_utc <= :now AND tipoff_utc >= :since
            ORDER BY tipoff_utc""", st=list(PENDING_STATUSES), fs=first_season, now=self.now,
                          since=self.now - self.lookback)

    def extract(self, first_season: int, issues: IssueLog) -> CanonicalData:
        pending = self.pending_games(first_season)
        games, teams_rows, player_rows, names = [], [], [], []
        failures = 0
        for i, g in enumerate(pending):
            if i and self.pause:
                time.sleep(self.pause)
            try:
                payload = self.fetch(g["game_id"])
                game, t, p, n = parse_boxscore(payload, g)
            except Exception as e:  # noqa: BLE001 - one bad game must not block the others
                failures += 1
                issues.add("game", g["game_id"], "live_fetch_failed", "warning", "skipped", error=str(e)[:300])
                log.warning("live box score %s: %s", g["game_id"], e)
                continue
            games.append(game)
            teams_rows += t
            player_rows += p
            names += n
        self._fetched_at = datetime.now(timezone.utc) if games else None
        self.summary = {"pending": len(pending), "fetched": len(games), "failed": failures,
                        "final": sum(g["status"] == GameStatus.FINAL.value for g in games),
                        "live": sum(g["status"] == GameStatus.LIVE.value for g in games)}
        # Several games and not one box score: the feed is blocked/down -> fail so it's retried and visible.
        # One or two stragglers (e.g. a box score that was never published) only log warnings.
        if not games and failures >= 3:
            raise FeedUnavailable(f"all {failures} live box-score requests failed (blocked, offline or feed down)")

        pgs = finish_player_rows(player_rows)
        played = set(pgs.filter(pl.col("participation") == "played")["game_id"].to_list()) if pgs.height else set()
        for game in games:
            game["has_player_stats"] = game["game_id"] in played

        # Teams and known players straight from the DB (no-op upserts); new players from box-score names.
        teams = pl.DataFrame(self._rows(f"SELECT {', '.join(TEAM_COLUMNS)} FROM teams"), schema=TEAM_COLUMNS)
        ids = sorted({n["player_id"] for n in names})
        known = self._rows(f"SELECT {', '.join(PLAYER_COLUMNS)} FROM players WHERE player_id = ANY(:ids)", ids=ids)
        known_ids = {r["player_id"] for r in known}
        new = {n["player_id"]: n for n in names if n["player_id"] not in known_ids}
        for pid in new:
            issues.add("player", pid, "new_player_from_live_feed", "info", "fixed", note="created from box-score name")
        new_rows = [{**n, "full_name": " ".join(x for x in (n["first_name"], n["last_name"]) if x) or None}
                    for n in new.values()]
        players = pl.concat([pl.DataFrame(known, schema=PLAYER_COLUMNS),
                             _conform(pl.DataFrame(new_rows, infer_schema_length=None), PLAYER_COLUMNS)
                             if new_rows else pl.DataFrame(schema=PLAYER_COLUMNS)])

        return CanonicalData(
            teams=teams,
            players=players,
            games=pl.DataFrame(games, schema=GAME_COLUMNS) if games else pl.DataFrame(schema=GAME_COLUMNS),
            team_game_stats=pl.DataFrame(teams_rows, schema=TEAM_GAME_COLUMNS) if teams_rows
            else pl.DataFrame(schema=TEAM_GAME_COLUMNS),
            player_game_stats=_conform(pgs, PLAYER_GAME_COLUMNS),
        )

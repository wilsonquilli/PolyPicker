"""Canonical season / game-type / status rules (NFR-08.1, FR-04.4).

The source data labels game types inconsistently ("NBA Emirates Cup", "NBA Cup",
"Pre Season", ...). The NBA game ID is reliable instead: 8 digits, ``TYYNNNNN``
where T is the game type and YY the season start year.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


class SeasonType(StrEnum):
    PRESEASON = "preseason"
    REGULAR = "regular"
    ALL_STAR = "all_star"
    PLAYOFFS = "playoffs"
    PLAY_IN = "play_in"
    CUP_FINAL = "cup_final"  # NBA Cup championship: does not count toward regular-season records


class GameStatus(StrEnum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    FINAL = "final"
    POSTPONED = "postponed"
    CANCELED = "canceled"
    UNKNOWN = "unknown"  # tipoff passed but no result has been ingested yet


_TYPE_DIGIT = {
    "1": SeasonType.PRESEASON,
    "2": SeasonType.REGULAR,
    "3": SeasonType.ALL_STAR,
    "4": SeasonType.PLAYOFFS,
    "5": SeasonType.PLAY_IN,
    "6": SeasonType.CUP_FINAL,
}

# Season types whose games are "real" competitive NBA games used for records and modeling.
COMPETITIVE_TYPES = (SeasonType.REGULAR, SeasonType.PLAYOFFS, SeasonType.PLAY_IN, SeasonType.CUP_FINAL)


def _gid(game_id: int | str) -> str:
    s = str(int(game_id)).zfill(8)
    if len(s) != 8:
        raise ValueError(f"Unexpected NBA game id: {game_id!r}")
    return s


def season_type_from_game_id(game_id: int | str) -> SeasonType:
    try:
        return _TYPE_DIGIT[_gid(game_id)[0]]
    except KeyError as e:
        raise ValueError(f"Unknown game type digit in game id {game_id!r}") from e


def season_from_game_id(game_id: int | str) -> int:
    """Season start year, e.g. 22300001 -> 2023 (the 2023-24 season)."""
    yy = int(_gid(game_id)[1:3])
    return 1900 + yy if yy >= 46 else 2000 + yy


def season_label(season: int) -> str:
    """2023 -> '2023-24'."""
    return f"{season}-{str(season + 1)[-2:]}"


def season_from_date(d: datetime) -> int:
    """Season start year for a calendar date (NBA seasons start in Oct and end by Jul)."""
    return d.year if d.month >= 8 else d.year - 1


def et_naive_to_utc(dt: datetime) -> datetime:
    """Source files give tipoff as a naive US-Eastern wall-clock time; store UTC."""
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt.replace(tzinfo=ET).astimezone(timezone.utc)


def is_cup_game(*labels: str | None) -> bool:
    text = " ".join(l for l in labels if l).lower()
    return "cup" in text or "in-season" in text


def derive_status(has_result: bool, tipoff_utc: datetime, now: datetime | None = None,
                  explicit: GameStatus | None = None) -> GameStatus:
    """Status when the source has no live status feed (FR-01.4).

    ``explicit`` wins (e.g. a known postponement). Otherwise a game with a final
    score is FINAL, a future game is SCHEDULED, and a past game without a result is
    UNKNOWN rather than guessed as postponed.
    """
    if explicit is not None:
        return explicit
    if has_result:
        return GameStatus.FINAL
    now = now or datetime.now(timezone.utc)
    return GameStatus.SCHEDULED if tipoff_utc > now else GameStatus.UNKNOWN

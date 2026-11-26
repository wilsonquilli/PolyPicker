"""Response contracts shared by the web and mobile clients via OpenAPI (NFR-06.3).

Every timestamp is ISO-8601 UTC; ``game_date`` is the US-Eastern calendar date the NBA uses.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Meta(BaseModel):
    generated_at: datetime
    attribution: str
    disclaimer: str | None = None
    total: int | None = None
    limit: int | None = None
    offset: int | None = None
    extra: dict[str, Any] | None = None


class Envelope(BaseModel, Generic[T]):
    data: T
    meta: Meta


class TeamRef(BaseModel):
    team_id: int
    abbreviation: str
    city: str
    name: str
    full_name: str
    conference: str | None = None
    division: str | None = None


class GameSide(TeamRef):
    score: int | None = None
    record: str | None = Field(None, description="Season W-L before this game (competitive games only)")


class PredictionHeadline(BaseModel):
    kind: Literal["live", "backtest"]
    revision: int
    published_at: datetime
    data_cutoff_utc: datetime
    p_home_win: float = Field(description="0-1 probability, full game including overtime")
    p_away_win: float
    p_home_pct: float = Field(description="Rounded percentage; home + away = 100.0")
    p_away_pct: float
    projected_home_points: float | None
    projected_away_points: float | None
    favorite: Literal["home", "away"]
    favorite_label: Literal["favorite", "slight favorite", "toss-up"] = Field(
        description="Text label so favorites are never encoded by color alone (FR-07.2)")
    checked_at: datetime | None = Field(None, description="Last successful forecast run for this league")
    odds_at: datetime | None = Field(None, description="Latest fetch of any market price for this game")


class Reason(BaseModel):
    """One plain-language reason behind a forecast (see api/reasons.py); the web app phrases it."""
    code: str
    side: Literal["home", "away"] | None
    favors: Literal["home", "away", "draw"] | None
    params: dict[str, Any]


class GameSummary(BaseModel):
    game_id: int
    season: int
    season_label: str
    season_type: str
    is_cup: bool
    label: str | None
    sub_label: str | None
    tipoff_utc: datetime
    game_date: date
    status: str
    home: GameSide
    away: GameSide
    winner_team_id: int | None
    prediction: PredictionHeadline | None
    reasons: list[Reason] | None = None


class GameDetail(GameSummary):
    arena: dict[str, str | None]
    periods: int | None
    attendance: int | None
    line_score: list[dict[str, Any]] | None
    context: dict[str, Any]


class SearchResult(BaseModel):
    type: Literal["team", "player"]
    id: int
    name: str
    subtitle: str | None

"""Provider adapter interface (spec §5.3, §6.1).

Each adapter turns one provider's raw data into *canonical* Polars frames whose columns
match the database tables. Swapping the research dataset for a licensed feed means writing
a new adapter; the validator, loader, models and API stay the same.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

import polars as pl

from polypicker.etl.issues import IssueLog

# Canonical frame contracts: column -> polars dtype. Validated before loading.
TEAM_COLUMNS = {
    "team_id": pl.Int64, "abbreviation": pl.String, "city": pl.String, "name": pl.String,
    "full_name": pl.String, "conference": pl.String, "division": pl.String, "is_active": pl.Boolean,
}
PLAYER_COLUMNS = {
    "player_id": pl.Int64, "first_name": pl.String, "last_name": pl.String, "full_name": pl.String,
    "birth_date": pl.Date, "height_inches": pl.Int64, "weight_lbs": pl.Int64, "position": pl.String,
    "jersey": pl.String, "country": pl.String, "school": pl.String, "draft_year": pl.Int64,
    "draft_round": pl.Int64, "draft_number": pl.Int64,
}
GAME_COLUMNS = {
    "game_id": pl.Int64, "season": pl.Int64, "season_type": pl.String, "is_cup": pl.Boolean,
    "game_label": pl.String, "game_sub_label": pl.String, "series_game_number": pl.Int64,
    "tipoff_utc": pl.Datetime("us", "UTC"), "game_date_et": pl.Date,
    "home_team_id": pl.Int64, "away_team_id": pl.Int64, "status": pl.String,
    "home_score": pl.Int64, "away_score": pl.Int64, "winner_team_id": pl.Int64, "periods": pl.Int64,
    "arena_name": pl.String, "arena_city": pl.String, "arena_state": pl.String, "attendance": pl.Int64,
    "has_player_stats": pl.Boolean,
}
_COUNTS = ["fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb", "ast", "stl", "blk",
           "tov", "pf"]
TEAM_GAME_COLUMNS = {
    "game_id": pl.Int64, "team_id": pl.Int64, "opponent_team_id": pl.Int64, "is_home": pl.Boolean,
    "win": pl.Boolean, "points": pl.Int64, "minutes": pl.Float64,
    **{c: pl.Int64 for c in _COUNTS}, "plus_minus": pl.Int64,
    "q1_points": pl.Int64, "q2_points": pl.Int64, "q3_points": pl.Int64, "q4_points": pl.Int64,
    "ot_points": pl.Int64,
}
PLAYER_GAME_COLUMNS = {
    "game_id": pl.Int64, "player_id": pl.Int64, "team_id": pl.Int64, "opponent_team_id": pl.Int64,
    "is_home": pl.Boolean, "participation": pl.String, "status_comment": pl.String,
    "starter": pl.Boolean, "starting_position": pl.String, "minutes": pl.Float64,
    "points": pl.Int64, "assists": pl.Int64, "rebounds": pl.Int64, "oreb": pl.Int64, "dreb": pl.Int64,
    "fgm": pl.Int64, "fga": pl.Int64, "fg3m": pl.Int64, "fg3a": pl.Int64, "ftm": pl.Int64,
    "fta": pl.Int64, "steals": pl.Int64, "blocks": pl.Int64, "turnovers": pl.Int64, "fouls": pl.Int64,
    "plus_minus": pl.Int64,
}


@dataclass
class CanonicalData:
    teams: pl.DataFrame
    players: pl.DataFrame
    games: pl.DataFrame
    team_game_stats: pl.DataFrame
    player_game_stats: pl.DataFrame


class SourceAdapter(ABC):
    code: str  # must match data_sources.code

    @abstractmethod
    def source_updated_at(self) -> datetime | None:
        """Last time the provider's data changed (FR-10.4)."""

    @abstractmethod
    def extract(self, first_season: int, issues: IssueLog) -> CanonicalData:
        """Read raw provider data and return canonical frames, logging any fixes to ``issues``."""

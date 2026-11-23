from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://polypicker:polypicker_dev@localhost:5433/polypicker"
    api_database_url: str = "postgresql+psycopg://polypicker_api:polypicker_api_dev@localhost:5433/polypicker"
    data_dir: Path = BACKEND_DIR.parent / "data"
    artifacts_dir: Path = BACKEND_DIR / "artifacts"
    first_season: int = 2023
    nfl_first_season: int = 2018  # NFL teams play 17 games a season, so models need several seasons
    soccer_first_season: int = 2010  # Premier League history from 2010-11 (first season only seeds ratings)
    football_data_api_key: str | None = None  # football-data.org free key (optional backup source)
    cors_origins: str = "http://localhost:3000"

    def resolved_data_dir(self) -> Path:
        p = self.data_dir
        return p if p.is_absolute() else (BACKEND_DIR / p).resolve()

    def resolved_artifacts_dir(self) -> Path:
        p = self.artifacts_dir
        return p if p.is_absolute() else (BACKEND_DIR / p).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()


ATTRIBUTION = (
    "Historical NBA data derived from the Eoin Moore 'Historical NBA Data and Player Box Scores' "
    "dataset (NBA.com-sourced); recent results from NBA.com's live data feed. PolyPicker is not "
    "affiliated with the NBA."
)
DISCLAIMER = (
    "PolyPicker predictions are statistical estimates, not guaranteed outcomes or financial advice. "
    "Injuries, lineup changes and random events can invalidate forecasts. PolyPicker does not accept "
    "or facilitate wagers."
)

"""Create / reset the schema plus DB-level guarantees that the ORM can't express."""

from sqlalchemy import Engine, text

from polypicker.db.models import Base
import polypicker.nfl.models  # noqa: F401  (registers the NFL tables on Base.metadata)
import polypicker.soccer.models  # noqa: F401  (soccer tables)
import polypicker.weather  # noqa: F401  (weather tables)
import polypicker.alerts  # noqa: F401  (job_alerts)

# Snapshots are never updated. The only delete allowed is storage compaction of superseded revisions of finished
# games (polypicker/compact.py), which opts in per transaction with SET LOCAL polypicker.compacting = 'on'.
_IMMUTABLE_FN = """
CREATE OR REPLACE FUNCTION polypicker_reject_change() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' AND current_setting('polypicker.compacting', true) = 'on' THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'predictions are immutable snapshots (FR-09.6); write a new revision instead';
END;
$$ LANGUAGE plpgsql;
"""
_IMMUTABLE_TRIGGER = """
DROP TRIGGER IF EXISTS predictions_immutable ON predictions;
CREATE TRIGGER predictions_immutable BEFORE UPDATE OR DELETE ON predictions
  FOR EACH ROW EXECUTE FUNCTION polypicker_reject_change();
DROP TRIGGER IF EXISTS nfl_predictions_immutable ON nfl_predictions;
CREATE TRIGGER nfl_predictions_immutable BEFORE UPDATE OR DELETE ON nfl_predictions
  FOR EACH ROW EXECUTE FUNCTION polypicker_reject_change();
DROP TRIGGER IF EXISTS soccer_predictions_immutable ON soccer_predictions;
CREATE TRIGGER soccer_predictions_immutable BEFORE UPDATE OR DELETE ON soccer_predictions
  FOR EACH ROW EXECUTE FUNCTION polypicker_reject_change();
"""

_SEED_SOURCES = """
INSERT INTO data_sources (code, name, url, license_note, approved_for) VALUES
 ('kaggle_eoin_moore', 'Historical NBA Data and Player Box Scores (Eoin Moore, Kaggle)',
  'https://www.kaggle.com/datasets/eoinamoore/historical-nba-data-and-player-box-scores',
  'NBA.com-derived. Research/prototyping only until licensing review (spec §6.4).', 'research'),
 ('nba_live', 'NBA.com live data feed (cdn.nba.com, via nba_api)',
  'https://github.com/swar/nba_api',
  'Unofficial access to NBA.com data; subject to the NBA.com Terms of Use. Research/prototyping only.', 'research'),
 ('nflverse', 'nflverse NFL data (via nflreadpy)', 'https://nflverse.nflverse.com',
  'Most nflverse datasets are CC BY 4.0 (attribution required); check each dataset''s license before commercial use.',
  'research'),
 ('espn_nfl', 'ESPN NFL scoreboard (site.api.espn.com)', 'https://site.api.espn.com/apis/site/v2/sports/football/nfl',
  'Unofficial, undocumented public endpoints; no usage rights granted. Research/prototyping only.', 'research'),
 ('espn_nba', 'ESPN NBA injury report (site.api.espn.com)', 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries',
  'Unofficial, undocumented public endpoints; no usage rights granted. Research/prototyping only.', 'research'),
 ('espn_nba_odds', 'ESPN NBA betting lines (sports.core.api.espn.com odds)', 'https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba',
  'Unofficial, undocumented public endpoints; no usage rights granted. Lines are third-party sportsbook data. Research/prototyping only.', 'research'),
 ('polymarket', 'Polymarket Gamma API (markets) and CLOB price history', 'https://docs.polymarket.com',
  'Public read-only APIs, no key required; prices are crowd-traded probabilities. Attribute Polymarket when displayed.', 'research'),
 ('nba_injury_report', 'Official NBA injury report (league PDF)', 'https://official.nba.com/nba-injury-report-2025-26-season/',
  'Published by the NBA for public use; check the NBA.com Terms of Use before commercial redistribution.', 'research'),
 ('odds_api', 'The Odds API (Pinnacle and US sportsbook lines)', 'https://the-odds-api.com',
  'Paid/free-tier API with key; lines are third-party sportsbook data. Check plan terms before displaying.', 'research'),
 ('kalshi', 'Kalshi trade API (NBA game markets and candlestick history)', 'https://docs.kalshi.com',
  'Public read-only market data, no key required; prices are exchange-traded probabilities. Attribute Kalshi when displayed.', 'research'),
 ('espn_soccer', 'ESPN soccer scoreboard and match summaries (site.api.espn.com)',
  'https://site.api.espn.com/apis/site/v2/sports/soccer',
  'Unofficial, undocumented public endpoints; no usage rights granted. Research/prototyping only.', 'research'),
 ('espn_soccer_odds', 'ESPN soccer betting lines (sports.core.api.espn.com odds)',
  'https://sports.core.api.espn.com/v2/sports/soccer',
  'Unofficial, undocumented public endpoints; no usage rights granted. Lines are third-party sportsbook data. Research/prototyping only.', 'research'),
 ('openfootball', 'openfootball football.json (public domain)', 'https://github.com/openfootball/football.json',
  'CC0 1.0 (public domain): no restrictions.', 'commercial'),
 ('fotmob', 'FotMob match data (www.fotmob.com/api/data, Opta-derived xG)', 'https://www.fotmob.com',
  'Unofficial, undocumented endpoints; no usage rights granted. Research use only.', 'research'),
 ('fdcuk', 'football-data.co.uk results, match stats and odds (CSV)', 'https://www.football-data.co.uk',
  'Site terms restrict commercial use; owner accepted for this project. Research use.', 'research'),
 ('fpl', 'Fantasy Premier League API + vaastav/Fantasy-Premier-League archive', 'https://fantasy.premierleague.com/api',
  'Unofficial public FPL endpoints; archive is a community GitHub dataset. Research use.', 'research'),
 ('nflverse_extras', 'nflverse depth charts, Next Gen Stats, FTN charting, officials (via nflreadpy)', 'https://nflreadr.nflverse.com',
  'NGS data is NFL-owned; FTN charting is CC BY-SA 4.0 (attribute FTN Data). Research use.', 'research'),
 ('open_meteo', 'Open-Meteo forecast + historical weather (ERA5) and geocoding', 'https://open-meteo.com',
  'Free for non-commercial use, CC BY 4.0 data (attribute Open-Meteo).', 'research'),
 ('sportsdataverse', 'SportsDataverse NBA possessions (stats.nba.com-derived, GitHub releases)',
  'https://github.com/sportsdataverse/sportsdataverse-data', 'Community dataset derived from stats.nba.com; NBA.com terms apply. Research use.', 'research'),
 ('clubelo', 'Club Elo ratings (api.clubelo.com)', 'http://clubelo.com',
  'Free public API; attribute clubelo.com. Research use.', 'research'),
 ('football_data_org', 'football-data.org API v4', 'https://www.football-data.org',
  'Free tier with API key; attribution required ("Football data provided by the Football-Data.org API").', 'research'),
 ('espn_nfl_odds', 'ESPN NFL betting lines, opening + closing (sports.core.api.espn.com odds)',
  'https://sports.core.api.espn.com/v2/sports/football/leagues/nfl', 'Unofficial public endpoint. Research use.', 'research')
ON CONFLICT (code) DO NOTHING;
"""


# create_all() doesn't alter existing tables: columns added after a table was first created go here.
_SOCCER_COLUMNS = """
ALTER TABLE soccer_games ALTER COLUMN competition TYPE varchar(32);
ALTER TABLE soccer_teams ALTER COLUMN abbreviation TYPE varchar(16);
ALTER TABLE soccer_games ADD COLUMN IF NOT EXISTS stage varchar(64);
ALTER TABLE soccer_games ADD COLUMN IF NOT EXISTS leg smallint;
ALTER TABLE soccer_games ADD COLUMN IF NOT EXISTS neutral boolean;
ALTER TABLE soccer_games ADD COLUMN IF NOT EXISTS decided varchar(4);
ALTER TABLE soccer_games ADD COLUMN IF NOT EXISTS home_score_90 smallint;
ALTER TABLE soccer_games ADD COLUMN IF NOT EXISTS away_score_90 smallint;
ALTER TABLE soccer_games ADD COLUMN IF NOT EXISTS home_shootout smallint;
ALTER TABLE soccer_games ADD COLUMN IF NOT EXISTS away_shootout smallint;
ALTER TABLE soccer_games ADD COLUMN IF NOT EXISTS note varchar(200);
ALTER TABLE models ADD COLUMN IF NOT EXISTS artifact_blob bytea;
ALTER TABLE nfl_games ADD COLUMN IF NOT EXISTS market_home_ml integer;
ALTER TABLE nfl_games ADD COLUMN IF NOT EXISTS market_away_ml integer;
"""


def create_all(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(_IMMUTABLE_FN))
        conn.execute(text(_IMMUTABLE_TRIGGER))
        conn.execute(text(_SEED_SOURCES))
        conn.execute(text(_SOCCER_COLUMNS))
        from polypicker.archive import DDL as ARCHIVE_DDL
        conn.execute(text(ARCHIVE_DDL))
        # Tables may predate the default-privileges grant; make sure the API role can read them.
        conn.execute(text("""
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'polypicker_api') THEN
                GRANT SELECT ON ALL TABLES IN SCHEMA public TO polypicker_api;
              END IF;
            END $$;"""))


def drop_all(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER IF EXISTS predictions_immutable ON predictions"))
        conn.execute(text("DROP TRIGGER IF EXISTS nfl_predictions_immutable ON nfl_predictions"))
        conn.execute(text("DROP TRIGGER IF EXISTS soccer_predictions_immutable ON soccer_predictions"))
        conn.execute(text("DROP TABLE IF EXISTS archived_prediction_stats"))
    Base.metadata.drop_all(engine)

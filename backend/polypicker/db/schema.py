"""Create / reset the schema plus DB-level guarantees that the ORM can't express."""

from sqlalchemy import Engine, text

from polypicker.db.models import Base

_IMMUTABLE_FN = """
CREATE OR REPLACE FUNCTION polypicker_reject_change() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'predictions are immutable snapshots (FR-09.6); write a new revision instead';
END;
$$ LANGUAGE plpgsql;
"""
_IMMUTABLE_TRIGGER = """
DROP TRIGGER IF EXISTS predictions_immutable ON predictions;
CREATE TRIGGER predictions_immutable BEFORE UPDATE OR DELETE ON predictions
  FOR EACH ROW EXECUTE FUNCTION polypicker_reject_change();
"""

_SEED_SOURCES = """
INSERT INTO data_sources (code, name, url, license_note, approved_for) VALUES
 ('kaggle_eoin_moore', 'Historical NBA Data and Player Box Scores (Eoin Moore, Kaggle)',
  'https://www.kaggle.com/datasets/eoinamoore/historical-nba-data-and-player-box-scores',
  'NBA.com-derived. Research/prototyping only until licensing review (spec §6.4).', 'research')
ON CONFLICT (code) DO NOTHING;
"""


def create_all(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(_IMMUTABLE_FN))
        conn.execute(text(_IMMUTABLE_TRIGGER))
        conn.execute(text(_SEED_SOURCES))
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
    Base.metadata.drop_all(engine)

"""Soccer competitions PolyPicker covers. One entry per competition; every source, model and API route reads from here.

* ``competition``: ESPN code, stored on every ``soccer_games`` row (``eng.1``, ``uefa.champions``).
* ``kind``: ``league`` (round robin, one table) or ``cup`` (league phase / groups, then knockout ties that can go
  to extra time and penalties).
* ``calendar_year``: seasons run Jan-Dec (South America) instead of Aug-May; ``season`` is then the calendar year.
* ``openfootball`` / ``football_data``: cross-check sources, None where the competition isn't covered (free tier).
* ``target``: model registry target, so each competition trains and activates its own model.
* ``feature_pool``: competitions whose matches feed team ratings. Cups pool the domestic leagues we cover so a
  club's form and Elo carry over between its league and continental games.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

EUROPE = ("eng.1", "esp.1", "ger.1", "ita.1", "fra.1", "uefa.champions", "uefa.europa")
SOUTH_AMERICA = ("ecu.1", "conmebol.libertadores", "conmebol.sudamericana")


@dataclass(frozen=True)
class League:
    slug: str            # URL slug: /soccer/<slug>, /api/v1/soccer/<slug>
    competition: str
    name: str
    short: str
    country: str
    target: str
    first_season: int    # first season ingested (it only seeds ratings; models validate on later seasons)
    kind: str = "league"
    calendar_year: bool = False
    openfootball: str | None = None
    football_data: str | None = None
    feature_pool: tuple[str, ...] = ()

    @property
    def pool(self) -> tuple[str, ...]:
        return self.feature_pool or (self.competition,)

    def current_season(self, today: date | None = None) -> int:
        t = today or datetime.now(timezone.utc).date()
        return t.year if self.calendar_year or t.month >= 7 else t.year - 1

    def season_label(self, season: int) -> str:
        return str(season) if self.calendar_year else f"{season}-{str(season + 1)[2:]}"


LEAGUES: dict[str, League] = {lg.slug: lg for lg in [
    League("epl", "eng.1", "Premier League", "EPL", "England", "soccer_eng1_match", 2010, openfootball="en.1", football_data="PL"),
    League("laliga", "esp.1", "LaLiga", "LaLiga", "Spain", "soccer_esp1_match", 2010, openfootball="es.1", football_data="PD"),
    League("bundesliga", "ger.1", "Bundesliga", "BUN", "Germany", "soccer_ger1_match", 2010, openfootball="de.1",
           football_data="BL1"),
    League("seriea", "ita.1", "Serie A", "SA", "Italy", "soccer_ita1_match", 2010, openfootball="it.1", football_data="SA"),
    League("ligue1", "fra.1", "Ligue 1", "L1", "France", "soccer_fra1_match", 2010, openfootball="fr.1", football_data="FL1"),
    League("ucl", "uefa.champions", "Champions League", "UCL", "Europe", "soccer_ucl_match", 2010, kind="cup",
           football_data="CL", feature_pool=EUROPE),
    League("uel", "uefa.europa", "Europa League", "UEL", "Europe", "soccer_uel_match", 2010, kind="cup", feature_pool=EUROPE),
    League("libertadores", "conmebol.libertadores", "Copa Libertadores", "LIB", "South America", "soccer_lib_match", 2012,
           kind="cup", calendar_year=True, feature_pool=SOUTH_AMERICA),
    League("sudamericana", "conmebol.sudamericana", "Copa Sudamericana", "SUD", "South America", "soccer_sud_match", 2012,
           kind="cup", calendar_year=True, feature_pool=SOUTH_AMERICA),
    League("ecuador", "ecu.1", "LigaPro Serie A", "ECU", "Ecuador", "soccer_ecu1_match", 2012, calendar_year=True),
]}
BY_COMPETITION: dict[str, League] = {lg.competition: lg for lg in LEAGUES.values()}


def get_league(slug: str) -> League:
    try:
        return LEAGUES[slug]
    except KeyError:
        raise ValueError(f"unknown soccer league {slug!r} (known: {', '.join(LEAGUES)})") from None

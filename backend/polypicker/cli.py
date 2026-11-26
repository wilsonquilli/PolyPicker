"""Operator CLI. All writes happen here, never through the public API (FR-11.4)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import typer

from polypicker.config import get_settings
from polypicker.db.session import owner_engine

app = typer.Typer(help="PolyPicker backend operations", no_args_is_help=True)
db_app = typer.Typer(help="Database schema management", no_args_is_help=True)
app.add_typer(db_app, name="db")
nfl_app = typer.Typer(help="NFL: nflverse data, ESPN live scores, models and forecasts", no_args_is_help=True)
app.add_typer(nfl_app, name="nfl")
soccer_app = typer.Typer(help="Soccer (Premier League, LaLiga): ESPN, openfootball, football-data.org, models, forecasts",
                         no_args_is_help=True)
app.add_typer(soccer_app, name="soccer")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _audit(action: str, detail: dict) -> None:
    from sqlalchemy import insert
    from polypicker.db import models as m
    with owner_engine().begin() as conn:
        conn.execute(insert(m.AdminAuditEvent).values(actor="cli", action=action, detail=detail))


def _dump(obj) -> None:
    typer.echo(json.dumps(obj, indent=2, default=str))


@db_app.command("store-models")
def db_store_models(all_models: bool = typer.Option(False, "--all", help="Every model with a file, not just active ones")) -> None:
    """Copy model files into the database (models.artifact_blob) so servers without the artifacts folder can load them."""
    from pathlib import Path

    from sqlalchemy import text
    eng = owner_engine()
    art = get_settings().resolved_artifacts_dir()
    with eng.begin() as conn:
        rows = conn.execute(text(f"SELECT id, artifact_path FROM models WHERE artifact_blob IS NULL "
                                 f"{'' if all_models else 'AND is_active'}")).all()
        stored, missing = 0, []
        for mid, path in rows:
            f = Path(path) if path else None
            if f is not None and not f.exists():
                f = art / f.name
            if f is None or not f.exists():
                missing.append(mid)
                continue
            conn.execute(text("UPDATE models SET artifact_blob = :b WHERE id = :i"), {"b": f.read_bytes(), "i": mid})
            stored += 1
    _dump({"stored": stored, "missing_files": missing})


@db_app.command("init")
def db_init() -> None:
    """Create tables, triggers and seed data (safe to re-run)."""
    from polypicker.db.schema import create_all
    create_all(owner_engine())
    typer.echo("schema ready")


@db_app.command("reset")
def db_reset(yes: bool = typer.Option(False, "--yes", help="Confirm dropping ALL data")) -> None:
    """Drop and recreate every table (development only)."""
    if not yes:
        raise typer.BadParameter("refusing to drop data without --yes")
    from polypicker.db.schema import create_all, drop_all
    drop_all(owner_engine())
    create_all(owner_engine())
    typer.echo("schema reset")


@app.command()
def ingest(retries: int = 3, first_season: int | None = None,
           only_if_changed: bool = typer.Option(False, help="Skip if the files haven't changed since the last import")) -> None:
    """Import / re-sync games, box scores, teams and players from the source files (FR-10.1)."""
    from polypicker.etl.runner import last_success_source_updated_at, run_ingestion
    from polypicker.etl.sources.kaggle_csv import KaggleCsvSource
    s = get_settings()
    src = KaggleCsvSource(s.resolved_data_dir())
    if only_if_changed:
        files, last = src.source_updated_at(), last_success_source_updated_at(owner_engine(), src.code)
        if files is None or (last is not None and files <= last):
            typer.echo("data files unchanged since the last import; skipping")
            return
    result = run_ingestion(owner_engine(), src, first_season or s.first_season, retries=retries)
    _dump(result)


@app.command()
def sync(lookback_days: int = typer.Option(14, help="Look for unfinished games that tipped off within N days"),
         retries: int = 2) -> None:
    """Fetch results + box scores for games that have tipped off, from NBA.com's live feed (nba_api)."""
    from polypicker.etl.runner import run_ingestion
    from polypicker.etl.sources.nba_live import NbaLiveSource
    s = get_settings()
    result = run_ingestion(owner_engine(), NbaLiveSource(owner_engine(), lookback_days=lookback_days),
                           s.first_season, retries=retries, backoff_seconds=20)
    _dump(result)


@app.command()
def injuries(retries: int = 2) -> None:
    """Fetch today's NBA injury report from ESPN (feeds player availability into forecasts)."""
    from polypicker.etl.sources.espn_injuries import sync_injuries
    _dump(sync_injuries(owner_engine(), retries=retries))


@app.command("official-injuries")
def official_injuries(backfill_from_season: int | None = typer.Option(None, help="One pregame report per game day since this season"),
                      retries: int = 2) -> None:
    """Fetch the newest official NBA injury report (league PDF); it overrides ESPN for the games it covers."""
    from polypicker.etl.sources.nba_injury_pdf import backfill_official_injuries, sync_official_injuries
    if backfill_from_season is not None:
        _dump(backfill_official_injuries(owner_engine(), backfill_from_season))
    else:
        _dump(sync_official_injuries(owner_engine(), retries=retries))


@app.command()
def odds(backfill_from_season: int | None = typer.Option(None, help="Fill closing lines for every game since this season"),
         force: bool = typer.Option(False, help="With --backfill-from-season: re-fetch games that already have lines"),
         retries: int = 2) -> None:
    """Fetch NBA betting lines from ESPN (yesterday..+2 days by default) for the market benchmark."""
    from polypicker.etl.sources.espn_odds import sync_odds
    _dump(sync_odds(owner_engine(), backfill_from_season=backfill_from_season, force=force, retries=retries))


@app.command()
def polymarket(backfill_since: str | None = typer.Option(None, help="ISO date: load every closed NBA game market since then"),
               retries: int = 2) -> None:
    """Fetch Polymarket NBA moneyline prices (open markets + pregame prices of finished games)."""
    from datetime import datetime, timezone
    from polypicker.etl.sources.polymarket import sync_polymarket
    since = datetime.fromisoformat(backfill_since).replace(tzinfo=timezone.utc) if backfill_since else None
    _dump(sync_polymarket(owner_engine(), backfill_since=since, retries=retries))


@app.command("odds-api")
def odds_api(force: bool = typer.Option(False, help="Ignore the 45-minute throttle / credit reserve"),
             retries: int = 2) -> None:
    """Fetch Pinnacle + US book moneylines from The Odds API (needs ODDS_API_KEY; throttled to save credits)."""
    from polypicker.etl.sources.odds_api import sync_odds_api
    _dump(sync_odds_api(owner_engine(), force=force, retries=retries))


@app.command()
def kalshi(backfill: bool = typer.Option(False, help="Load every archived NBA game market (since April 2025)"),
           retries: int = 2) -> None:
    """Fetch Kalshi NBA game-winner prices (open markets + pregame prices of finished games)."""
    from polypicker.etl.sources.kalshi import sync_kalshi
    _dump(sync_kalshi(owner_engine(), backfill=backfill, retries=retries))


@app.command("value")
def value_cmd() -> None:
    """Flag value bets (winners + props) for upcoming games from live forecasts and current prices."""
    from polypicker.ml.value import find_value
    _dump(find_value(owner_engine()))


@app.command("value-grade")
def value_grade() -> None:
    """Settle value picks for finished games and measure closing-line value."""
    from polypicker.ml.value import grade_value
    _dump(grade_value(owner_engine()))


@app.command("value-backtest")
def value_backtest(season: int = typer.Option(..., help="Season start year, e.g. 2026"),
                   market: str = typer.Option("both", help="winners | props | both")) -> None:
    """Honest value backtest: forecasts from opening lines / point-in-time props, priced at opening odds."""
    from sqlalchemy import insert
    from polypicker.db import models as m
    from polypicker.ml.value import backtest_props, backtest_winners
    eng = owner_engine()
    out = {}
    for mk, fn in (("winners", backtest_winners), ("props", backtest_props)):
        if market in (mk, "both"):
            rep = fn(eng, season)
            with eng.begin() as conn:
                conn.execute(insert(m.ValueBacktest).values(market=mk, season=season, report=rep))
            out[mk] = rep
    _dump(out)


@app.command()
def train(validation_season: int = typer.Option(2025, help="Season (start year) held out for validation"),
          activate: bool = typer.Option(True, help="Make the new models the ones used for live forecasts")) -> None:
    """Train game + player models chronologically and register them (spec §9.3-9.4)."""
    from polypicker.ml.pipeline import build_features, train_models
    eng = owner_engine()
    ids = train_models(eng, build_features(eng), validation_season, activate=activate)
    _audit("train", {"validation_season": validation_season, "model_ids": ids, "activated": activate})
    _dump(ids)


@app.command()
def predict(days: int = typer.Option(7, help="Forecast scheduled games tipping off within N days")) -> None:
    """Publish pregame forecasts for upcoming games (FR-02, FR-03, FR-06.2)."""
    from polypicker.ml.pipeline import predict_upcoming
    _dump(predict_upcoming(owner_engine(), days=days))


@app.command()
def grade() -> None:
    """Grade predictions whose games have finished (FR-09)."""
    from polypicker.ml.grade import grade_all
    _dump(grade_all(owner_engine()))


@app.command()
def backtest(season: int = typer.Option(..., help="Season start year to backtest, e.g. 2026")) -> None:
    """Point-in-time forecasts for a season's completed games using only earlier seasons (kind='backtest')."""
    from polypicker.ml.grade import grade_all
    from polypicker.ml.pipeline import backtest_season
    result = backtest_season(owner_engine(), season)
    result["grading"] = grade_all(owner_engine())
    _audit("backtest", {"season": season, "models": result["models"]})
    _dump(result)


@app.command("daily")
def daily(days: int = 7, retries: int = 3) -> None:
    """The scheduled job (FR-06.2/6.3): sync data, grade finished games, publish new forecasts.

    Re-imports the dataset files only if they changed, then pulls new results from the live feed.
    A live-feed outage is logged but doesn't stop grading/forecasting with the data we have."""
    ingest(retries=retries, first_season=None, only_if_changed=True)
    try:
        sync(lookback_days=14, retries=retries)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).error("live sync failed, continuing with existing data: %s", e)
    try:
        odds(backfill_from_season=None, force=False, retries=retries)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).error("odds fetch failed, continuing: %s", e)
    try:
        polymarket(backfill_since=None, retries=retries)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).error("Polymarket fetch failed, continuing: %s", e)
    try:
        kalshi(backfill=False, retries=retries)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).error("Kalshi fetch failed, continuing: %s", e)
    _step("odds api", odds_api, force=False, retries=retries)
    try:
        injuries(retries=retries)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).error("injury report failed, using box-score availability: %s", e)
    _step("official injuries", official_injuries, backfill_from_season=None, retries=retries)
    grade()
    _step("value grade", value_grade)
    predict(days=days)
    _step("value", value_cmd)


# ---------------------------------------------------------------- NFL

def _nfl_season() -> int:
    now = datetime.now(timezone.utc)
    return now.year if now.month >= 8 else now.year - 1


def _last_success(job: str, params_contains: str) -> datetime | None:
    """Finish time of the latest successful ingestion run of ``job`` whose params contain the given JSON."""
    from sqlalchemy import text
    with owner_engine().connect() as c:
        return c.execute(text("""SELECT max(finished_at) FROM ingestion_runs WHERE job = :j AND status = 'succeeded'
                                 AND params @> CAST(:p AS jsonb)"""), {"j": job, "p": params_contains}).scalar()


@nfl_app.command("ingest")
def nfl_ingest(retries: int = 3, first_season: int | None = None) -> None:
    """Import / re-sync NFL schedules, box scores, snaps, rosters and injuries from nflverse."""
    from polypicker.nfl.etl.nflverse import NflverseSource
    from polypicker.nfl.etl.runner import ingest_nflverse
    s = get_settings()
    _dump(ingest_nflverse(owner_engine(), NflverseSource(first_season or s.nfl_first_season), retries=retries))


@nfl_app.command("sync")
def nfl_sync(lookback_days: int = 21, retries: int = 2,
             backfill_quarters: bool = typer.Option(False, help="Also fill quarter scores for final games missing them")) -> None:
    """Live status, final scores and quarter scores from ESPN for games that have kicked off."""
    from polypicker.nfl.etl.espn import sync_espn
    _dump(sync_espn(owner_engine(), lookback_days=lookback_days, retries=retries, backfill_quarters=backfill_quarters))


@nfl_app.command("markets")
def nfl_markets(backfill: bool = typer.Option(False, help="Load every archived game market (Polymarket 2024+, Kalshi 2025+)"),
                retries: int = 2) -> None:
    """Kalshi + Polymarket NFL win prices (open markets + pregame prices of finished games)."""
    from polypicker.nfl.etl.markets import sync_nfl_kalshi, sync_nfl_polymarket
    _dump({"kalshi": sync_nfl_kalshi(owner_engine(), backfill=backfill, retries=retries),
           "polymarket": sync_nfl_polymarket(owner_engine(), backfill=backfill, retries=retries)})


@nfl_app.command("odds-api")
def nfl_odds_api(force: bool = typer.Option(False, help="Ignore the 45-minute throttle / credit reserve"),
                 retries: int = 2) -> None:
    """Pinnacle + US book NFL moneylines from The Odds API (needs ODDS_API_KEY)."""
    from polypicker.nfl.etl.markets import sync_nfl_odds_api
    _dump(sync_nfl_odds_api(owner_engine(), force=force, retries=retries))


@nfl_app.command("train")
def nfl_train(validation_season: int = typer.Option(2025, help="Season held out for validation"),
              activate: bool = typer.Option(True, help="Use the new models for live forecasts")) -> None:
    """Train the NFL game + player prop models chronologically and register them."""
    from polypicker.nfl.ml.pipeline import build_features, train_models
    eng = owner_engine()
    ids = train_models(eng, build_features(eng), validation_season, activate=activate)
    _audit("nfl_train", {"validation_season": validation_season, "model_ids": ids, "activated": activate})
    _dump(ids)


@nfl_app.command("espn-odds")
def nfl_espn_odds(backfill_from_season: int | None = typer.Option(None, help="Every final game from this season on (2023+)"),
                  retries: int = 2) -> None:
    """Opening + closing moneylines from ESPN (one book per game), for the line-movement record."""
    from polypicker.nfl.etl.espn_odds import sync_nfl_espn_odds
    _dump(sync_nfl_espn_odds(owner_engine(), backfill_from_season=backfill_from_season, retries=retries))


@nfl_app.command("predict")
def nfl_predict(weeks: int = typer.Option(2, help="Forecast every scheduled game in the next N NFL weeks")) -> None:
    """Publish pregame NFL forecasts (winner, score, spread, total, player props)."""
    from polypicker.nfl.ml.pipeline import predict_upcoming
    _dump(predict_upcoming(owner_engine(), weeks=weeks))


@nfl_app.command("grade")
def nfl_grade() -> None:
    """Grade NFL forecasts whose games have finished."""
    from polypicker.nfl.ml.grade import grade_all
    _dump(grade_all(owner_engine()))


@nfl_app.command("backtest")
def nfl_backtest(season: int = typer.Option(..., help="Season to backtest, e.g. 2026")) -> None:
    """Point-in-time forecasts for a season's completed games using only earlier seasons (kind='backtest')."""
    from polypicker.nfl.ml.grade import grade_all
    from polypicker.nfl.ml.pipeline import backtest_season
    result = backtest_season(owner_engine(), season)
    result["grading"] = grade_all(owner_engine())
    _audit("nfl_backtest", {"season": season, "models": result["models"]})
    _dump(result)


@nfl_app.command("extras")
def nfl_extras(first_season: int | None = typer.Option(None, help="Backfill from this season (default: current season only)")) -> None:
    """Depth charts, Next Gen Stats, FTN charting (per team-game) and game officials from nflverse."""
    from sqlalchemy import text
    from polypicker.nfl.etl.extras import sync_nfl_extras
    eng = owner_engine()
    with eng.connect() as c:
        cur = c.execute(text("SELECT max(season) FROM nfl_games")).scalar()
    _dump(sync_nfl_extras(eng, list(range(first_season or cur, cur + 1))))


@nfl_app.command("daily")
def nfl_daily(weeks: int = 2, retries: int = 3,
              full: bool | None = typer.Option(None, "--full/--quick", help="Re-import every season from 2018 "
                                               "(default: only when the last full import is over a week old)")) -> None:
    """Scheduled NFL job: nflverse import, ESPN scores, grade, publish forecasts. An ESPN outage is
    logged and the job continues with the data it has. Finished seasons never change, so the import re-reads
    only the current season except for a weekly full pass."""
    s = get_settings()
    if full is None:
        last = _last_success("nfl_ingest", f'{{"seasons": [{s.nfl_first_season}]}}')
        full = last is None or datetime.now(timezone.utc) - last > timedelta(days=7)
    nfl_ingest(retries=retries, first_season=None if full else _nfl_season())
    try:
        nfl_sync(lookback_days=21, retries=2, backfill_quarters=True)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).error("ESPN sync failed, continuing: %s", e)
    _step("nfl markets", nfl_markets, backfill=False, retries=2)
    _step("nfl odds api", nfl_odds_api, force=False, retries=2)
    _step("nfl espn odds", nfl_espn_odds, backfill_from_season=None, retries=2)
    nfl_grade()
    nfl_predict(weeks=weeks)


# ---------------------------------------------------------------- Soccer

LEAGUE_OPT = typer.Option("epl", "--league", "-l", help="League slug: epl, laliga, bundesliga, seriea, ligue1, ucl, uel, "
                                                           "libertadores, sudamericana, ecuador")


def _lg(slug: str):
    from polypicker.soccer.leagues import get_league
    try:
        return get_league(slug)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from None


@soccer_app.command("ingest")
def soccer_ingest(league: str = LEAGUE_OPT, first_season: int | None = None, last_season: int | None = None,
                  retries: int = 2) -> None:
    """Backfill / re-sync whole seasons from ESPN (resumable; ~1 h per league for 2010-11 onward)."""
    from polypicker.soccer.etl.espn import ingest_espn
    lg = _lg(league)
    first, last = first_season or lg.first_season, last_season or lg.current_season()
    _dump(ingest_espn(owner_engine(), list(range(first, last + 1)), lg.competition, retries=retries))


@soccer_app.command("sync")
def soccer_sync(league: str = LEAGUE_OPT, back_days: int = 10, ahead_days: int = 60,
                full: bool | None = typer.Option(None, "--full/--quick", help="Walk every match date in the window "
                                                 "(default: when the last full sync is over 20 h old)")) -> None:
    """Recent results (+ team stats) and upcoming fixtures from ESPN."""
    from polypicker.soccer.etl.espn import sync_espn
    _dump(sync_espn(owner_engine(), _lg(league).competition, back_days=back_days, ahead_days=ahead_days, full=full))


@soccer_app.command("crosscheck")
def soccer_crosscheck(league: str = LEAGUE_OPT, first_season: int | None = None, last_season: int | None = None) -> None:
    """Check ESPN results against openfootball (CC0) and fill any result ESPN is missing."""
    from polypicker.soccer.etl.crosscheck import crosscheck_openfootball
    lg = _lg(league)
    first, last = first_season or lg.first_season, last_season or lg.current_season()
    _dump(crosscheck_openfootball(owner_engine(), list(range(first, last + 1)), lg))


@soccer_app.command("fd-sync")
def soccer_fd_sync(league: str = LEAGUE_OPT, season: int | None = None) -> None:
    """football-data.org backup for the current season (needs FOOTBALL_DATA_API_KEY in backend/.env)."""
    from polypicker.soccer.etl.crosscheck import sync_football_data
    lg = _lg(league)
    _dump(sync_football_data(owner_engine(), season or lg.current_season(), lg))


@soccer_app.command("odds")
def soccer_odds(league: str = LEAGUE_OPT,
                backfill_from_season: int | None = typer.Option(None, help="Every finished match from this season on without a closing line"),
                ahead_days: int = 10,
                force: bool = typer.Option(False, help="With --backfill-from-season: re-fetch matches that already have a closing line")) -> None:
    """Sportsbook 1X2 + total-goals lines from ESPN (median across books): the market model's input."""
    from polypicker.soccer.etl.odds import sync_odds
    _dump(sync_odds(owner_engine(), _lg(league), backfill_from_season=backfill_from_season, ahead_days=ahead_days,
                    force=force))


@soccer_app.command("pinnacle")
def soccer_pinnacle(league: str = LEAGUE_OPT,
                    force: bool = typer.Option(False, help="Ignore the 3-hour gap and the credit reserve")) -> None:
    """Snapshot 1X2 prices from Pinnacle / Betfair / European books (The Odds API, needs ODDS_API_KEY) to track line movement."""
    from polypicker.soccer.etl.odds_api import sync_soccer_odds_api
    _dump(sync_soccer_odds_api(owner_engine(), _lg(league), force=force))


@soccer_app.command("fotmob")
def soccer_fotmob(league: str = LEAGUE_OPT,
                  first_season: int | None = typer.Option(None, help="Backfill from this season (default: current season only)"),
                  last_season: int | None = None,
                  limit: int | None = typer.Option(None, help="Stop after this many match requests")) -> None:
    """Expected goals (xG, xGOT), big chances and box touches per finished match from FotMob (research use only)."""
    from sqlalchemy import text
    from polypicker.soccer.etl.fotmob import sync_fotmob
    lg = _lg(league)
    eng = owner_engine()
    with eng.connect() as c:
        cur = c.execute(text("SELECT max(season) FROM soccer_games WHERE competition = :c AND status = 'final'"),
                        {"c": lg.competition}).scalar()
    seasons = list(range(first_season or cur, (last_season or cur) + 1))
    _dump(sync_fotmob(eng, lg, seasons, limit=limit))


@soccer_app.command("fdcuk")
def soccer_fdcuk(league: str = LEAGUE_OPT,
                 first_season: int | None = typer.Option(None, help="Backfill from this season (default: current season only)"),
                 last_season: int | None = None) -> None:
    """football-data.co.uk: match stats (corners, cards, fouls), xG and closing odds; fills missing betting lines."""
    from sqlalchemy import text
    from polypicker.soccer.etl.fdcuk import sync_fdcuk
    lg = _lg(league)
    eng = owner_engine()
    with eng.connect() as c:
        cur = c.execute(text("SELECT max(season) FROM soccer_games WHERE competition = :c"), {"c": lg.competition}).scalar()
    _dump(sync_fdcuk(eng, lg, list(range(first_season or cur, (last_season or cur) + 1))))


@soccer_app.command("fpl")
def soccer_fpl(first_season: int | None = typer.Option(None, help="Backfill from this season (archive from 2019; default: current only)")) -> None:
    """Fantasy Premier League: player availability (chance of playing) and per-match player stats incl. xG / xA (EPL)."""
    from sqlalchemy import text
    from polypicker.soccer.etl.fpl import FIRST_ARCHIVE_SEASON, sync_fpl
    eng = owner_engine()
    with eng.connect() as c:
        cur = c.execute(text("SELECT max(season) FROM soccer_games WHERE competition = 'eng.1'")).scalar()
    start = max(first_season, FIRST_ARCHIVE_SEASON) if first_season else cur
    _dump(sync_fpl(eng, list(range(start, cur + 1)), cur))


@soccer_app.command("clubelo")
def soccer_clubelo(first_season: int = typer.Option(2010, help="First season to map clubs for")) -> None:
    """Club Elo rating history for top-five-league clubs (clubelo.com), mapped to our clubs."""
    from sqlalchemy import text
    from polypicker.soccer.etl.clubelo import sync_clubelo
    eng = owner_engine()
    with eng.connect() as c:
        cur = c.execute(text("SELECT max(season) FROM soccer_games WHERE competition = 'eng.1'")).scalar()
    _dump(sync_clubelo(eng, list(range(first_season, cur + 1))))


@soccer_app.command("train")
def soccer_train(league: str = LEAGUE_OPT,
                 validation_season: int = typer.Option(2025, help="Season (start year) held out for validation"),
                 activate: bool = True) -> None:
    """Train a league's match model (expected goals + Dixon-Coles) and register it."""
    from polypicker.soccer.ml.pipeline import build_features, train
    eng, lg = owner_engine(), _lg(league)
    mid = train(eng, build_features(eng, lg), validation_season, lg, activate=activate)
    _audit("soccer_train", {"league": lg.slug, "validation_season": validation_season, "model_id": mid, "activated": activate})
    _dump({"league": lg.slug, "model_id": mid})


@soccer_app.command("backtest")
def soccer_backtest(league: str = LEAGUE_OPT,
                    season: int = typer.Option(..., help="Season (start year) to backtest, e.g. 2026")) -> None:
    """Point-in-time forecasts for a season's completed matches using only earlier seasons (kind='backtest')."""
    from polypicker.soccer.ml.grade import grade_all
    from polypicker.soccer.ml.pipeline import backtest_season
    result = backtest_season(owner_engine(), season, _lg(league))
    result["grading"] = grade_all(owner_engine())
    _dump(result)


@soccer_app.command("predict")
def soccer_predict(league: str = LEAGUE_OPT,
                   days: int = typer.Option(10, help="Forecast scheduled matches in the next N days")) -> None:
    """Publish pre-match forecasts (home/draw/away, expected goals, totals, both teams to score)."""
    from polypicker.soccer.ml.pipeline import predict_upcoming
    _dump(predict_upcoming(owner_engine(), _lg(league), days=days))


@soccer_app.command("grade")
def soccer_grade() -> None:
    """Grade soccer forecasts (every league) whose matches have finished."""
    from polypicker.soccer.ml.grade import grade_all
    _dump(grade_all(owner_engine()))


@soccer_app.command("logos")
def soccer_logos(refresh: bool = typer.Option(False, help="Re-download every current club's logo, not just missing ones")) -> None:
    """Club logos from ESPN for every club in the current seasons (only missing ones unless --refresh)."""
    from polypicker.soccer.etl.logos import sync_logos
    _dump(sync_logos(owner_engine(), refresh=refresh))


@soccer_app.command("daily")
def soccer_daily(days: int = 10, league: str | None = typer.Option(None, "--league", "-l", help="One league (default: all)"),
                 full: bool | None = typer.Option(None, "--full/--quick", help="Full ESPN calendar walk + research feeds "
                                                  "(default: once every 20 h)")) -> None:
    """Scheduled job, per league: ESPN sync, football-data.org backup (if a key is set), betting lines, grade, publish forecasts.
    One league failing doesn't stop the others.

    Quick runs (between the first run of the day and the next one 20 h later) re-read only dates that hold an
    unfinished match and skip the research feeds (football-data.co.uk, FotMob, FPL) that no forecast reads."""
    from polypicker.soccer.etl.espn import FULL_EVERY, _last_full_sync
    from polypicker.soccer.leagues import LEAGUES
    log = logging.getLogger(__name__)
    slugs = [_lg(league).slug] if league else list(LEAGUES)
    if full is None:
        lasts = [_last_full_sync(owner_engine(), _lg(sl).competition) for sl in slugs]
        full = any(t is None or datetime.now(timezone.utc) - t > FULL_EVERY for t in lasts)
    for slug in slugs:
        try:
            soccer_sync(league=slug, back_days=10, ahead_days=60, full=full)
        except Exception as e:  # noqa: BLE001
            log.error("ESPN soccer sync (%s) failed, continuing: %s", slug, e)
        try:
            soccer_fd_sync(league=slug, season=None)
        except Exception as e:  # noqa: BLE001
            log.error("football-data.org sync (%s) failed, continuing: %s", slug, e)
        try:
            soccer_odds(league=slug, backfill_from_season=None, ahead_days=days, force=False)
        except Exception as e:  # noqa: BLE001
            log.error("ESPN soccer odds (%s) failed, continuing: %s", slug, e)
        from polypicker.soccer.etl.fdcuk import DIVS
        if full and slug in DIVS:
            try:
                soccer_fdcuk(league=slug, first_season=None, last_season=None)
            except Exception as e:  # noqa: BLE001
                log.error("football-data.co.uk (%s) failed, continuing: %s", slug, e)
        from polypicker.soccer.etl.fotmob import DAILY_LEAGUES
        if full and slug in DAILY_LEAGUES:
            try:
                soccer_fotmob(league=slug, first_season=None, last_season=None, limit=None)
            except Exception as e:  # noqa: BLE001
                log.error("FotMob xG (%s) failed, continuing: %s", slug, e)
        from polypicker.soccer.etl.odds_api import DEFAULT_LEAGUES
        if slug in DEFAULT_LEAGUES:
            try:
                soccer_pinnacle(league=slug, force=False)
            except Exception as e:  # noqa: BLE001
                log.error("Odds API soccer snapshot (%s) failed, continuing: %s", slug, e)
    if full and "epl" in slugs:
        try:
            soccer_fpl(first_season=None)
        except Exception as e:  # noqa: BLE001
            log.error("Fantasy Premier League sync failed, continuing: %s", e)
    soccer_grade()
    failed = []
    for slug in slugs:
        try:
            soccer_predict(league=slug, days=days)
        except Exception as e:  # noqa: BLE001
            if "no active" in str(e):   # a new league before its first training run
                log.warning("%s: no trained model yet, skipping forecasts", slug)
                continue
            log.error("soccer predict (%s) failed: %s", slug, e)
            failed.append(slug)
    if failed:
        raise RuntimeError(f"soccer forecasts failed for: {', '.join(failed)}")


# ---------------------------------------------------------------- scheduled jobs (launchd now, cron on a server later)

def _step(name: str, fn, *args, **kwargs) -> bool:
    """Run one step; a failure is logged and the job moves on (one source down mustn't stop the rest)."""
    log = logging.getLogger("polypicker.schedule")
    import os
    import time
    if os.environ.get("PP_JOB_RUN"):   # inside a job (also in run-all's per-sport child processes): collect errors
        from polypicker import alerts
        alerts.install(os.environ.get("PP_JOB_NAME", "job"))
    t0 = time.monotonic()
    try:
        log.info("start %s", name)
        fn(*args, **kwargs)
        log.info("done %s in %.1fs", name, time.monotonic() - t0)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("step %s failed after %.1fs: %s", name, time.monotonic() - t0, e)
        return False


def _job_start(job: str) -> str:
    import os

    from polypicker import alerts
    os.environ["PP_JOB_NAME"] = job
    return alerts.start_job(job)


def _job_end(run: str, job: str, ok: dict[str, bool], optional: frozenset[str] = frozenset()) -> None:
    """One alert per job run if anything failed (steps or logged errors); never lets alerting break the job."""
    from polypicker import alerts
    try:
        res = alerts.finish_job(run, job, [k for k, v in ok.items() if not v and k not in optional])
        if res:
            logging.getLogger("polypicker.schedule").warning("alert sent (%s): %s", ", ".join(res["sent"]) or "no channel",
                                                             res["title"])
    except Exception as e:  # noqa: BLE001
        logging.getLogger("polypicker.schedule").warning("couldn't send the failure alert: %s", e)


def _parallel(steps: dict[str, tuple]) -> dict[str, bool]:
    """Run independent ``_step``s at once, one process each (feature building / forecasting is CPU-bound, so threads
    would just queue on the GIL). Each sport writes its own tables. ``steps`` = {key: (name, fn, kwargs)}."""
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=len(steps), mp_context=mp.get_context("spawn")) as ex:
        futs = {k: ex.submit(_step, name, fn, **kw) for k, (name, fn, kw) in steps.items()}
        return {k: f.result() for k, f in futs.items()}


@app.command("possessions")
def possessions_cmd(first_season: int = typer.Option(2013, help="First season (2013 = 2013-14)"),
                    last_season: int | None = typer.Option(None, help="Last season (default: the latest published)")) -> None:
    """NBA possession-level lineup data (SportsDataverse) for possession RAPM."""
    from datetime import date
    from polypicker.etl.sources.sdv_possessions import sync_possessions
    last = last_season or (date.today().year - 1 if date.today().month >= 8 else date.today().year - 2)
    _dump(sync_possessions(owner_engine(), list(range(first_season, last + 1))))


@app.command("weather")
def weather_cmd(sport: str = typer.Option("all", help="nfl, soccer or all"),
                history: bool = typer.Option(False, help="Backfill every past game (archive), not just the next 16 days")) -> None:
    """Kickoff weather (temperature, rain, wind) for NFL and soccer games from Open-Meteo."""
    from polypicker.weather import sync_weather
    for sp in (["nfl", "soccer"] if sport == "all" else [sport]):
        _dump(sync_weather(owner_engine(), sp, history=history))


@app.command("futures")
def futures_cmd(only: str | None = typer.Option(None, help="Comma-separated: nba,nfl,epl,laliga,...,ecuador (default: all)"),
                sims: int = typer.Option(20_000, help="Simulations per competition")) -> None:
    """Championship forecasts: simulate the rest of each season (NBA title, Super Bowl, every soccer competition)."""
    from polypicker.futures import run_all as run_futures
    res = run_futures(owner_engine(), n=sims, only=only.split(",") if only else None)
    _dump(res)
    if any("error" in v for v in res.values()):
        raise typer.Exit(1)


@app.command("combos")
def combos_cmd() -> None:
    """Home-page combos: grade the ones in progress (leg by leg), then rebuild every slot from the latest forecasts."""
    from polypicker.combos import refresh as refresh_combos
    with owner_engine().begin() as conn:
        _dump(refresh_combos(conn))


def _sport_pipeline(sport: str, full: bool | None) -> None:
    """One sport's part of run-all (runs in its own process): daily pipeline, then its championship sims, which
    only need that sport's fresh results and forecasts."""
    from polypicker.futures import SOCCER_RUNNERS
    if sport == "nba":
        ok = [_step("nba daily", daily, days=7, retries=3), _step("nba futures", futures_cmd, only="nba", sims=20_000)]
    elif sport == "nfl":
        ok = [_step("nfl daily", nfl_daily, weeks=2, retries=3, full=full),
              _step("nfl futures", futures_cmd, only="nfl", sims=20_000)]
    else:
        ok = [_step("soccer daily", soccer_daily, days=10, league=None, full=full),
              _step("soccer futures", futures_cmd, only=",".join(["ecuador", *SOCCER_RUNNERS]), sims=20_000)]
    if not all(ok):
        raise RuntimeError(f"{sport}: daily or futures failed")


@app.command("run-all")
def run_all(full: bool | None = typer.Option(None, "--full/--quick", help="Force (or skip) the slow full passes: every "
                                             "NFL season, every soccer calendar date, research feeds. Default: automatic "
                                             "(NFL weekly, soccer every 20 h)")) -> None:
    """Morning / midday job: every league's daily pipeline (results, grading, data feeds, forecasts), then
    the championship simulations. The three sports run at the same time."""
    run = _job_start("run-all")
    ok = _parallel({"nba": ("nba pipeline", _sport_pipeline, {"sport": "nba", "full": full}),
                    "nfl": ("nfl pipeline", _sport_pipeline, {"sport": "nfl", "full": full}),
                    "soccer": ("soccer pipeline", _sport_pipeline, {"sport": "soccer", "full": full})})
    ok["nfl_extras"] = _step("nfl extras", nfl_extras, first_season=None)
    ok["weather"] = _step("weather", weather_cmd, sport="all", history=False)
    # New clubs (season start, cup draws) get their logo; existing ones are never re-downloaded.
    ok["logos"] = _step("soccer logos", soccer_logos, refresh=False)
    ok["combos"] = _step("combos", combos_cmd)
    _dump({"job": "run-all", "ok": ok})
    optional = {"nfl_extras", "weather"}   # supplementary feeds: a failure is logged, not a failed job
    _job_end(run, "run-all", ok, frozenset(optional))
    if not all(v for k, v in ok.items() if k not in optional):
        raise typer.Exit(1)


def _nba_refresh(days: int) -> None:
    ok = {"injuries": _step("injuries", injuries, retries=2),
          "official_injuries": _step("official injuries", official_injuries, backfill_from_season=None, retries=2),
          "odds": _step("odds", odds, backfill_from_season=None, force=False, retries=2),
          "polymarket": _step("polymarket", polymarket, backfill_since=None, retries=2),
          "kalshi": _step("kalshi", kalshi, backfill=False, retries=2),
          "odds_api": _step("odds api", odds_api, force=False, retries=2)}
    ok["predict"] = _step("predict", predict, days=days)
    ok["value"] = _step("value", value_cmd)
    if not ok["predict"]:
        raise RuntimeError("NBA forecasts failed")


def _nfl_refresh() -> None:
    """Latest scores, injuries (current season only), market prices, then re-forecast."""
    _step("nfl ingest (current season)", nfl_ingest, retries=2, first_season=_nfl_season())
    _step("nfl sync", nfl_sync, lookback_days=2, retries=2, backfill_quarters=False)
    _step("nfl markets", nfl_markets, backfill=False, retries=2)
    _step("nfl odds api", nfl_odds_api, force=False, retries=2)
    if not _step("nfl predict", nfl_predict, weeks=1):   # grading stays in run-all (10 s, not needed pre-bet)
        raise RuntimeError("NFL forecasts failed")


def _soccer_refresh(hours: int) -> None:
    """Only competitions with a match in the next ``hours``: results / kickoff changes, betting lines, re-forecast."""
    from sqlalchemy import text

    from polypicker.soccer.etl.odds_api import DEFAULT_LEAGUES
    from polypicker.soccer.leagues import BY_COMPETITION
    with owner_engine().connect() as c:
        comps = [r[0] for r in c.execute(text("""
            SELECT DISTINCT competition FROM soccer_games WHERE status = 'scheduled'
               AND kickoff_utc BETWEEN now() - interval '3 hours' AND now() + make_interval(hours => :h)"""), {"h": hours})]
    days = max(1, -(-hours // 24))
    failed = []
    for slug in [BY_COMPETITION[c].slug for c in comps if c in BY_COMPETITION]:
        _step(f"soccer sync {slug}", soccer_sync, league=slug, back_days=1, ahead_days=days + 1, full=False)
        _step(f"soccer odds {slug}", soccer_odds, league=slug, backfill_from_season=None, ahead_days=days + 1, force=False)
        if slug in DEFAULT_LEAGUES:
            _step(f"soccer pinnacle {slug}", soccer_pinnacle, league=slug, force=False)
        if not _step(f"soccer predict {slug}", soccer_predict, league=slug, days=days + 1):
            failed.append(slug)
    if failed:
        raise RuntimeError(f"soccer forecasts failed for: {', '.join(failed)}")


@app.command("refresh")
def refresh(days: int = typer.Option(7, help="NBA forecast window (days)"),
            sport: str = typer.Option("all", help="nba, nfl, soccer or all"),
            soccer_hours: int = typer.Option(36, help="Soccer: competitions with a match in the next N hours")) -> None:
    """Pre-game / pre-bet job (~30 s): latest injuries, betting lines and market prices, then re-forecast, for
    every sport at once (soccer: only competitions playing soon). Forecasts only get a new revision when an input
    actually changed. The full daily pipeline is ``run-all``."""
    run = _job_start("refresh")
    jobs = {"nba": ("nba refresh", _nba_refresh, {"days": days}),
            "nfl": ("nfl refresh", _nfl_refresh, {}),
            "soccer": ("soccer refresh", _soccer_refresh, {"hours": soccer_hours})}
    if sport != "all":
        if sport not in jobs:
            raise typer.BadParameter("sport must be nba, nfl, soccer or all")
        jobs = {sport: jobs[sport]}
    ok = _parallel(jobs)
    ok["combos"] = _step("combos", combos_cmd)
    _dump({"job": "refresh", "ok": ok})
    _job_end(run, "refresh", ok)
    if not all(ok.values()):
        raise typer.Exit(1)


def _games_in_progress() -> dict[str, int]:
    """Games that started within a typical game length and aren't final (cheap check; no network)."""
    from sqlalchemy import text
    sql = """
        SELECT 'nba', count(*) FROM games WHERE status IN ('scheduled', 'live', 'unknown')
               AND tipoff_utc BETWEEN now() - interval '4 hours' AND now() + interval '5 minutes'
        UNION ALL
        SELECT 'nfl', count(*) FROM nfl_games WHERE status IN ('scheduled', 'live', 'unknown')
               AND kickoff_utc BETWEEN now() - interval '4.5 hours' AND now() + interval '5 minutes'
        UNION ALL
        SELECT 'soccer', count(*) FROM soccer_games WHERE status IN ('scheduled', 'live', 'unknown')
               AND kickoff_utc BETWEEN now() - interval '2.5 hours' AND now() + interval '5 minutes'"""
    with owner_engine().connect() as conn:
        return {k: int(n) for k, n in conn.execute(text(sql)).all()}


def _soccer_in_progress() -> list[str]:
    """League slugs with a soccer game in progress (same window as _games_in_progress)."""
    from sqlalchemy import text

    from polypicker.soccer.leagues import BY_COMPETITION
    sql = """SELECT DISTINCT competition FROM soccer_games WHERE status IN ('scheduled', 'live', 'unknown')
             AND kickoff_utc BETWEEN now() - interval '2.5 hours' AND now() + interval '5 minutes'"""
    with owner_engine().connect() as conn:
        return [BY_COMPETITION[c].slug for (c,) in conn.execute(text(sql)) if c in BY_COMPETITION]


@app.command("scores")
def scores() -> None:
    """Live-score job (every few minutes): syncs only the leagues with games in progress; exits fast otherwise."""
    run = _job_start("scores")
    live = _games_in_progress()
    if not any(live.values()):
        ok = _step("combos", combos_cmd)   # combos whose games ended since the last sync get graded; slots rebuilt
        _dump({"job": "scores", "in_progress": live, "synced": []})
        _job_end(run, "scores", {"combos": ok})
        return
    done = []
    if live.get("nba"):
        done.append(("nba", _step("nba sync", sync, lookback_days=1, retries=1)))
    if live.get("nfl"):
        done.append(("nfl", _step("nfl sync", nfl_sync, lookback_days=2, retries=1, backfill_quarters=False)))
    if live.get("soccer"):
        done.append(("soccer", all([_step(f"soccer sync {slug}", soccer_sync, league=slug, back_days=1, ahead_days=1, full=False)
                                    for slug in _soccer_in_progress()])))
    done.append(("combos", _step("combos", combos_cmd)))
    _dump({"job": "scores", "in_progress": live, "synced": dict(done)})
    _job_end(run, "scores", dict(done))


@app.command("alerts")
def alerts_cmd(test: bool = typer.Option(False, help="Send a test alert on every configured channel"),
               limit: int = typer.Option(20, help="How many recent errors to list")) -> None:
    """Recent job errors (what the failure alerts were about), or --test the alert channels."""
    from sqlalchemy import select

    from polypicker.alerts import JobAlert, send
    if test:
        _dump({"sent": send("PolyPicker test alert", "Alerts are working: you'll get one like this when a job fails.")})
        return
    with owner_engine().connect() as c:
        rows = c.execute(select(JobAlert).order_by(JobAlert.id.desc()).limit(limit)).scalars().all()
    _dump([{"at": r.created_at, "job": r.job, "run": r.job_run, "where": r.logger, "error": r.message} for r in rows])


if __name__ == "__main__":
    app()

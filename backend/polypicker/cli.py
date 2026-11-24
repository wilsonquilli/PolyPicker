"""Operator CLI. All writes happen here, never through the public API (FR-11.4)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

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
        injuries(retries=retries)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).error("injury report failed, using box-score availability: %s", e)
    grade()
    _step("value grade", value_grade)
    predict(days=days)
    _step("value", value_cmd)


# ---------------------------------------------------------------- NFL

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


@nfl_app.command("train")
def nfl_train(validation_season: int = typer.Option(2025, help="Season held out for validation"),
              activate: bool = typer.Option(True, help="Use the new models for live forecasts")) -> None:
    """Train the NFL game + player prop models chronologically and register them."""
    from polypicker.nfl.ml.pipeline import build_features, train_models
    eng = owner_engine()
    ids = train_models(eng, build_features(eng), validation_season, activate=activate)
    _audit("nfl_train", {"validation_season": validation_season, "model_ids": ids, "activated": activate})
    _dump(ids)


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


@nfl_app.command("daily")
def nfl_daily(weeks: int = 2, retries: int = 3) -> None:
    """Scheduled NFL job: nflverse import, ESPN scores, grade, publish forecasts. An ESPN outage is
    logged and the job continues with the data it has."""
    nfl_ingest(retries=retries, first_season=None)
    try:
        nfl_sync(lookback_days=21, retries=2, backfill_quarters=True)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).error("ESPN sync failed, continuing: %s", e)
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
def soccer_sync(league: str = LEAGUE_OPT, back_days: int = 10, ahead_days: int = 60) -> None:
    """Recent results (+ team stats) and upcoming fixtures from ESPN."""
    from polypicker.soccer.etl.espn import sync_espn
    _dump(sync_espn(owner_engine(), _lg(league).competition, back_days=back_days, ahead_days=ahead_days))


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
def soccer_daily(days: int = 10, league: str | None = typer.Option(None, "--league", "-l", help="One league (default: all)")) -> None:
    """Scheduled job, per league: ESPN sync, football-data.org backup (if a key is set), betting lines, grade, publish forecasts.
    One league failing doesn't stop the others."""
    from polypicker.soccer.leagues import LEAGUES
    log = logging.getLogger(__name__)
    slugs = [_lg(league).slug] if league else list(LEAGUES)
    for slug in slugs:
        try:
            soccer_sync(league=slug, back_days=10, ahead_days=60)
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
    try:
        log.info("start %s", name)
        fn(*args, **kwargs)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("step %s failed: %s", name, e)
        return False


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


@app.command("run-all")
def run_all() -> None:
    """Morning / midday job: every league's daily pipeline (results, grading, data feeds, forecasts), then
    the championship simulations."""
    ok = {"nba": _step("nba daily", daily, days=7, retries=3),
          "nfl": _step("nfl daily", nfl_daily, weeks=2, retries=3),
          "soccer": _step("soccer daily", soccer_daily, days=10, league=None)}
    ok["futures"] = _step("futures", futures_cmd, only=None, sims=20_000)
    # New clubs (season start, cup draws) get their logo; existing ones are never re-downloaded.
    ok["logos"] = _step("soccer logos", soccer_logos, refresh=False)
    ok["combos"] = _step("combos", combos_cmd)
    _dump({"job": "run-all", "ok": ok})
    if not all(ok.values()):
        raise typer.Exit(1)


@app.command("refresh")
def refresh(days: int = 7) -> None:
    """Pre-game job (every 30 min in the evening): NBA injuries, betting lines, Polymarket, re-forecast.
    Forecasts only get a new revision when an input actually changed."""
    ok = {"injuries": _step("injuries", injuries, retries=2),
          "odds": _step("odds", odds, backfill_from_season=None, force=False, retries=2),
          "polymarket": _step("polymarket", polymarket, backfill_since=None, retries=2)}
    ok["predict"] = _step("predict", predict, days=days)
    ok["value"] = _step("value", value_cmd)
    ok["combos"] = _step("combos", combos_cmd)
    _dump({"job": "refresh", "ok": ok})
    if not ok["predict"]:
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


@app.command("scores")
def scores() -> None:
    """Live-score job (every few minutes): syncs only the leagues with games in progress; exits fast otherwise."""
    live = _games_in_progress()
    if not any(live.values()):
        _step("combos", combos_cmd)   # combos whose games ended since the last sync get graded; slots rebuilt
        _dump({"job": "scores", "in_progress": live, "synced": []})
        return
    done = []
    if live.get("nba"):
        done.append(("nba", _step("nba sync", sync, lookback_days=1, retries=1)))
    if live.get("nfl"):
        done.append(("nfl", _step("nfl sync", nfl_sync, lookback_days=2, retries=1, backfill_quarters=False)))
    if live.get("soccer"):
        done.append(("soccer", _step("soccer sync", soccer_sync, back_days=1, ahead_days=1)))
    done.append(("combos", _step("combos", combos_cmd)))
    _dump({"job": "scores", "in_progress": live, "synced": dict(done)})


if __name__ == "__main__":
    app()

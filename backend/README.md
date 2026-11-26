# PolyPicker backend

Python service that imports NBA data, trains forecasting models, writes immutable prediction
snapshots and serves a read-only JSON API to the web and mobile clients.

```
data/ (CSV, Parquet) ──┐
NBA.com live feed ─────┴▶ etl (adapter → validate → idempotent upsert) ──▶ PostgreSQL
PostgreSQL ──▶ ml (point-in-time features → models → forecasts → grading) ──▶ predictions (append-only)
PostgreSQL ──▶ api (FastAPI, SELECT-only role) ──▶ web / mobile
```

## Setup

```bash
cd backend
docker compose up -d --wait           # PostgreSQL 17 on localhost:5433 (+ read-only API role)
python3.14 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env                  # adjust if needed; never commit .env

.venv/bin/polypicker db init          # tables, immutability trigger, data source registry (safe to re-run)
.venv/bin/polypicker ingest           # ~75 s: 2023-24 → today from ../data
.venv/bin/polypicker train            # train 2023-24 + 2024-25, validate 2025-26, refit, activate
.venv/bin/polypicker backtest --season 2026   # point-in-time forecasts for completed 2026-27 games
.venv/bin/polypicker predict --days 2 # live forecasts for the next 48 h

.venv/bin/uvicorn polypicker.api.main:app --port 8000
# → http://localhost:8000/api/v1/docs  (OpenAPI: /api/v1/openapi.json)

.venv/bin/pytest                      # 100 tests; DB tests skip if Postgres isn't running
```

## Data sources

| Source | Adapter | Provides |
|---|---|---|
| Kaggle "Historical NBA Data and Player Box Scores" (Eoin Moore), files in `../data` | `etl/sources/kaggle_csv.py` | history from 2023-24, the season schedule, players |
| NBA.com live data feed (`cdn.nba.com`, via [`nba_api`](https://github.com/swar/nba_api)) | `etl/sources/nba_live.py` | results and box scores for games that have tipped off |

You only need to download the Kaggle files once (and again when a new season's schedule comes out).
`polypicker sync` fills in everything since: for each game that has tipped off without a final result
(looking back 14 days), it fetches the live box score and writes the score, quarters, team and player
stats. Both sources use NBA.com IDs, so rows update in place, and the stat conventions match
(team rebounds = OREB + DREB, team turnovers = player turnovers).

Guarantees when mixing the two:

* A final result is never overwritten by a source that doesn't have it (re-importing an older
  download leaves live results alone; see `final_results_protected` in the run stats).
* Season date ranges only widen; partial imports can't shrink them.
* The live adapter re-emits teams and known players from the database, so it never blanks columns
  it doesn't know. New players (call-ups, two-way signings) are created from box-score names.
* A box score that fails to download is logged (`live_fetch_failed`) and retried on the next run.
  If every request fails for 3+ games the run fails (feed blocked or down) and is retried with backoff.

Notes: the live feed is unofficial NBA.com data (subject to the NBA.com Terms of Use). It's served from
`cdn.nba.com`, which works from home connections; `stats.nba.com` (not used) blocks many cloud IPs.

## NFL

The NFL runs as a parallel pipeline in `polypicker/nfl/` with its own tables (`nfl_*`), sharing the run
tracking, model registry and market/settlement rules with the NBA.

| Source | Adapter | Provides |
|---|---|---|
| nflverse via `nflreadpy` (no download step) | `nfl/etl/nflverse.py` | 2018+ schedules and results, team and player game stats, offensive snap counts, weekly rosters (IR / released / traded), official injury reports, closing betting lines (accuracy yardstick only) |
| ESPN scoreboard (`site.api.espn.com`) | `nfl/etl/espn.py` | live status, final scores, quarter scores; one request per game date |

IDs: `game_id` = ESPN event id (nflverse carries it for every game), `team_id` = NFL franchise id
(stable across relocations), `player_id` = GSIS id. The same safeguards as the NBA apply (finals never
downgraded, partial sources never blank columns).

Models (`nfl/ml/`), trained on 2018-2024, validated on 2025, backtested on 2026:
* **Game**: ridge regression on team points from week-by-week point-in-time features (points scored/allowed,
  EPA per play, last 4 games, Elo, home field / neutral site, rest / byes / short weeks, dome, division game,
  starting QB listed Out/Doubtful) -> Normal margin and total. Ties settle the moneyline as a push.
* **Player props**: passing yards (Normal), passing TDs, rushing yards, receptions, receiving yards and anytime TD
  (Negative Binomial), each a gradient-boosted Poisson mean on snap share, usage, recent/season production,
  opponent defense and game script. Players Out/Doubtful or on IR / released / traded get no forecast.
* Probabilities for lines/thresholds are published only if held-out calibration error <= 0.04 (all pass today).

```bash
.venv/bin/polypicker nfl ingest           # ~70 s, idempotent
.venv/bin/polypicker nfl sync [--backfill-quarters]
.venv/bin/polypicker nfl train            # registers nfl_game + nfl_player_* models
.venv/bin/polypicker nfl backtest --season 2026
.venv/bin/polypicker nfl predict          # every game in the next 2 NFL weeks
.venv/bin/polypicker nfl daily            # ingest + ESPN sync + grade + predict
```

API: `/api/v1/nfl/...` (`games`, `games/weeks`, `games/{id}`, `games/{id}/boxscore`, `games/{id}/predictions`,
`games/{id}/predictions/custom`, `teams`, `standings`, `teams/{abbr}`, `players/{id}`, `search`,
`models/performance` with the market benchmark, `status`).

## Soccer (10 competitions)

`polypicker/soccer/` with tables `soccer_*`; every row is tagged with a `competition` (ESPN code). Competitions are
listed once in `polypicker/soccer/leagues.py` (URL slug, ESPN code, kind, season calendar, cross-check sources,
model target, feature pool); adding one is a new entry there plus a backfill and a training run. Each competition
has its own model. Match outcomes only for now; player predictions come later.

| Competition | Slug | ESPN | Kind | Seasons | openfootball | football-data.org |
|---|---|---|---|---|---|---|
| Premier League | `epl` | `eng.1` | league | Aug-May | `en.1` | `PL` |
| LaLiga | `laliga` | `esp.1` | league | Aug-May | `es.1` | `PD` |
| Bundesliga | `bundesliga` | `ger.1` | league | Aug-May | `de.1` | `BL1` |
| Serie A | `seriea` | `ita.1` | league | Aug-May | `it.1` | `SA` |
| Ligue 1 | `ligue1` | `fra.1` | league | Aug-May | `fr.1` | `FL1` |
| Champions League | `ucl` | `uefa.champions` | cup | Aug-May | - | `CL` |
| Europa League | `uel` | `uefa.europa` | cup | Aug-May | - | - |
| Copa Libertadores | `libertadores` | `conmebol.libertadores` | cup | calendar year | - | - |
| Copa Sudamericana | `sudamericana` | `conmebol.sudamericana` | cup | calendar year | - | - |
| LigaPro Serie A (Ecuador) | `ecuador` | `ecu.1` | league (split season + finals) | calendar year | - | - |

Cups: matches store `stage`, `leg`, `neutral`, `note` (aggregate) and, for ties settled after 90 minutes,
`decided` (`aet`/`pen`), the 90-minute score (from ESPN's goal timeline, checked against the final score) and the
shootout. `result` is always the 90-minute result, which is what is forecast and graded. Group membership comes
from ESPN standings (`soccer_groups`). Cup models compute team ratings over a feature pool (European cups: the five
leagues + both cups; South American cups: both cups + LigaPro), so form carries between competitions. Single-match
finals are treated as neutral venues. `/standings` returns group / league-phase / stage tables plus knockout ties.

| Source | Role |
|---|---|
| ESPN (`site.api.espn.com`, unofficial) | primary: 2010-11+ fixtures, results, half-time scores, team match stats (shots, shots on target, possession, corners, cards); daily sync of recent results and upcoming fixtures |
| openfootball `football.json` (CC0) | independent result history: every ESPN final is cross-checked (`of_verified`), a missing result is filled. Its automatic updates stopped in Sept 2026, so it isn't used for the current season |
| football-data.org (free key) | official API backup for the current season: stores its match id, fills/flags results. Set `FOOTBALL_DATA_API_KEY` in `.env`; skipped without it. Attribution: "Football data provided by the Football-Data.org API" |

Model (`soccer/ml/`): expected goals per team from a Poisson regression on date-by-date point-in-time
features (exponentially weighted goals / shots / shots on target for and against, season record, Elo with a
goal-difference multiplier, home advantage, rest, promoted sides) -> Dixon-Coles scoreline grid -> home /
draw / away, likely scores, over/under 0.5-4.5 goals, both teams to score. Trained 2011-12..2024-25,
validated on 2025-26 (vs. historical frequencies and an Elo-only model), backtested on 2026-27.

Every command takes `--league <slug>` (default `epl`); `daily` runs all competitions unless one is given and skips any without a trained model yet.

```bash
.venv/bin/polypicker soccer ingest -l laliga        # ESPN backfill 2010-11 -> now (~5 min/season, resumable)
.venv/bin/polypicker soccer crosscheck -l laliga    # results vs openfootball
.venv/bin/polypicker soccer train -l laliga && .venv/bin/polypicker soccer backtest -l laliga --season 2026
.venv/bin/polypicker soccer daily                   # every league: ESPN sync + football-data.org (if key) + grade + predict
```

API: `/api/v1/soccer/{slug}/...` (`games`, `games/dates`, `games/{id}`, `games/{id}/predictions`, `table`, `teams`,
`teams/{id}`, `standings`, `search`, `models/performance`, `status`).

## Operating timetable (FR-06.2, FR-06.3)

Three scheduled commands cover every league. Each step logs and continues if one source is down.

| Command | When (US Eastern) | What it does |
|---|---|---|
| `polypicker run-all [--full/--quick]` | 06:00 and 13:00 daily | NBA + NFL + soccer daily pipelines (results, grading, injuries, betting lines, Polymarket, forecasts, championship simulations), one process per sport running at the same time (~1.5 min). Slow full passes run automatically: every soccer calendar date in the next 60 days + football-data.co.uk / FotMob / FPL once every 20 h (otherwise ESPN re-reads only dates with an unfinished match), every NFL season from 2018 once a week (otherwise the current season only). `--full` forces them (~2.5 min), `--quick` skips them |
| `polypicker soccer logos [--refresh]` | inside `run-all` | Club logos from ESPN's logo CDN (`a.espncdn.com/i/teamlogos/soccer/500/{id}.png`, free, no key, unofficial) for every club in each competition's current season. Only clubs without a file are downloaded, so each logo is fetched once (new clubs appear at season start or after cup draws). Saved as 256 px WebP in `web/public/logos/soccer/{id}.webp`; `web/lib/soccer-logos.json` lists them for `TeamBadge`. `--refresh` re-downloads all (e.g. after a crest redesign). |
| `polypicker futures [--only nba,epl] [--sims 20000]` | inside `run-all` | Championship forecasts (home page "Who wins it all?"): each game model prices every remaining fixture, team ratings are fitted to those forecasts, then the rest of the season and the post-season are simulated. Stored in `futures_forecasts`, served at `GET /api/v1/futures`. Limits: random tie-breaks beyond points/goal difference, the NBA's 30 unscheduled Cup-dependent games are skipped, UCL/UEL knockout pairings are drawn within UEFA seeding pairs, and post-seasons already underway aren't modelled yet (NBA/NFL playoffs, UEFA knockouts) |
| `polypicker refresh [--sport nba\|nfl\|soccer]` | every 30 min, 17:00 to 22:30, and right before betting | Pre-game for all sports at once (~35 s): NBA injury report, lines, Polymarket/Kalshi; NFL current-season nflverse (injuries), ESPN scores, Kalshi/Polymarket/Odds API; soccer competitions with a match in the next 36 h: ESPN results/kickoffs, odds, Pinnacle snapshot. Then re-forecast + combos. Grading stays in `run-all` |
| `polypicker scores` | every 3 min | live scores for leagues with games in progress; exits in ~1 s otherwise |
| `polypicker train` + `backtest --season <current>` | weekly (Mon 04:00) | retrain on the newest results |
| `polypicker alerts [--test]` | — | Job-failure alerts: every error a `run-all` / `refresh` / `scores` run logs (including inside the per-sport processes) is stored in `job_alerts`, and one alert per failed run goes to a macOS notification (`ALERT_DESKTOP`) and/or `ALERT_WEBHOOK_URL` (ntfy.sh topic, Discord or Slack webhook). Retries that recover don't alert. `--test` sends a test alert; no flag lists recent errors |
| `polypicker nfl espn-odds [--backfill-from-season 2023]` | inside `nfl daily` | ESPN opening + closing moneylines (one book per game, 2023+) into `nfl_market_prices` source `espn`, for the line-movement record on /nfl/accuracy. The NFL model doesn't read this source |
| `polypicker value-backtest --season <current>` | weekly, after retraining | refresh the value-finder backtests shown on /nba/value |

Each `predict` writes a new **revision** only if a game's inputs changed. Old revisions are kept.

**Running it**
* **Server (planned: Render).** Make each row a Render Cron Job running `polypicker <command>` in this
  directory. Render cron uses **UTC**: 06:00 ET is `0 11 * * *` in winter (EST) and `0 10 * * *` in
  summer (EDT); 17:00 to 22:30 ET every 30 min is `*/30 22-23,0-3 * * *` (EST). `scores` is `*/3 * * * *`.
* **This Mac (optional).** `scripts/schedule/install-launchd.sh` installs the same schedule as launchd
  agents (logs in `~/Library/Logs/PolyPicker/`); `uninstall-launchd.sh` removes it. macOS blocks
  background jobs from reading `~/Documents`, so this only works if the project lives elsewhere
  (e.g. `~/Developer`) or `/bin/zsh` has Full Disk Access. Until then, run `polypicker run-all` /
  `polypicker refresh` by hand.

## Value finder (NBA)

`run-all` / `refresh` flag value bets after publishing forecasts (`polypicker value`) and settle finished ones
(`polypicker value-grade`); `polypicker value-backtest --season N` stores an honest backtest. Prices come from
ESPN's odds feed (every book's game lines; one book's player over/unders, opening + closing) and Polymarket.
* EV = p × decimal odds − 1, published at ≥ +3% (and ≤ +40%, larger gaps are stale lines). Stake guide: quarter-Kelly.
* Winners: only games whose forecast already uses a betting line. Props: 30% model / 70% no-vig market blend
  (the stand-alone props model is overconfident; the blend beat both on the 2026-27 backtest).
* CLV: no-vig probability of our side at close minus when flagged (same book); props whose line moved count
  as beating the close if it moved our way. Tables: `nba_odds_books`, `nba_prop_odds`, `value_picks`, `value_backtests`.

## Commands

| Command | What it does |
|---|---|
| `db init` / `db reset --yes` | create schema / drop + recreate (dev only) |
| `ingest [--retries N] [--only-if-changed]` | import / re-sync the Kaggle files; one transaction per attempt, retried with backoff |
| `sync [--lookback-days N]` | pull results + box scores for games that have tipped off from the NBA.com live feed |
| `train [--validation-season Y] [--no-activate]` | chronological train → validate → refit → register |
| `predict [--days N]` | live forecasts for scheduled games within N days |
| `grade` | grade snapshots whose games finished |
| `backtest --season Y` | retrain on seasons < Y, forecast every finished game of Y day-by-day |
| `daily` | ingest (if files changed) + sync + grade + predict |

## API (all `GET`, anonymous, under `/api/v1`)

| Route | Purpose |
|---|---|
| `/games?date=&team=&status=&season=&season_type=` | browse schedule (defaults to today, ET) |
| `/games/dates?season=` | days with games (date picker) |
| `/games/{id}` | detail, line score, last 5/10, home/away record, rest, head-to-head |
| `/games/{id}/boxscore` | team and player box scores (DNP rows show status, not zeros) |
| `/games/{id}/predictions?category=&kind=&revision=` | latest forecast: winner, score, spread, total, player props |
| `/games/{id}/predictions/custom?market=&threshold=&threshold_kind=&player_id=` | probability for a user-chosen line |
| `/teams`, `/standings`, `/teams/{id|abbr}`, `/teams/{t}/games`, `/teams/{a}/head-to-head/{b}` | team views |
| `/players?q=&team=`, `/players/{id}`, `/players/{id}/gamelog` | player views with last-5/10/season aggregates |
| `/search?q=` | teams + players |
| `/predictions/history` | archived snapshots with grading |
| `/models/performance?kind=&season=&category=` | accuracy, Brier, log loss, calibration, MAE |
| `/status`, `/health` | freshness, active models, data sources, disclaimer |

Responses are `{"data": …, "meta": {generated_at, attribution, disclaimer?, total?, …}}`.
Errors are `{"error": {"code", "message", "details?"}}`. Timestamps are UTC ISO-8601.
`game_date` is the US-Eastern date.

## Requirement map

| FR | Where |
|---|---|
| FR-01 schedule/status | `etl/sources/kaggle_csv.py` (status rules), `api/routers/games.py` |
| FR-02 game forecasts | `ml/game_model.py`, `ml/distributions.py`, `ml/pipeline.py::_game_rows` |
| FR-03 player props | `ml/player_features.py` (eligibility, availability), `ml/player_model.py` |
| FR-04 history | `db/models.py`, `etl/*`, team/player routers (last 5/10/season) |
| FR-05 analysis + explanations | `ml/team_features.py`, `GameModel._factors`, `explain_player` |
| FR-06 operations | `cli.py` (`daily`), `prediction_runs`, revisions + `input_hash` |
| FR-07 presentation data | `favorite_label`, attribution/disclaimer in `meta`, `/status` |
| FR-08 search/filters | `/search`, `/players?q=`, `category=` filters, `/games?team=` |
| FR-09 archive/accuracy | `ml/grade.py`, `prediction_outcomes`, `/predictions/history`, `/models/performance` |
| FR-10 ingestion/validation | `etl/validate.py`, `etl/load.py`, `etl/runner.py`, `data_quality_issues`, `source_id_mapping` |
| FR-11 read-only access | `ReadOnlyMiddleware`, `polypicker_api` SELECT-only role, read-only sessions |

## Design decisions worth knowing

* **Game type comes from the game ID** (`TYYNNNNN`), not the inconsistent text labels in the source.
* **No guessing.** A past game with no result is `unknown`, not `postponed`. A player with no minutes
  and no comment is `unknown`, not a zero-stat appearance.
* **Point-in-time features.** Features are built one ET day at a time. A game only sees results from
  earlier days (`tests/test_leakage.py`).
* **Threshold probabilities need a calibrated distribution.** Spread/total and player thresholds are
  published only if held-out calibration error ≤ 0.04 (FR-02.4). Otherwise only the projection is shown.
* **Snapshots are immutable.** A Postgres trigger rejects UPDATE/DELETE on `predictions`. Grades live in
  `prediction_outcomes`.
* **Backtests are labeled.** `kind='backtest'` forecasts were regenerated after the fact and are never
  presented as published picks.

## Known limitations

* **No injury or lineup feed.** Availability is inferred from recent box scores and labeled that way.
  "Likely out" (inactive in the last 2 team games) suppresses the forecast.
* **No live game status** in the source. `live` is never set, and tonight's tipped-off games show `unknown`
  until results are ingested.
* **Licensing.** The data source is registered as research-only. Spec §6.4 is a launch blocker.

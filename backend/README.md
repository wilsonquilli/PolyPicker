# PolyPicker backend

Python service that imports NBA data, trains forecasting models, writes immutable prediction
snapshots and serves a read-only JSON API to the web and mobile clients.

```
data/ (CSV, Parquet) ──▶ etl (adapter → validate → idempotent upsert) ──▶ PostgreSQL
PostgreSQL ──▶ ml (point-in-time features → models → forecasts → grading) ──▶ predictions (append-only)
PostgreSQL ──▶ api (FastAPI, SELECT-only role) ──▶ web / mobile
```

## Setup

```bash
cd backend
docker compose up -d --wait           # PostgreSQL 17 on localhost:5433 (+ read-only API role)
python3.14 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env                  # adjust if needed; never commit .env

.venv/bin/polypicker db init          # tables, immutability trigger, data source registry
.venv/bin/polypicker ingest           # ~75 s: 2023-24 → today from ../data
.venv/bin/polypicker train            # train 2023-24 + 2024-25, validate 2025-26, refit, activate
.venv/bin/polypicker backtest --season 2026   # point-in-time forecasts for completed 2026-27 games
.venv/bin/polypicker predict --days 2 # live forecasts for the next 48 h

.venv/bin/uvicorn polypicker.api.main:app --port 8000
# → http://localhost:8000/api/v1/docs  (OpenAPI: /api/v1/openapi.json)

.venv/bin/pytest                      # 64 tests; DB tests skip if Postgres isn't running
```

## Operating timetable (FR-06.2, FR-06.3)

Run `polypicker daily` (ingest → grade → predict) on a schedule:

| When (ET)        | Why                                                                 |
|------------------|---------------------------------------------------------------------|
| 06:00 daily      | sync overnight finals and stat corrections, grade, publish forecasts |
| 12:00 daily      | re-forecast with any new data before early tips                     |
| after new data   | the same command; unchanged games are skipped (no new revision)     |

Each `predict` writes a new **revision** only if a game's inputs changed. Old revisions are kept.

## Commands

| Command | What it does |
|---|---|
| `db init` / `db reset --yes` | create schema / drop + recreate (dev only) |
| `ingest [--retries N]` | import / re-sync; one transaction per attempt, retried with backoff |
| `train [--validation-season Y] [--no-activate]` | chronological train → validate → refit → register |
| `predict [--days N]` | live forecasts for scheduled games within N days |
| `grade` | grade snapshots whose games finished |
| `backtest --season Y` | retrain on seasons < Y, forecast every finished game of Y day-by-day |
| `daily` | ingest + grade + predict |

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

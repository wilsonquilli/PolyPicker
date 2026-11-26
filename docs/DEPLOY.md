# Deploying PolyPicker (free tier)

| Piece | Host | Notes |
|---|---|---|
| Web (Next.js, `web/`) | Vercel Hobby | proxies `/api/v1/*` to the API, so browsers never call Render directly |
| API (FastAPI, `backend/`) | Render free web service (`render.yaml`) | read-only DB role; sleeps after 15 min idle, kept awake by the scores job |
| Database | Supabase free (500 MB) | ~410 MB after compaction; growth ~12 MB/week; seasons archived automatically |
| Scheduled jobs | GitHub Actions (`.github/workflows/jobs.yml`) | public repo = unlimited minutes |

Never paste passwords or connection strings into chat, code or commits. They go only into the Supabase / Render /
Vercel / GitHub dashboards, or the hidden prompts of `push_to_supabase.sh`.

## 1. Supabase
1. New project, region **East US (North Virginia)**. Save the database password in a password manager.
2. **Project Settings → Data API: turn it off.** We don't use it, and left on it can expose tables to the public anon key.
3. **Connect → Session pooler**: copy the URI (`postgresql://postgres.<ref>:[YOUR-PASSWORD]@aws-...pooler.supabase.com:5432/postgres`)
   and put your password in. Use the *session pooler*, not "direct connection" (IPv6 only; Render and GitHub can't reach it).

## 2. Copy the database (from your Mac)
```bash
cd backend && scripts/deploy/push_to_supabase.sh
```
It asks (hidden) for the pooler URI and a new password for the read-only `polypicker_api` role, then
compacts → dumps (without the 338 MB training-only `nba_lineup_possessions` rows) → restores → creates the role →
compares row counts.

**After this first push, Supabase is the source of truth**: the jobs write forecasts and results there. The script
refuses to run again if Supabase has newer forecasts than your Mac (`FORCE=1` overrides, erasing them).

The API role's URL is the same pooler URI with the user changed: `postgresql://polypicker_api.<ref>:<api password>@aws-...pooler.supabase.com:5432/postgres`.

## 3. Render (API)
New → **Blueprint** → pick the repo (it reads `render.yaml`). When asked for the env vars:
- `API_DATABASE_URL` = the `polypicker_api.<ref>` pooler URI above
- `CORS_ORIGINS` = your Vercel URL (e.g. `https://polypicker.vercel.app`)

Check `https://<service>.onrender.com/api/v1/health` returns OK.

## 4. Vercel (web)
Import the repo, **Root Directory = `web`**, framework Next.js. Environment variable:
- `API_URL` = `https://<service>.onrender.com/api/v1`

## 5. GitHub Actions (jobs)
Repo → Settings → Secrets and variables → Actions:
- Secret `DATABASE_URL` = the `postgres.<ref>` pooler URI (owner; jobs write forecasts)
- Secret `ALERT_WEBHOOK_URL` (recommended) = e.g. `https://ntfy.sh/polypicker-<random>`: failure alerts on your phone
- Secrets `ODDS_API_KEY`, `FOOTBALL_DATA_API_KEY` if you have them
- Variable `API_PUBLIC_URL` = `https://<service>.onrender.com` (no `/api/v1`)

Then Actions → **jobs** → Run workflow → `run-all` to check it end to end. Schedules (ET): run-all 06:00 + 13:00,
refresh every 30 min 17:00–22:30, live scores every 3 min 07:00–01:00.

## Storage upkeep (automatic)
- `polypicker compact` (end of every run-all): deletes superseded forecast revisions of finished, graded games;
  keeps the final pregame revision (what every accuracy number uses) and every revision's win-probability rows.
- `polypicker archive` (end of every run-all; no-op until a season ends): moves a finished season's player-prop
  rows to `archive/{sport}-{season}-player-predictions.parquet` (published as a GitHub release by the workflow)
  and keeps their exact accuracy sums in `archived_prediction_stats`; accuracy pages are unchanged.

## Known limits
- NBA.com sometimes blocks cloud IPs (GitHub runners). If `nba sync` fails in the Actions log, NBA results and box
  scores stop updating: tell Claude and we'll add a fallback source.
- New soccer club logos downloaded by run-all on Actions aren't committed; run `polypicker soccer logos` locally and commit when a new club appears.
- Retraining models stays manual on the Mac and needs a way to pull Supabase's data back down first (not built yet;
  models aren't due for retraining soon).

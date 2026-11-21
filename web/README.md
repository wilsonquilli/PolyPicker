# PolyPicker web

Next.js 16 (App Router, Cache Components) + TypeScript + Tailwind CSS 4.

The UX follows prediction-market patterns: probability-first cards, category tabs, an event page with a chart,
outcome rows and a side panel. Nothing can be bought, sold or wagered. The side panel is a **line explorer** that
shows the model's probability for any line. PolyPicker uses its own branding and team abbreviation badges
instead of official logos (spec §8.1).

## Run

```bash
# 1. Backend API running on :8000 (see ../backend/README.md)
# 2. Web app
cp .env.example .env.local
npm install
npm run dev        # http://localhost:3000
npm run lint
npm run build
```

`API_URL` is used by Server Components. `NEXT_PUBLIC_API_URL` is used by the browser (search box, line explorer)
and must be listed in the API's `CORS_ORIGINS`.

## Pages

| Route | What it shows |
|---|---|
| `/` (`?view=tomorrow\|upcoming\|results`, `?date=`) | Closest-matchup hero, most-confident picks, date strip, game cards |
| `/games/[id]` | Win %, margin/total distribution chart, outcome rows, spread/total lines, factors, player props, comparison, head-to-head, settlement rules, line explorer |
| `/teams`, `/teams/[abbr]` | Teams by division; record, ratings, last-10 margins, upcoming and completed games |
| `/players/[id]` | Last 5 / 10 / season averages, points-by-game chart, game log (DNP shown as status, not zeros) |
| `/standings` | Conference standings with playoff and play-in cutoffs |
| `/accuracy` | Live vs. backtest accuracy, Brier score, calibration chart, recently graded forecasts, validation vs. baselines |
| `/methodology` | How to read forecasts, inputs, honesty rules, disclaimer |

## Structure

```
app/            routes (each data read sits inside <Suspense>, as Cache Components requires)
components/     ui.tsx (badges, pills, bars, gauge), GameCard, Header + HeaderClient (search, tabs, theme)
components/game DistributionChart, LineCalculator, PropsBoard (client components)
components/charts.tsx  server-rendered SVG charts (margin bars, stat bars, calibration)
lib/            api.ts (server fetch), types.ts (API contracts), format.ts, teams.ts
```

## Conventions

* **Times are shown in ET** (the NBA's reference zone), formatted on the server so server and client output match.
  Relative times ("updated 5m ago") are computed client-side.
* **Color is never the only signal.** Favorites are also labeled in text, and charts carry `aria-label`s (FR-07.2, NFR-05.3).
* **Light and dark themes** come from CSS tokens in `app/globals.css`. An inline script applies the saved theme
  before first paint.
* **States**: skeleton fallbacks, empty states ("no games", "forecast not published yet"), stale and backtest labels,
  and an error boundary.

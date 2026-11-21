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

`API_URL` is where the Next.js server finds the backend. Browser code (search, line explorer) calls the same site
at `/api/v1/...`, and Next.js proxies those calls to `API_URL`. That means phones and the installed app never need
to reach the backend directly, and no CORS setup is needed.

## Installable app (PWA): iPhone, iPad, Android, desktop

PolyPicker installs from the browser to the home screen. It's free, with no App Store or Google Play needed.
The phone experience lives in this same codebase.

| Piece | File |
|---|---|
| Web app manifest (name, icons, standalone display, shortcuts, install screenshots) | `app/manifest.ts` |
| Icons: `public/icons/*` (any + maskable), `app/apple-icon.png` (iOS), `app/icon.png` (favicon) | |
| iOS meta (`apple-mobile-web-app-*`), theme colors, safe-area viewport | `app/layout.tsx` |
| Service worker: offline copies of visited pages, cached build assets, never caches live data | `public/sw.js` |
| SW registration, install prompt (Android) / Add-to-Home-Screen hint (iOS), offline banner, bottom tab bar | `components/pwa.tsx` |
| Install instructions page · phone "More" tab · offline fallback | `app/install`, `app/more`, `app/offline` |

The service worker only registers in production builds (`npm run build && npm start`), so it never interferes
with hot reload in `npm run dev`. Bump `VERSION` in `public/sw.js` when changing caching behaviour.

**How users install it**

* **iPhone/iPad**: Safari (or Chrome/Edge on iOS 16.4+) → Share → **Add to Home Screen**. The site shows this hint
  automatically on iOS.
* **Android**: Chrome/Edge/Samsung Internet shows **Install**. The site also offers its own Install button.
* **Desktop**: install icon in Chrome/Edge's address bar, or the button on `/install`.

**Testing on a real phone.** Installing and offline mode need **HTTPS** (only `localhost` is exempt). A phone on your
Wi-Fi visiting `http://<your-computer-ip>:3000` can browse, but won't get the full app. For a free HTTPS URL to
your laptop, run a production build and open a quick tunnel, for example:

```bash
npm run build && npm start                         # :3000
npx cloudflared tunnel --url http://localhost:3000 # prints https://<random>.trycloudflare.com
```

Open that URL on the phone and install. For a permanent public URL, deploy the web app (e.g. Vercel's free tier)
and host the API and database somewhere reachable. Set `API_URL` to the API's address.

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

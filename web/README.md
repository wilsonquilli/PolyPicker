# PolyPicker web

Next.js 16 (App Router, Cache Components) + TypeScript + Tailwind CSS 4.

The UX follows prediction-market patterns: probability-first cards, category tabs, an event page with a chart,
outcome rows and a side panel. Nothing can be bought, sold or wagered. The side panel is a **line explorer** that
shows the model's probability for any line. PolyPicker uses its own branding; teams are shown with their NBA logos
(`public/logos/nba/{ABBR}.webp`, 256px, trimmed and shown on a white tile so they read in light and dark mode).

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
| Brand: `public/brand/logo-{light,dark}.png` (header wordmark, generated from `design/PolyPickerLogo.png` (master art, not served): the original white-on-transparent art for dark mode, white parts recoloured to ink for light mode); app icons `public/icons/*` (any + maskable), `app/apple-icon.png` (iOS) and `app/icon.png` (favicon) stack the emblem over the wordmark on navy | |
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
Wi-Fi visiting `http://<your-computer-ip>:3000` can browse and use everything (the dev server allows home-network
addresses via `allowedDevOrigins` in `next.config.ts`; restart `npm run dev` after changing it), but can't install it. For a free HTTPS URL to
your laptop, run a production build and open a quick tunnel, for example:

```bash
npm run build && npm start                         # :3000
npx cloudflared tunnel --url http://localhost:3000 # prints https://<random>.trycloudflare.com
```

Open that URL on the phone and install. For a permanent public URL, deploy the web app (e.g. Vercel's free tier)
and host the API and database somewhere reachable. Set `API_URL` to the API's address.

## Splash screen

Same look everywhere: the PolyPicker logo on brand navy (`#021022`).
- **Web:** `components/Splash.tsx` + `.pp-splash*` styles in `app/globals.css`. Server-rendered so it covers the first
  paint, fades out once the app is ready (at least ~0.9 s; 0.4 s in the installed app), once per browser session.
  Never shown again on page changes or language switches; a CSS fallback hides it even without JavaScript.
- **iOS (installed):** `public/splash/launch-*.png`, one per device screen size, linked via
  `lib/splash-screens.json` in the root layout. Regenerate after a logo change:
  `backend/.venv/bin/python web/scripts/generate-splash.py` (reads `design/PolyPickerLogo.png`).
- **Android (installed):** built by Chrome from `app/manifest.ts` (`background_color`, the 512 px icon and the name).

## Sports

Each sport lives under its own URL prefix, with a Polymarket-style sport tab row in the header
(`lib/sports.ts`). Basketball is `/nba/...`, Football is `/nfl/...` (week view, game pages with
props, box score and line explorer, standings, teams, players, accuracy vs. the betting market as a yardstick).
Soccer is `/soccer/<league>/...` (`/soccer` -> `/soccer/epl`): match days, three-way match cards (`components/soccer/`),
match pages with likely scores, table, clubs, accuracy; types in `lib/soccer.ts`. Club badges use ESPN team colors;
logo files dropped into `public/logos/soccer/{espn_team_id}.webp` can be enabled in `components/ui.tsx`.
NFL types live in `lib/nfl.ts`; football components in `components/nfl/`. `GameCard` and `TeamBadge` take a
`league` prop; the header sections and the phone tab bar switch with the current sport. Search (⌘K, `components/SearchPalette.tsx`)
covers every sport and competition through `GET /search/all`. `/` is the multi-sport home page, and pre-move URLs (`/games/:id`, `/teams/...`, `/players/:id`,
`/standings`, `/accuracy`) redirect to their `/nba` equivalents (`next.config.ts`). Team logos live per
league in `public/logos/<league>/{ABBR}.webp` (NBA, NFL: 256px WebP) and `public/logos/soccer/{espn_id}.webp`
(downloaded by `polypicker soccer logos`; list in `lib/soccer-logos.json`).

## Pages

| Route | What it shows |
|---|---|
| `/nba` (`?view=tomorrow\|upcoming\|results`, `?date=`) | Closest-matchup hero, most-confident picks, date strip, game cards |
| `/nba/games/[id]` | Win %, margin/total distribution chart, outcome rows, spread/total lines, factors, player props, comparison, head-to-head, settlement rules, line explorer |
| `/nba/teams`, `/nba/teams/[abbr]` | Teams by division; record, ratings, last-10 margins, upcoming and completed games |
| `/nba/players/[id]` | Last 5 / 10 / season averages, points-by-game chart, game log (DNP shown as status, not zeros) |
| `/nba/standings` | Conference standings with playoff and play-in cutoffs |
| `/nba/accuracy` | Live vs. backtest accuracy, Brier score, calibration chart, recently graded forecasts, validation vs. baselines |
| `/methodology` | How to read forecasts, inputs, honesty rules, disclaimer |

## Languages (English / Spanish)

The EN | ES switch in the header translates every page. URLs don't change: `proxy.ts` reads the `pp-lang` cookie
(first visit: the browser's Accept-Language) and serves `app/[lang]/...` for that language. `/es/...` and `/en/...`
links set the language and redirect to the plain URL, so Spanish links can be shared.

- **Text:** wrap visible strings in `t("English text")`: `getT()` in Server Components (`@/lib/i18n/server`),
  `useT()` in Client Components (`@/components/i18n`). English is the key; add the Spanish to `lib/i18n/es.json`.
  A phrase with no entry falls back to English, never blank. Placeholders: `t("{n} games", { n })`.
- **Sentences with bold words or links:** `rich(t("Tap <b>Add</b> then {icon}"), { icon })` (`@/lib/i18n/rich`).
- **Words whose Spanish depends on context** (table headers, result badges): `keyed(t, "col:W", "W")`.
- **Text from the API** (forecast labels, factor names, injury details, player explanations) goes through `t()` too:
  exact entries in `es.json`, numbered ones via the patterns in `lib/i18n/patterns.ts`. The championship reasons are
  written in Spanish by the backend (`reasons_es`).
- Dates and times use the page language: pass `lang` (from `getLang()` / `useLang()`) to the `lib/format.ts` helpers.
- **Performance:** `es.json` is only imported by `lib/i18n/server.ts`. The browser receives it through `<LangProvider dict>`
  on Spanish pages only, so English visitors never download it. Don't import `es.json`/`es.ts` from client code.
  The EN | ES switch refreshes in place (`router.refresh()`), with no full page reload.

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

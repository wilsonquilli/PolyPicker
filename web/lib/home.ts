// Multi-sport home page: one "market" shape for NBA, NFL and soccer (Premier League, LaLiga) games so the home feed can
// mix them, Polymarket-style. Each league's API response is normalised here.

import { intlLocale, makeT, type Lang, type T } from "./i18n";
import type { NflGame } from "./nfl";
import type { LeagueSlug, SoccerGame } from "./soccer";
import { SOCCER_LEAGUES, teamCss } from "./soccer";
import type { Reason } from "./reasons";
import { lastUpdated, type Freshness, type GameStatus, type GameSummary } from "./types";

export type LeagueKey = "nba" | "nfl" | LeagueSlug;

export const LEAGUES: Record<LeagueKey, { label: string; sport: string; icon: string; href: string }> = {
  nba: { label: "NBA", sport: "Basketball", icon: "🏀", href: "/nba" },
  nfl: { label: "NFL", sport: "Football", icon: "🏈", href: "/nfl" },
  ...(Object.fromEntries(Object.values(SOCCER_LEAGUES).map((l) => [l.slug, { label: l.name, sport: "Soccer", icon: "⚽", href: `/soccer/${l.slug}` }])) as
    Record<LeagueSlug, { label: string; sport: string; icon: string; href: string }>),
};

/** Soccer markets are three-way (home / draw / away) and listed home team first. */
export const isSoccer = (m: { league: LeagueKey }) => m.league in SOCCER_LEAGUES;

export interface MarketSide {
  team_id: number;
  name: string;
  short: string;
  abbr: string;
  record: string | null;
  score: number | null;
  color?: string; // soccer badge color
  p: number | null; // model probability this side wins
}

export type PickKey = "home" | "away" | "draw";

/** Combo grader leg key (the API's `sport-gameid-pick`); plain module so server components can call it. */
export type Sport = "nba" | "nfl" | "soccer";
export const legKey = (league: LeagueKey, gameId: number, pick: PickKey) =>
  `${isSoccer({ league }) ? "soccer" : league}-${gameId}-${pick}`;

export interface Market {
  key: string;
  league: LeagueKey;
  href: string;
  tipoff: string;
  date: string;
  status: GameStatus;
  context: string | null; // "Week 12", "NBA Cup", ...
  home: MarketSide;
  away: MarketSide;
  pDraw: number | null; // soccer only
  pick: PickKey | null;
  pickP: number | null;
  projection: string | null;
  result: PickKey | null; // finals only
  polymarket?: { p_home: number; url: string; volume_usd: number | null } | null; // NBA only, for comparison
  fresh: { at: string; checked: string | null; odds: string | null } | null; // "Updated X ago"; live forecasts only
  reasons: Reason[] | null; // plain-language "why", upcoming games only
}

function freshOf(p: Freshness & { kind: string } | null): Market["fresh"] {
  return p && p.kind === "live" ? { at: lastUpdated(p), checked: p.checked_at ?? null, odds: p.odds_at ?? null } : null;
}

function twoWay(g: GameSummary, league: "nba" | "nfl", context: string | null, t: T): Market {
  const p = g.prediction;
  const side = (s: GameSummary["home"], prob: number | null): MarketSide => ({
    team_id: s.team_id, name: s.full_name ?? s.name, short: s.name, abbr: s.abbreviation, record: s.record, score: s.score, p: prob,
  });
  const final = g.status === "final" && g.home.score !== null && g.away.score !== null;
  const tie = final && g.home.score === g.away.score;
  const proj = p && p.projected_home_points !== null && p.projected_away_points !== null
    ? `${t("Proj.")} ${g.away.abbreviation} ${Math.round(p.projected_away_points)}–${Math.round(p.projected_home_points)} ${g.home.abbreviation}` : null;
  return {
    key: `${league}-${g.game_id}`, league, href: `/${league}/games/${g.game_id}`, tipoff: g.tipoff_utc, date: g.game_date,
    status: g.status, context,
    home: side(g.home, p ? p.p_home_win : null), away: side(g.away, p ? p.p_away_win : null), pDraw: null,
    pick: p ? p.favorite : null, pickP: p ? Math.max(p.p_home_win, p.p_away_win) : null, projection: proj,
    result: !final ? null : tie ? "draw" : g.winner_team_id === g.home.team_id ? "home" : "away",
    fresh: freshOf(p), reasons: g.reasons ?? null,
  };
}

// `t` translates the context / projection lines into the page language (English if omitted).
export function fromNba(g: GameSummary, t: T = makeT()): Market {
  const ctx = g.is_cup && g.season_type === "regular" ? t("NBA Cup") : g.season_type === "regular" ? null : g.label ? t(g.label) : null;
  return twoWay(g, "nba", ctx, t);
}

export function fromNfl(g: NflGame, t: T = makeT()): Market {
  return twoWay(g, "nfl", g.label ? t(g.label) : t("Week {n}", { n: g.week }), t);
}

export function fromSoccer(g: SoccerGame, t: T = makeT()): Market {
  const p = g.prediction;
  const side = (s: SoccerGame["home"], prob: number | null): MarketSide => ({
    team_id: s.team_id, name: s.name, short: s.short_name ?? s.name, abbr: s.abbreviation, record: s.record, score: s.score,
    color: teamCss(s), p: prob,
  });
  return {
    key: `${g.league}-${g.game_id}`, league: g.league, href: `/soccer/${g.league}/games/${g.game_id}`, tipoff: g.tipoff_utc, date: g.game_date,
    status: g.status, context: null,
    home: side(g.home, p ? p.p_home : null), away: side(g.away, p ? p.p_away : null), pDraw: p ? p.p_draw : null,
    pick: p ? p.pick : null, pickP: p ? p.pick_probability : null,
    projection: p && p.xg_home !== undefined && p.xg_away !== undefined
      ? `${t("Exp. goals")} ${g.home.abbreviation} ${p.xg_home.toFixed(1)}–${p.xg_away.toFixed(1)} ${g.away.abbreviation}` : null,
    result: g.result === "H" ? "home" : g.result === "A" ? "away" : g.result === "D" ? "draw" : null,
    fresh: freshOf(p), reasons: g.reasons ?? null,
  };
}

const ET = "America/New_York";

/** Today's calendar date in US-Eastern (YYYY-MM-DD), the reference day for every league's schedule. */
export function todayET(now = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: ET, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

/** "Today 7:30 PM ET", "Tomorrow 1:00 PM ET", "Sat 3:00 PM ET". */
export function whenLabel(iso: string, date: string, today: string, tomorrow: string, lang: Lang = "en"): string {
  const loc = intlLocale(lang);
  const t = new Intl.DateTimeFormat(loc, { hour: "numeric", minute: "2-digit", timeZone: ET }).format(new Date(iso)) + " ET";
  if (date === today) return `${lang === "es" ? "Hoy" : "Today"} ${t}`;
  if (date === tomorrow) return `${lang === "es" ? "Mañana" : "Tomorrow"} ${t}`;
  return `${new Intl.DateTimeFormat(loc, { weekday: "short", timeZone: "UTC" }).format(new Date(`${date}T12:00:00Z`))} ${t}`;
}

/** Request-time clock for server components (call after `connection()`). */
export function nowMs(): number {
  return Date.now();
}

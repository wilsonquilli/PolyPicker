// Soccer response types (backend/polypicker/api/routers/soccer.py) and helpers. Routes live under
// /soccer/<league>; the slugs match backend/polypicker/soccer/leagues.py.
import { intlLocale, makeT, type Lang, type T } from "./i18n";

import type { Reason } from "./reasons";
import type { Freshness, Outcome } from "./types";

type LeagueDef = { slug: string; name: string; short: string; country: string; flag: string; kind: "league" | "cup"; region: Region };
export type Region = "Top leagues" | "European cups" | "South America";

export const SOCCER_LEAGUES = {
  epl: { slug: "epl", name: "Premier League", short: "EPL", country: "England", flag: "🏴󠁧󠁢󠁥󠁮󠁧󠁿", kind: "league", region: "Top leagues" },
  laliga: { slug: "laliga", name: "LaLiga", short: "LaLiga", country: "Spain", flag: "🇪🇸", kind: "league", region: "Top leagues" },
  bundesliga: { slug: "bundesliga", name: "Bundesliga", short: "BUN", country: "Germany", flag: "🇩🇪", kind: "league", region: "Top leagues" },
  seriea: { slug: "seriea", name: "Serie A", short: "Serie A", country: "Italy", flag: "🇮🇹", kind: "league", region: "Top leagues" },
  ligue1: { slug: "ligue1", name: "Ligue 1", short: "Ligue 1", country: "France", flag: "🇫🇷", kind: "league", region: "Top leagues" },
  ucl: { slug: "ucl", name: "Champions League", short: "UCL", country: "Europe", flag: "🇪🇺", kind: "cup", region: "European cups" },
  uel: { slug: "uel", name: "Europa League", short: "UEL", country: "Europe", flag: "🇪🇺", kind: "cup", region: "European cups" },
  libertadores: { slug: "libertadores", name: "Copa Libertadores", short: "Libertadores", country: "South America", flag: "🌎", kind: "cup", region: "South America" },
  sudamericana: { slug: "sudamericana", name: "Copa Sudamericana", short: "Sudamericana", country: "South America", flag: "🌎", kind: "cup", region: "South America" },
  ecuador: { slug: "ecuador", name: "LigaPro Serie A", short: "LigaPro", country: "Ecuador", flag: "🇪🇨", kind: "league", region: "South America" },
} as const satisfies Record<string, LeagueDef>;
export const REGIONS: Region[] = ["Top leagues", "European cups", "South America"];
export type LeagueSlug = keyof typeof SOCCER_LEAGUES;
export type SoccerLeague = (typeof SOCCER_LEAGUES)[LeagueSlug] & { base: string; api: string };
export const LEAGUE_SLUGS = Object.keys(SOCCER_LEAGUES) as LeagueSlug[];

/** The soccer league a pathname belongs to (/soccer/laliga/table -> "laliga"); the Premier League otherwise. */
export function soccerSlugOf(path: string): LeagueSlug {
  const seg = path.split("/")[2];
  return seg && seg in SOCCER_LEAGUES ? (seg as LeagueSlug) : "epl";
}

/** League config for a route segment / API slug, or null for an unknown league (pages call notFound()). */
export function soccerLeague(slug: string | undefined): SoccerLeague | null {
  if (!slug || !(slug in SOCCER_LEAGUES)) return null;
  const lg = SOCCER_LEAGUES[slug as LeagueSlug];
  return { ...lg, base: `/soccer/${lg.slug}`, api: `/soccer/${lg.slug}` };
}

export interface SoccerTeam {
  team_id: number;
  name: string;
  short_name: string | null;
  abbreviation: string;
  color: string | null;
  alt_color: string | null;
}

export interface SoccerSide extends SoccerTeam {
  score: number | null;
  record: string | null; // W-D-L entering the match
}

export interface SoccerHeadline extends Freshness {
  kind: "live" | "backtest";
  revision: number;
  published_at: string;
  p_home: number;
  p_draw: number;
  p_away: number;
  xg_home?: number;
  xg_away?: number;
  pick: "home" | "draw" | "away";
  pick_probability: number;
}

export interface SoccerGame {
  game_id: number;
  league: LeagueSlug;
  season: number;
  season_label: string;
  stage: string | null;
  stage_label: string | null; // cups / split seasons: "Round of 16", "Group stage"
  leg: number | null;
  neutral: boolean;
  note: string | null; // "2nd Leg - X advance 5-3 on aggregate"
  decided: "aet" | "pen" | null; // settled in extra time / on penalties (score is after extra time)
  score_90: { home: number; away: number } | null;
  shootout: { home: number; away: number } | null;
  tipoff_utc: string;
  game_date: string;
  status: "scheduled" | "live" | "final" | "postponed" | "canceled" | "unknown";
  home: SoccerSide;
  away: SoccerSide;
  result: "H" | "D" | "A" | null;
  prediction: SoccerHeadline | null;
  reasons?: Reason[] | null;
}

export interface FormRow { game_id: number; date: string; opponent: string; home: boolean; result: "W" | "D" | "L"; goals_for: number; goals_against: number }

export interface SoccerTeamStats {
  team_id: number; is_home: boolean; goals: number | null; shots: number | null; shots_on_target: number | null;
  possession: number | null; corners: number | null; fouls: number | null; yellow_cards: number | null; red_cards: number | null;
  offsides: number | null; saves: number | null; passes: number | null; pass_pct: number | null;
}

export interface SoccerGameDetail extends SoccerGame {
  venue: string | null;
  attendance: number | null;
  half_time: { home: number | null; away: number | null } | null;
  verified_by_openfootball: boolean | null;
  team_stats: SoccerTeamStats[];
  context: { home_form: FormRow[]; away_form: FormRow[];
    head_to_head: { game_id: number; date: string; season: number; home_team_id: number; away_team_id: number;
      home_score: number; away_score: number; result: "H" | "D" | "A" }[] };
}

export interface SoccerFactor { factor: string; home_goal_edge: number; favors: "home" | "away" }

export interface SoccerPredictions {
  game_id: number;
  game_status: string;
  forecast_status: "current" | "stale" | "pending" | "unavailable" | "backtest";
  kind?: "live" | "backtest";
  revision?: number;
  published_at?: string;
  data_cutoff_utc?: string;
  model_version?: string | null;
  markets: {
    result: Partial<Record<"home" | "draw" | "away", { probability: number; outcome: Outcome | null }>>;
    goals: Partial<Record<"home" | "away", { expected: number; outcome: Outcome | null }>>;
    totals: { expected?: number; outcome?: Outcome | null; probabilities_available?: boolean;
      lines: { line: number; label: string; probability: number; outcome: Outcome | null }[] };
    btts: { probability: number; outcome: Outcome | null } | null;
    factors?: SoccerFactor[];
    top_scores?: { home: number; away: number; p: number }[];
    distribution?: { type: string; xg_home: number; xg_away: number; rho: number };
  } | null;
  notes?: { kind: string | null; result: string; model: string };
}

export interface StandingsTable { name: string; stage: string | null; stage_label: string; rows: TableRow[] }
export interface TieLeg {
  game_id: number; date: string; leg: number | null; status: SoccerGame["status"]; home_team_id: number; away_team_id: number;
  home_score: number | null; away_score: number | null; decided: "aet" | "pen" | null; shootout: { home: number; away: number } | null;
}
export interface KnockoutTie {
  team_a: SoccerTeam; team_b: SoccerTeam; aggregate: { a: number; b: number }; winner_id: number | null;
  note: string | null; complete: boolean; legs: TieLeg[];
}
export interface Standings { tables: StandingsTable[]; knockout: { stage: string; label: string; ties: KnockoutTie[] }[] }

/** "Round of 16 · 2nd leg" for cup matches; null for ordinary league fixtures. */
export function stageText(g: Pick<SoccerGame, "stage_label" | "leg">, t: T = makeT()): string | null {
  if (!g.stage_label) return null;
  return g.leg ? `${t(g.stage_label)} · ${t(g.leg === 1 ? "1st leg" : "2nd leg")}` : t(g.stage_label);
}

/** "AET" / "4-3 pens" suffix for matches settled after 90 minutes. */
export function decidedText(g: Pick<SoccerGame, "decided" | "shootout">, t: T = makeT()): string | null {
  if (g.decided === "pen" && g.shootout) return t("{h}-{a} pens", { h: g.shootout.home, a: g.shootout.away });
  if (g.decided) return t("AET");
  return null;
}

export interface TableRow extends SoccerTeam {
  position: number; played: number; won: number; drawn: number; lost: number; gf: number; ga: number; gd: number;
  points: number; form: ("W" | "D" | "L")[];
  group?: string;   // split seasons (Ecuador final stage): the group this club plays in; positions are within the group
}

/** Team color as CSS; ESPN colors are hex without '#'. Very light primaries fall back to the alternate color. */
/** A "strong pick": the most likely result is rated at least this likely. Below it the match is close to a coin flip
 *  and we show "no strong pick" (same cut-off as the backend's STRONG_PICK, used for the strong-pick accuracy). */
export const STRONG_PICK = 0.45;

export function isStrongPick(p: { p_home: number; p_draw: number; p_away: number }): boolean {
  return Math.max(p.p_home, p.p_draw, p.p_away) >= STRONG_PICK;
}

/** Double chance (favourite or draw) and draw no bet (favourite, a draw voids it), from the 1X2 probabilities. */
export function saferPicks(p: { p_home: number; p_draw: number; p_away: number }) {
  const fav = p.p_home >= p.p_away ? "home" as const : "away" as const;
  const pf = fav === "home" ? p.p_home : p.p_away;
  return { fav, doubleChance: pf + p.p_draw, drawNoBet: pf / (p.p_home + p.p_away) };
}

export function teamCss(t: { color: string | null; alt_color?: string | null }): string {
  const pick = (h: string | null | undefined) => (h && /^[0-9a-f]{6}$/i.test(h) ? h : null);
  const c = pick(t.color), alt = pick(t.alt_color);
  const light = (h: string) => {
    const n = parseInt(h, 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
    return 0.299 * r + 0.587 * g + 0.114 * b > 200;
  };
  if (c && !light(c)) return `#${c}`;
  if (alt && !light(alt)) return `#${alt}`;
  return "#5f6878";
}

export function kickoffTime(iso: string, lang: Lang = "en"): string {
  return new Intl.DateTimeFormat(intlLocale(lang), { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" }).format(new Date(iso)) + " ET";
}


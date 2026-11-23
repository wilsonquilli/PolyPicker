// NFL response types (backend/polypicker/api/routers/nfl.py) and football helpers.
// Shapes mirror the NBA ones (GameSummary, line markets, outcomes) so shared components work for both.
import { intlLocale, type Lang } from "./i18n";

import type { Dist, Factor, GameLineMarket, GameSummary, Outcome } from "./types";

// Team colors (fallback badges, player avatars). Logos: public/logos/nfl/{ABBR}.webp (256px, trimmed).
export const NFL_COLORS: Record<string, string> = {
  ARI: "#97233F", ATL: "#A71930", BAL: "#241773", BUF: "#00338D", CAR: "#0085CA", CHI: "#0B162A", CIN: "#FB4F14",
  CLE: "#FF3C00", DAL: "#002244", DEN: "#002244", DET: "#0076B6", GB: "#203731", HOU: "#03202F", IND: "#002C5F",
  JAX: "#006778", KC: "#E31837", LA: "#003594", LAC: "#007BC7", LV: "#000000", MIA: "#008E97", MIN: "#4F2683",
  NE: "#002244", NO: "#101820", NYG: "#0B2265", NYJ: "#003F2D", PHI: "#004C54", PIT: "#000000", SEA: "#002244",
  SF: "#AA0000", TB: "#A71930", TEN: "#4495D2", WAS: "#5A1414",
};

export type NflStat = "passing_yards" | "passing_tds" | "rushing_yards" | "receptions" | "receiving_yards" | "scrimmage_tds";

export const STAT_LABEL: Record<NflStat, string> = {
  passing_yards: "Pass yds", passing_tds: "Pass TD", rushing_yards: "Rush yds", receptions: "Receptions",
  receiving_yards: "Rec yds", scrimmage_tds: "Anytime TD",
};
export const STAT_MARKET: Record<NflStat, string> = {
  passing_yards: "player_passing_yards", passing_tds: "player_passing_tds", rushing_yards: "player_rushing_yards",
  receptions: "player_receptions", receiving_yards: "player_receiving_yards", scrimmage_tds: "player_anytime_td",
};
export const STAT_ORDER: NflStat[] = ["passing_yards", "passing_tds", "rushing_yards", "receptions", "receiving_yards", "scrimmage_tds"];

export interface NflGame extends GameSummary {
  week: number;
  game_type: string;
  overtime: boolean | null;
  prediction: (GameSummary["prediction"] & { p_tie?: number | null }) | null;
}

export interface NflWeek {
  season: number;
  week: number;
  game_type: string;
  label: string;
  start_date: string;
  end_date: string;
  games: number;
  final: number;
  scheduled: number;
}

export interface NflTeamStats {
  team_id: number;
  points: number | null;
  plays: number | null;
  pass_attempts: number | null;
  completions: number | null;
  passing_yards: number | null;
  passing_tds: number | null;
  interceptions: number | null;
  sacks: number | null;
  carries: number | null;
  rushing_yards: number | null;
  rushing_tds: number | null;
  fumbles_lost: number | null;
  turnovers: number | null;
  first_downs: number | null;
  penalties: number | null;
  penalty_yards: number | null;
  passing_epa: number | null;
  rushing_epa: number | null;
}

export interface NflForm {
  games: number;
  wins: number;
  losses: number;
  ties: number;
  avg_points: number | null;
  avg_margin: number | null;
  results: { date: string; opponent: string; home: boolean; result: "W" | "L" | "T"; points: number; opponent_points: number }[];
}

export interface NflGameDetail extends NflGame {
  venue: { stadium: string | null; roof: string | null; surface: string | null; neutral_site: boolean };
  div_game: boolean;
  line_score: { team_id: number; q1_points: number | null; q2_points: number | null; q3_points: number | null;
    q4_points: number | null; ot_points: number | null; points: number }[] | null;
  team_stats: NflTeamStats[];
  context: { home: { last5: NflForm; rest_days: number | null }; away: { last5: NflForm; rest_days: number | null };
    head_to_head: { game_id: number; date: string; season: number; home_team_id: number; away_team_id: number;
      home_score: number; away_score: number; winner_team_id: number | null }[] };
}

export interface NflThreshold {
  kind: "at_least" | "over";
  threshold: number;
  label: string;
  probability: number;
  push_probability: number | null;
  complement_probability: number;
  outcome: Outcome | null;
}

export interface NflPlayerMarket {
  label: string;
  expected: number;
  rollout: "ga" | "beta";
  explanation: string[];
  recent: Record<string, number>;
  thresholds_available: boolean;
  distribution: { type: "normal" | "negative_binomial"; mean: number; sd?: number; dispersion?: number };
  outcome: Outcome | null;
  thresholds: NflThreshold[];
}

export interface NflPlayerForecast {
  player_id: string;
  full_name: string | null;
  position: string | null;
  team_id: number;
  availability: { status: "available" | "questionable" | "out"; reason: string | null; source: string } | null;
  markets: Partial<Record<NflStat, NflPlayerMarket>>;
}

export interface NflPredictions {
  game_id: number;
  game_status: string;
  forecast_status: "current" | "stale" | "pending" | "unavailable" | "backtest";
  kind?: "live" | "backtest";
  revision?: number;
  published_at?: string;
  data_cutoff_utc?: string;
  model_versions?: Record<string, string>;
  markets: {
    winner?: { label: string; home: { team_id: number; abbreviation: string; probability: number; outcome: Outcome | null };
      away: { team_id: number; abbreviation: string; probability: number; outcome: Outcome | null };
      home_pct: number; away_pct: number; tie_probability: number | null; factors?: Factor[];
      qb_out?: { home: boolean; away: boolean }; distribution?: Dist };
    projected_score?: { home: number; away: number; home_outcome: Outcome | null; away_outcome: Outcome | null };
    spread?: GameLineMarket;
    total?: GameLineMarket;
  } | null;
  players: NflPlayerForecast[];
  excluded_players?: { player_id: string; full_name: string | null; team_id: number; position: string | null; reason: string; markets: string[] }[];
  revisions?: { kind: string; revision: number; published_at: string }[];
  notes?: { kind: string | null; availability: string; thresholds: string };
}

export interface NflStandingRow {
  team_id: number;
  abbreviation: string;
  name: string;
  full_name: string;
  city: string;
  conference: string;
  division: string;
  wins: number;
  losses: number;
  ties: number;
  games: number;
  win_pct: number | null;
  record: string;
  points_for: number;
  points_against: number;
  point_diff: number;
  div_wins: number;
  div_losses: number;
  games_behind?: number;
}

export function kickoff(iso: string, lang: Lang = "en"): string {
  return new Intl.DateTimeFormat(intlLocale(lang), { weekday: "short", hour: "numeric", minute: "2-digit", timeZone: "America/New_York" })
    .format(new Date(iso)) + " ET";
}

export function weekRange(w: NflWeek, lang: Lang = "en"): string {
  const f = (d: string) => new Intl.DateTimeFormat(intlLocale(lang), { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(`${d}T12:00:00Z`));
  return w.start_date === w.end_date ? f(w.start_date) : `${f(w.start_date)} – ${f(w.end_date)}`;
}

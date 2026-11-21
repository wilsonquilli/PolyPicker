// Response types for the PolyPicker API (see backend/polypicker/api/schemas.py and routers).

export interface Meta {
  generated_at: string;
  attribution: string;
  disclaimer?: string | null;
  total?: number | null;
  limit?: number | null;
  offset?: number | null;
  extra?: Record<string, unknown> | null;
}

export interface Envelope<T> {
  data: T;
  meta: Meta;
}

export interface TeamRef {
  team_id: number;
  abbreviation: string;
  city: string;
  name: string;
  full_name: string;
  conference?: string | null;
  division?: string | null;
}

export interface GameSide extends TeamRef {
  score: number | null;
  record: string | null;
}

export interface PredictionHeadline {
  kind: "live" | "backtest";
  revision: number;
  published_at: string;
  data_cutoff_utc: string;
  p_home_win: number;
  p_away_win: number;
  p_home_pct: number;
  p_away_pct: number;
  projected_home_points: number | null;
  projected_away_points: number | null;
  favorite: "home" | "away";
  favorite_label: "favorite" | "slight favorite" | "toss-up";
}

export type GameStatus = "scheduled" | "live" | "final" | "postponed" | "canceled" | "unknown";

export interface GameSummary {
  game_id: number;
  season: number;
  season_label: string;
  season_type: string;
  is_cup: boolean;
  label: string | null;
  sub_label: string | null;
  tipoff_utc: string;
  game_date: string;
  status: GameStatus;
  home: GameSide;
  away: GameSide;
  winner_team_id: number | null;
  prediction: PredictionHeadline | null;
}

export interface FormResult {
  date: string;
  opponent: string;
  home: boolean;
  result: "W" | "L";
  points: number;
  opponent_points: number;
}

export interface TeamForm {
  games: number;
  wins: number;
  losses: number;
  avg_points: number | null;
  avg_margin: number | null;
  results: FormResult[];
}

export interface SideContext {
  last5: TeamForm;
  last10: TeamForm;
  home_away_record: { home: string; away: string };
  rest_days: number | null;
}

export interface H2HGame {
  game_id: number;
  date: string;
  season: number;
  season_type: string;
  home_team_id: number;
  away_team_id: number;
  home_score: number;
  away_score: number;
  winner_team_id: number;
}

export interface GameDetail extends GameSummary {
  arena: { name: string | null; city: string | null; state: string | null };
  periods: number | null;
  attendance: number | null;
  line_score:
    | { team_id: number; q1_points: number | null; q2_points: number | null; q3_points: number | null;
        q4_points: number | null; ot_points: number | null; points: number }[]
    | null;
  context: { home: SideContext; away: SideContext; head_to_head: H2HGame[] };
}

export interface Outcome {
  state: "pending" | "final" | "void" | "unavailable" | "unevaluable";
  result: "hit" | "miss" | "push" | null;
  observed: number | null;
}

export interface Factor {
  factor: string;
  home_margin_points: number;
  favors: "home" | "away";
}

export interface LineRow {
  line: number;
  side: string | null;
  label: string;
  probability: number;
  push_probability: number | null;
  complement_probability: number;
  outcome: Outcome | null;
}

export interface ThresholdRow {
  kind: "at_least" | "over";
  threshold: number;
  label: string;
  probability: number;
  push_probability: number | null;
  complement_probability: number;
  outcome: Outcome | null;
}

export interface Availability {
  status: "available" | "uncertain" | "doubtful" | "likely_out";
  reason: string | null;
  source: string;
}

export interface StatMarket {
  expected?: number;
  rollout?: "ga" | "beta";
  explanation?: string[];
  recent?: Record<string, number>;
  thresholds_available?: boolean;
  outcome?: Outcome | null;
  thresholds: ThresholdRow[];
}

export interface Dist {
  type: "normal";
  mean: number;
  sd: number;
}

export interface GameLineMarket {
  projection: number;
  projection_label: string;
  probabilities_available: boolean;
  lines: LineRow[];
  projection_outcome: Outcome | null;
  distribution?: Dist;
}

export interface PlayerForecast {
  player_id: number;
  full_name: string | null;
  position: string | null;
  team_id?: number;
  availability?: Availability;
  markets: Partial<Record<"points" | "assists" | "rebounds", StatMarket>>;
}

export interface GamePredictions {
  game_id: number;
  game_status?: GameStatus;
  forecast_status: "current" | "stale" | "pending" | "unavailable" | "backtest";
  kind?: "live" | "backtest";
  revision?: number;
  published_at?: string;
  data_cutoff_utc?: string;
  model_versions?: Record<string, string>;
  markets: {
    winner?: {
      label: string;
      home: { team_id: number; abbreviation: string; probability: number; outcome: Outcome | null };
      away: { team_id: number; abbreviation: string; probability: number; outcome: Outcome | null };
      home_pct: number;
      away_pct: number;
      factors?: Factor[];
      distribution?: Dist;
    };
    projected_score?: { home: number; away: number };
    spread?: GameLineMarket;
    total?: GameLineMarket;
  } | null;
  players: PlayerForecast[];
  excluded_players?: { player_id: number; full_name: string | null; market: string; reason: string }[];
  revisions?: { kind: string; revision: number; published_at: string }[];
  notes?: Record<string, string | null>;
}

export interface CustomLineResult {
  label: string;
  expected: number;
  p_hit: number;
  p_push: number;
  p_miss: number;
}

export interface StandingRow extends TeamRef {
  wins: number;
  losses: number;
  points_per_game: number | null;
  opp_points_per_game: number | null;
  avg_margin: number | null;
  games_behind?: number;
  win_pct?: number | null;
}

export interface SearchResult {
  type: "team" | "player";
  id: number;
  name: string;
  subtitle: string | null;
}

export interface PlayerAgg {
  games_played: number;
  minutes: number | null;
  points: number | null;
  assists: number | null;
  rebounds: number | null;
  fg_pct: number | null;
  fg3_pct: number | null;
  ft_pct: number | null;
  games_missed: number;
}

export interface GameLogRow {
  game_id: number;
  date: string;
  season_type: string;
  team: string;
  opponent: string;
  is_home: boolean;
  result: "W" | "L";
  participation: "played" | "dnp" | "inactive" | "unknown";
  status_comment: string | null;
  starter: boolean | null;
  minutes: number | null;
  points: number | null;
  assists: number | null;
  rebounds: number | null;
  fgm: number | null;
  fga: number | null;
  fg3m: number | null;
  fg3a: number | null;
  ftm: number | null;
  fta: number | null;
  plus_minus: number | null;
}

// Plain-language reasons behind a forecast. The API sends codes + params (backend api/reasons.py); this file phrases
// them in the page language. Plain module (no hooks) so server components can call it.

import type { Lang, T } from "./i18n";

export interface Reason {
  code: string;
  side: "home" | "away" | null; // team the reason is about
  favors: "home" | "away" | "draw" | null; // side it helps (null = neutral, e.g. weather)
  params: Record<string, unknown>;
}

export interface ReasonTeams {
  home: string;
  away: string;
}

function ordinal(n: number, lang: Lang): string {
  if (lang === "es") return `${n}.º`;
  const s = n % 100 >= 11 && n % 100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th";
  return `${n}${s}`;
}


function names(list: unknown, n: unknown, t: T): string {
  const xs = Array.isArray(list) ? (list as string[]) : [];
  const total = Number(n) || xs.length;
  const base = xs.length > 1 ? `${xs.slice(0, -1).join(", ")} ${t("and")} ${xs[xs.length - 1]}` : xs[0] ?? "";
  return total > xs.length ? `${base} ${t("(+{n} more)", { n: total - xs.length })}` : base;
}

/** One reason as a sentence, or null for a code this version of the site doesn't know. */
export function reasonText(r: Reason, teams: ReasonTeams, t: T, lang: Lang): string | null {
  const team = r.side ? teams[r.side] : "";
  const opp = r.side ? teams[r.side === "home" ? "away" : "home"] : "";
  const p = r.params;
  switch (r.code) {
    case "missing_players": return t("{team} will be without {players}", { team, players: names(p.names, p.n, t) });
    case "qb_out": return t("{team}'s starting quarterback is out", { team });
    case "form_hot": return t("{team}: won {w} of the last {n}", { team, w: Number(p.w), n: Number(p.n) });
    case "form_cold": return t("{team}: won only {w} of the last {n}", { team, w: Number(p.w), n: Number(p.n) });
    case "form_soccer_hot": return t("{team}: won {w} of the last 5 ({form})", { team, w: Number(p.w), form: String(p.form) });
    case "form_soccer_cold": return t("{team}: no wins in the last 5 ({form})", { team, form: String(p.form) });
    case "back_to_back": return t("Second night of a back-to-back for {team}", { team });
    case "off_bye": return t("Coming off a bye week: {team}", { team });
    case "short_week": return t("Short week for {team} ({days} days of rest)", { team, days: Number(p.days) });
    case "table_gap": return t("In the table: {team} {pos}, {opp} {opp_pos}", { team, opp, pos: ordinal(Number(p.pos), lang), opp_pos: ordinal(Number(p.opp_pos), lang) });
    case "congestion": return t("Less rest for {team}: played {days} days ago, {opp} had longer", { team, opp, days: Number(p.days) });
    case "snow": return t("Snow in the forecast, which usually means fewer points");
    case "wind": return t("Strong wind expected ({kmh} km/h), which usually means fewer points", { kmh: Number(p.kmh) });
    case "cold": return t("Freezing at kickoff ({c}°C)", { c: Number(p.c) });
    case "rain": return t("Rain expected at kickoff");
    case "home_court": return t("Home court for {team}", { team });
    case "home_field": return t("Home advantage for {team}", { team });
    case "neutral_site": return t("Neutral venue: no home advantage");
    default: return null;
  }
}

/** How a reason relates to the pick: backs it, goes against it, or neutral context. */
export function stance(r: Reason, pick: "home" | "away" | "draw" | null): "for" | "against" | "neutral" {
  if (r.favors === null || pick === null) return "neutral";
  return r.favors === pick ? "for" : "against";
}

/** The headline reason for a card: the first one backing the pick that isn't just home advantage. */
export function topReason(rs: Reason[] | null | undefined, pick: "home" | "away" | "draw" | null): Reason | null {
  if (!rs?.length) return null;
  const venue = new Set(["home_court", "home_field", "neutral_site"]);
  return rs.find((r) => stance(r, pick) === "for" && !venue.has(r.code)) ?? rs.find((r) => !venue.has(r.code)) ?? null;
}

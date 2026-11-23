// Formatting helpers. Game times are shown in US-Eastern, the NBA's reference time zone,
// which also keeps server and client output identical (no hydration mismatch).

import { intlLocale, type Lang } from "@/lib/i18n";

const ET = "America/New_York";
// Dates follow the page language ("Nov 22" / "22 nov"); the last argument is the language, English by default.
const L = (lang: Lang) => intlLocale(lang);

export function tipoffTime(iso: string, lang: Lang = "en"): string {
  return new Intl.DateTimeFormat(L(lang), { hour: "numeric", minute: "2-digit", timeZone: ET }).format(new Date(iso)) + " ET";
}

export function longDate(isoDate: string, lang: Lang = "en"): string {
  // isoDate is a calendar date (YYYY-MM-DD); format at noon UTC to avoid TZ edge cases.
  return new Intl.DateTimeFormat(L(lang), { weekday: "long", month: "long", day: "numeric", timeZone: "UTC" })
    .format(new Date(`${isoDate}T12:00:00Z`));
}

export function shortDate(isoDate: string, lang: Lang = "en"): string {
  return new Intl.DateTimeFormat(L(lang), { month: "short", day: "numeric", timeZone: "UTC" })
    .format(new Date(`${isoDate}T12:00:00Z`));
}

export function weekday(isoDate: string, lang: Lang = "en"): string {
  return new Intl.DateTimeFormat(L(lang), { weekday: "short", timeZone: "UTC" }).format(new Date(`${isoDate}T12:00:00Z`));
}

export function dateTimeET(iso: string, lang: Lang = "en"): string {
  return new Intl.DateTimeFormat(L(lang), { month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    timeZone: ET }).format(new Date(iso)) + " ET";
}

export function addDays(isoDate: string, n: number): string {
  const d = new Date(`${isoDate}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Probability (0-1) -> "62%"; values within 0.5% of 0/100 shown as "<1%" / ">99%". */
export function pct(p: number | null | undefined, digits = 0): string {
  if (p === null || p === undefined || Number.isNaN(p)) return "—";
  const v = p * 100;
  if (v < 0.5) return "<1%";
  if (v > 99.5) return ">99%";
  return `${v.toFixed(digits)}%`;
}

export function signed(n: number, digits = 1): string {
  const s = n.toFixed(digits);
  return n > 0 ? `+${s}` : s;
}

export function num(n: number | null | undefined, digits = 1): string {
  return n === null || n === undefined ? "—" : Number(n).toFixed(digits);
}

export function seasonTypeLabel(t: string): string {
  return ({ regular: "Regular season", playoffs: "Playoffs", play_in: "Play-In", cup_final: "NBA Cup Final",
    preseason: "Preseason", all_star: "All-Star" } as Record<string, string>)[t] ?? t;
}

/**
 * Translations. English text is the key: `t("Win probability")` returns the Spanish entry when the page is in
 * Spanish, and the English text itself when a phrase has no entry yet (so nothing ever renders blank).
 * Placeholders: `t("{n} games", { n: 5 })`.
 *
 * The Spanish dictionary (es.json) is passed in rather than imported here, so it stays out of the shared browser
 * bundle: Server Components get it from `@/lib/i18n/server`, the browser only receives it when the page is in
 * Spanish (via <LangProvider dict>). English needs no dictionary at all.
 */
import { translatePattern } from "./patterns";

export const LANGS = ["en", "es"] as const;
export type Lang = (typeof LANGS)[number];
export type Dict = Record<string, string>;
export const isLang = (x: unknown): x is Lang => x === "en" || x === "es";
export type Vars = Record<string, string | number>;
export type T = (s: string, vars?: Vars) => string;

export function interpolate(s: string, vars?: Vars): string {
  return vars ? s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m)) : s;
}

/** `dict` null = English. */
export function translate(dict: Dict | null, s: string, vars?: Vars): string {
  if (!dict) return interpolate(s, vars);
  return interpolate(dict[s] ?? translatePattern(s, dict) ?? s, vars);
}

export const makeT = (dict: Dict | null = null): T => (s, vars) => translate(dict, s, vars);

/** Intl locale for numbers and dates. */
export const intlLocale = (lang: Lang) => (lang === "es" ? "es-ES" : "en-US");

/** Context-specific strings: `keyed(t, "status:scheduled", "Upcoming")` uses the "status:scheduled" entry in Spanish
 *  and the given English otherwise (for words whose Spanish depends on where they appear). */
export const keyed = (t: T, key: string, en: string): string => {
  const v = t(key);
  return v === key ? en : v;
};

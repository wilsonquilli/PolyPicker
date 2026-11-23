import "server-only";

import { lang as rootLang } from "next/root-params";

import { ES } from "./es";
import { isLang, makeT, type Dict, type Lang, type T } from "./index";

export const dictFor = (lang: Lang): Dict | null => (lang === "es" ? ES : null);
const T_EN = makeT(null), T_ES = makeT(ES);
export const tFor = (lang: Lang): T => (lang === "es" ? T_ES : T_EN);

/** Current page language in any Server Component (no prop drilling). */
export async function getLang(): Promise<Lang> {
  const l = await rootLang();
  return isLang(l) ? l : "en";
}

export async function getT(): Promise<T> {
  return tFor(await getLang());
}

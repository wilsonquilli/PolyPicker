"use client";

import { useRouter } from "next/navigation";
import { createContext, useContext, useMemo, useTransition } from "react";

import { makeT, type Dict, type Lang, type T } from "@/lib/i18n";

const LangContext = createContext<{ lang: Lang; dict: Dict | null }>({ lang: "en", dict: null });

/** `dict`: the Spanish dictionary, sent by the layout only when the page is in Spanish (null for English). */
export function LangProvider({ lang, dict, children }: { lang: Lang; dict: Dict | null; children: React.ReactNode }) {
  const value = useMemo(() => ({ lang, dict }), [lang, dict]);
  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export const useLang = () => useContext(LangContext).lang;

export function useT(): T {
  const { dict } = useContext(LangContext);
  return useMemo(() => makeT(dict), [dict]);
}

/**
 * Switch language without reloading the page: remember the choice (proxy.ts reads the cookie) and refresh the
 * server-rendered content in place. `router.refresh()` also drops the client cache, so pages visited afterwards
 * come back in the new language. Returns [pending, switch].
 */
export function useSwitchLanguage(): [boolean, (lang: Lang) => void] {
  const router = useRouter();
  const [pending, start] = useTransition();
  return [pending, (lang: Lang) => {
    document.cookie = `pp-lang=${lang}; path=/; max-age=${60 * 60 * 24 * 365}; samesite=lax`;
    start(() => router.refresh());
  }];
}

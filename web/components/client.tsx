"use client";

import { useRouter } from "next/navigation";
import { useEffect, useLayoutEffect, useState, useSyncExternalStore } from "react";

import { useLang, useT } from "@/components/i18n";
import { intlLocale, type Lang } from "@/lib/i18n";

/** "updated 2h ago". Server renders the absolute ET time; the client swaps in a relative one. */
export function RelativeTime({ iso, prefix = "" }: { iso: string; prefix?: string }) {
  const lang = useLang();
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    const update = () => setText(relative(iso, lang));
    update();
    const id = setInterval(update, 60_000);
    return () => clearInterval(id);
  }, [iso, lang]);
  const abs = new Intl.DateTimeFormat(intlLocale(lang), { month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    timeZone: "America/New_York" }).format(new Date(iso)) + " ET";
  return (
    <time dateTime={iso} title={abs} suppressHydrationWarning>
      {prefix}{text ?? abs}
    </time>
  );
}

function relative(iso: string, lang: Lang): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  const es = lang === "es";
  if (s < 0) {
    const m = -s / 60;
    const v = m < 60 ? `${Math.round(m)}m` : m < 60 * 24 ? `${Math.round(m / 60)}h` : `${Math.round(m / 1440)}d`;
    return es ? `en ${v}` : `in ${v}`;
  }
  if (s < 60) return es ? "justo ahora" : "just now";
  const v = s < 3600 ? `${Math.round(s / 60)}m` : s < 86400 ? `${Math.round(s / 3600)}h` : `${Math.round(s / 86400)}d`;
  return es ? `hace ${v}` : `${v} ago`;
}

/** Re-renders the server page on an interval (scores and statuses), only while the tab is visible. */
export function AutoRefresh({ seconds }: { seconds: number }) {
  const router = useRouter();
  useEffect(() => {
    const tick = () => { if (document.visibilityState === "visible") router.refresh(); };
    const id = setInterval(tick, seconds * 1000);
    document.addEventListener("visibilitychange", tick);
    return () => { clearInterval(id); document.removeEventListener("visibilitychange", tick); };
  }, [router, seconds]);
  return null;
}

const noSubscribe = () => () => {};
/**
 * Inline script that must run before first paint (theme). Rendered in the server HTML and during hydration only;
 * afterwards it renders nothing, so client re-renders of the root layout (language switch) never re-insert a
 * <script> element (React warns about those, and the code has already run).
 */
export function BootScript({ code }: { code: string }) {
  const fromServer = useSyncExternalStore(noSubscribe, () => false, () => true);
  return fromServer ? <script dangerouslySetInnerHTML={{ __html: code }} /> : null;
}

/**
 * Keeps the saved light/dark theme when the root layout re-renders on the client. Switching language refreshes the
 * page in place, and React then resets <html className> to the server's value, which never includes "dark" (the
 * theme lives in localStorage, applied by the boot script). Re-apply it before paint whenever the language changes.
 */
export function ThemeSync() {
  const lang = useLang();
  useLayoutEffect(() => {
    try {
      const saved = localStorage.getItem("pp-theme");
      const dark = saved === "dark" || (!saved && matchMedia("(prefers-color-scheme: dark)").matches);
      document.documentElement.classList.toggle("dark", dark);
    } catch {}
  }, [lang]);
  return null;
}

/** Floating "back to top" button on long pages; sits above the phone tab bar. */
export function BackToTop() {
  const t = useT();
  const [show, setShow] = useState(false);
  useEffect(() => {
    const onScroll = () => setShow(window.scrollY > 1400);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  return (
    <button type="button" aria-label={t("Back to top")} onClick={() => window.scrollTo({ top: 0 })}
      className={`fixed right-4 z-30 flex h-11 w-11 items-center justify-center rounded-full border border-line bg-surface/95 text-muted shadow-lg backdrop-blur transition-all duration-200 hover:text-ink bottom-[calc(76px+env(safe-area-inset-bottom))] md:bottom-6 ${
        show ? "translate-y-0 opacity-100" : "pointer-events-none translate-y-3 opacity-0"}`}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden><path d="M12 19V5M5 12l7-7 7 7" /></svg>
    </button>
  );
}

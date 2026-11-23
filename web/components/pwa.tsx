"use client";

import Image from "next/image";
import Link from "next/link";

import { useT } from "@/components/i18n";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useState, useSyncExternalStore } from "react";

import { SOCCER_LEAGUES, soccerSlugOf } from "@/lib/soccer";

// ------------------------------------------------------------------ environment detection

type BeforeInstallPromptEvent = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: "accepted" | "dismissed" }> };

const noop = () => () => {};
/** true only after hydration (avoids server/client mismatches for browser-only values). */
export function useMounted() {
  return useSyncExternalStore(noop, () => true, () => false);
}

function subscribeDisplayMode(cb: () => void) {
  const mq = window.matchMedia("(display-mode: standalone)");
  mq.addEventListener("change", cb);
  return () => mq.removeEventListener("change", cb);
}
/** Running as an installed app (home-screen launch) rather than in a browser tab. */
export function useIsStandalone() {
  return useSyncExternalStore(
    subscribeDisplayMode,
    () => window.matchMedia("(display-mode: standalone)").matches || (navigator as Navigator & { standalone?: boolean }).standalone === true,
    () => false,
  );
}

export type Platform = "ios" | "android" | "desktop";
export function detectPlatform(): Platform {
  const ua = navigator.userAgent;
  const iPadOS = navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1;
  if (/iPhone|iPad|iPod/i.test(ua) || iPadOS) return "ios";
  if (/Android/i.test(ua)) return "android";
  return "desktop";
}

function subscribeOnline(cb: () => void) {
  window.addEventListener("online", cb);
  window.addEventListener("offline", cb);
  return () => { window.removeEventListener("online", cb); window.removeEventListener("offline", cb); };
}
export function useOnline() {
  return useSyncExternalStore(subscribeOnline, () => navigator.onLine, () => true);
}

// The browser's install prompt (Chrome/Edge/Samsung on Android and desktop) is captured once,
// globally, so any component can offer an "Install" button. Safari/iOS never fires it.
let deferred: BeforeInstallPromptEvent | null = null;
const listeners = new Set<() => void>();
if (typeof window !== "undefined") {
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferred = e as BeforeInstallPromptEvent;
    listeners.forEach((l) => l());
  });
  window.addEventListener("appinstalled", () => {
    deferred = null;
    listeners.forEach((l) => l());
  });
}
function subscribeInstall(cb: () => void) {
  listeners.add(cb);
  return () => { listeners.delete(cb); };
}
export function useInstallPrompt() {
  const available = useSyncExternalStore(subscribeInstall, () => deferred !== null, () => false);
  const install = async () => {
    if (!deferred) return "unavailable" as const;
    await deferred.prompt();
    const { outcome } = await deferred.userChoice;
    deferred = null;
    listeners.forEach((l) => l());
    return outcome;
  };
  return { available, install };
}

// ------------------------------------------------------------------ service worker

/** Registers /sw.js in production builds (in dev it would fight hot reloading). */
export function ServiceWorkerRegistrar() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production" || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" }).catch(() => {});
  }, []);
  return null;
}

// ------------------------------------------------------------------ offline banner

export function OfflineBanner() {
  const t = useT();
  const online = useOnline();
  if (online) return null;
  return (
    <div role="status" className="bg-warn-soft px-4 py-2 text-center text-xs font-medium text-warn">
      {t("You’re offline. Showing the last saved copy of this page; forecasts and scores may be out of date.")}
    </div>
  );
}

// ------------------------------------------------------------------ install banner

const DISMISS_KEY = "pp-install-dismissed";
const DISMISS_DAYS = 14;

function recentlyDismissed(): boolean {
  try {
    return Date.now() - Number(localStorage.getItem(DISMISS_KEY) ?? 0) < DISMISS_DAYS * 864e5;
  } catch {
    return false;
  }
}

function ShareIcon() {
  const t = useT();
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="inline -mt-0.5" aria-label={t("Share")}>
      <path d="M12 3v12M7 8l5-5 5 5" /><path d="M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7" />
    </svg>
  );
}

/** Small, dismissible prompt on phones. Android: real Install button. iOS: Add-to-Home-Screen steps. */
export function InstallBanner() {
  const t = useT();
  const mounted = useMounted();
  const standalone = useIsStandalone();
  const { available, install } = useInstallPrompt();
  const [dismissedNow, setDismissedNow] = useState(false);
  const dismissedBefore = useSyncExternalStore(noop, recentlyDismissed, () => true);
  if (!mounted || standalone || dismissedNow || dismissedBefore) return null;
  const platform = detectPlatform();
  if (platform === "desktop") return null;
  if (platform === "android" && !available) return null; // browser hasn't offered install (yet)
  const dismiss = () => {
    try { localStorage.setItem(DISMISS_KEY, String(Date.now())); } catch {}
    setDismissedNow(true);
  };
  return (
    <div className="fixed inset-x-3 bottom-[calc(76px+env(safe-area-inset-bottom))] z-50 md:hidden" role="dialog" aria-label={t("Install PolyPicker")}>
      <div className="flex items-center gap-3 rounded-2xl border border-line bg-surface p-3 shadow-lg">
        <Image src="/icons/icon-192.png" alt="" width={40} height={40} unoptimized className="rounded-xl border border-line" />
        <div className="min-w-0 flex-1 text-sm">
          <div className="font-semibold">{t("Get the PolyPicker app")}</div>
          {platform === "ios" ? (
            <div className="text-xs text-muted">{t("Tap")} <ShareIcon /> {t("then")} <b>{t("Add to Home Screen")}</b></div>
          ) : (
            <div className="text-xs text-muted">{t("Free · no app store needed")}</div>
          )}
        </div>
        {platform === "android" && (
          <button onClick={async () => { if ((await install()) !== "unavailable") dismiss(); }}
            className="rounded-xl bg-accent px-3 py-2 text-sm font-semibold text-accent-fg">{t("Install")}</button>
        )}
        <button onClick={dismiss} aria-label={t("Dismiss")} className="rounded-lg p-1.5 text-muted hover:bg-surface-2">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><path d="M6 6l12 12M18 6 6 18" /></svg>
        </button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ bottom tab bar (phones)

const ICON = {
  week: <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M3 10h18M8 3v4M16 3v4" /></>,
  standings: <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />,
  teams: <><circle cx="9" cy="8" r="3" /><path d="M3 20a6 6 0 0 1 12 0" /><circle cx="17" cy="9" r="2.5" /><path d="M15.5 14.5A5 5 0 0 1 21 19" /></>,
  accuracy: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1.5" /></>,
  more: <><circle cx="5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="19" cy="12" r="1.6" /></>,
};
const HOME_TABS = [
  { href: "/", label: "Home", icon: <path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" /> },
  { href: "/nba", label: "NBA", icon: <><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3v18M5.6 5.6c3 3 3 9.8 0 12.8M18.4 5.6c-3 3-3 9.8 0 12.8" /></> },
  { href: "/nfl", label: "NFL", icon: <><ellipse cx="12" cy="12" rx="9.5" ry="6" transform="rotate(-45 12 12)" /><path d="M9 15l6-6M10.5 10.5l1 1M12.5 8.5l1 1M8.5 12.5l1 1" /></> },
  { href: "/soccer", label: "Soccer", icon: <><circle cx="12" cy="12" r="9" /><path d="M12 7.5l3.8 2.8-1.5 4.4H9.7l-1.5-4.4zM12 3v4.5M15.8 10.3 20.5 9M14.3 14.7l2.5 4.4M9.7 14.7l-2.5 4.4M8.2 10.3 3.5 9" /></> },
  { href: "/more", label: "More", icon: ICON.more },
];

const NFL_TABS = [
  { href: "/nfl", label: "This week", icon: ICON.week },
  { href: "/nfl/standings", label: "Standings", icon: ICON.standings },
  { href: "/nfl/teams", label: "Teams", icon: ICON.teams },
  { href: "/nfl/accuracy", label: "Accuracy", icon: ICON.accuracy },
  { href: "/more", label: "More", icon: ICON.more },
];

const soccerTabs = (base: string) => [
  { href: base, label: "Matches", icon: ICON.week },
  { href: `${base}/table`, label: SOCCER_LEAGUES[soccerSlugOf(base)].kind === "cup" ? "Standings" : "Table", icon: ICON.standings },
  { href: `${base}/teams`, label: "Clubs", icon: ICON.teams },
  { href: `${base}/accuracy`, label: "Accuracy", icon: ICON.accuracy },
  { href: "/more", label: "More", icon: ICON.more },
];

const TABS = [
  { href: "/nba", view: "today", label: "Today", icon: <path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" /> },
  { href: "/nba?view=upcoming", view: "upcoming", label: "Upcoming", icon: <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M3 10h18M8 3v4M16 3v4" /></> },
  { href: "/nba?view=results", view: "results", label: "Results", icon: <><path d="M9 11l3 3 8-8" /><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9" /></> },
  { href: "/nba/standings", label: "Standings", icon: <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /> },
  { href: "/more", label: "More", icon: <><circle cx="5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="19" cy="12" r="1.6" /></> },
];

export function BottomNav() {
  const tr = useT();
  const path = usePathname();
  const view = useSearchParams().get("view") ?? "today";
  const nfl = path.startsWith("/nfl"), soccer = path.startsWith("/soccer");
  const home = path === "/";
  const soccerBase = `/soccer/${soccerSlugOf(path)}`;
  const hub = path === "/soccer";
  const tabs = home || hub ? HOME_TABS : nfl ? NFL_TABS : soccer ? soccerTabs(soccerBase) : TABS;
  const isActive = (t: { href: string; view?: string }) => home || hub ? t.href === path : soccer
    ? (t.href === soccerBase ? path === soccerBase || path.startsWith(`${soccerBase}/games`) : path.startsWith(t.href))
    : nfl
    ? (t.href === "/nfl" ? path === "/nfl" || path.startsWith("/nfl/games") || path.startsWith("/nfl/players")
      : t.href === "/more" ? path.startsWith("/more") : path.startsWith(t.href))
    : t.view ? path === "/nba" && view === t.view
      : t.href === "/more" ? ["/more", "/nba/teams", "/nba/value", "/nba/accuracy", "/nba/players", "/methodology", "/install"].some((p) => path.startsWith(p))
        : path.startsWith(t.href);
  return (
    <nav aria-label={tr("Primary")} className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-bg/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
      <ul className="grid grid-cols-5">
        {tabs.map((tm) => {
          const active = isActive(tm);
          return (
            <li key={tm.label}>
              <Link href={tm.href} aria-current={active ? "page" : undefined}
                className={`flex h-[60px] flex-col items-center justify-center gap-0.5 text-[11px] font-medium ${active ? "text-accent" : "text-muted"}`}>
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>{tm.icon}</svg>
                {tr(tm.label)}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

// ------------------------------------------------------------------ install button (install page / header)

export function InstallButton({ className = "" }: { className?: string }) {
  const t = useT();
  const mounted = useMounted();
  const standalone = useIsStandalone();
  const { available, install } = useInstallPrompt();
  if (!mounted) return null;
  if (standalone) return <p className={`text-sm font-medium text-win ${className}`}>✓ {t("You’re using the installed app.")}</p>;
  if (!available) return null;
  return (
    <button onClick={() => install()} className={`rounded-xl bg-accent px-4 py-2.5 text-sm font-semibold text-accent-fg ${className}`}>
      {t("Install PolyPicker")}
    </button>
  );
}

export function GetAppLink() {
  const t = useT();
  const standalone = useIsStandalone();
  if (standalone) return null;
  return (
    <Link href="/install" className="hidden items-center gap-1.5 rounded-lg px-3 py-1.5 font-medium text-accent hover:bg-accent-soft sm:flex">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><path d="M12 3v12M7 10l5 5 5-5M5 21h14" /></svg>
      {t("Get the app")}
    </Link>
  );
}

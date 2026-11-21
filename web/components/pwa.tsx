"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useState, useSyncExternalStore } from "react";

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
  const online = useOnline();
  if (online) return null;
  return (
    <div role="status" className="bg-warn-soft px-4 py-2 text-center text-xs font-medium text-warn">
      You’re offline. Showing the last saved copy of this page; forecasts and scores may be out of date.
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
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="inline -mt-0.5" aria-label="Share">
      <path d="M12 3v12M7 8l5-5 5 5" /><path d="M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7" />
    </svg>
  );
}

/** Small, dismissible prompt on phones. Android: real Install button. iOS: Add-to-Home-Screen steps. */
export function InstallBanner() {
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
    <div className="fixed inset-x-3 bottom-[calc(76px+env(safe-area-inset-bottom))] z-50 md:hidden" role="dialog" aria-label="Install PolyPicker">
      <div className="flex items-center gap-3 rounded-2xl border border-line bg-surface p-3 shadow-lg">
        <Image src="/icons/icon-192.png" alt="" width={40} height={40} className="rounded-xl" />
        <div className="min-w-0 flex-1 text-sm">
          <div className="font-semibold">Get the PolyPicker app</div>
          {platform === "ios" ? (
            <div className="text-xs text-muted">Tap <ShareIcon /> then <b>Add to Home Screen</b></div>
          ) : (
            <div className="text-xs text-muted">Free · no app store needed</div>
          )}
        </div>
        {platform === "android" && (
          <button onClick={async () => { if ((await install()) !== "unavailable") dismiss(); }}
            className="rounded-xl bg-accent px-3 py-2 text-sm font-semibold text-accent-fg">Install</button>
        )}
        <button onClick={dismiss} aria-label="Dismiss" className="rounded-lg p-1.5 text-muted hover:bg-surface-2">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><path d="M6 6l12 12M18 6 6 18" /></svg>
        </button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ bottom tab bar (phones)

const TABS = [
  { href: "/", view: "today", label: "Today", icon: <path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" /> },
  { href: "/?view=upcoming", view: "upcoming", label: "Upcoming", icon: <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M3 10h18M8 3v4M16 3v4" /></> },
  { href: "/?view=results", view: "results", label: "Results", icon: <><path d="M9 11l3 3 8-8" /><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9" /></> },
  { href: "/standings", label: "Standings", icon: <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /> },
  { href: "/more", label: "More", icon: <><circle cx="5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="19" cy="12" r="1.6" /></> },
];

export function BottomNav() {
  const path = usePathname();
  const view = useSearchParams().get("view") ?? "today";
  return (
    <nav aria-label="Primary" className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-bg/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
      <ul className="grid grid-cols-5">
        {TABS.map((t) => {
          const active = t.view ? path === "/" && view === t.view
            : t.href === "/more" ? ["/more", "/teams", "/accuracy", "/methodology", "/install", "/players"].some((p) => path.startsWith(p))
            : path.startsWith(t.href);
          return (
            <li key={t.label}>
              <Link href={t.href} aria-current={active ? "page" : undefined}
                className={`flex h-[60px] flex-col items-center justify-center gap-0.5 text-[11px] font-medium ${active ? "text-accent" : "text-muted"}`}>
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>{t.icon}</svg>
                {t.label}
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
  const mounted = useMounted();
  const standalone = useIsStandalone();
  const { available, install } = useInstallPrompt();
  if (!mounted) return null;
  if (standalone) return <p className={`text-sm font-medium text-win ${className}`}>✓ You’re using the installed app.</p>;
  if (!available) return null;
  return (
    <button onClick={() => install()} className={`rounded-xl bg-accent px-4 py-2.5 text-sm font-semibold text-accent-fg ${className}`}>
      Install PolyPicker
    </button>
  );
}

export function GetAppLink() {
  const standalone = useIsStandalone();
  if (standalone) return null;
  return (
    <Link href="/install" className="hidden items-center gap-1.5 rounded-lg px-3 py-1.5 font-medium text-accent hover:bg-accent-soft sm:flex">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><path d="M12 3v12M7 10l5 5 5-5M5 21h14" /></svg>
      Get the app
    </Link>
  );
}

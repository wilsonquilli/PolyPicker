"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

/**
 * Launch screen for the web (iOS and Android installed apps get their own from public/splash and the manifest; this
 * one looks the same, so an installed app hands off seamlessly). Server-rendered, so it covers the page from the
 * first paint, then fades out once the app is ready: at most once per browser session.
 *
 * - The boot script (layout) marks the session before paint, so repeat page loads hide it via CSS
 *   (`pp-splash-seen`) even if this component's JavaScript never loads.
 * - If JavaScript never runs, a CSS animation fades it out anyway (never traps the page).
 */
const MIN_MS = 900;          // from navigation start: long enough to read, short enough not to annoy
const MIN_MS_INSTALLED = 400; // the phone already showed its own launch screen
const FADE_MS = 380;

// Read once per page load (before the effect below marks the session), plus "already shown during this page load":
// a later client-only remount of the layout (language switch) must never show it again.
// The boot script in the layout marks the session before paint and sets window.__ppSplashFirst on the first load.
let seenAtLoad = false;
try { seenAtLoad = typeof window !== "undefined" && !(window as { __ppSplashFirst?: number }).__ppSplashFirst; } catch {}
let shownThisLoad = false;
const noSubscribe = () => () => {};

export function Splash() {
  // Hydration uses the server value (not seen) so the markup matches; client-only mounts read the real value.
  const skip = useSyncExternalStore(noSubscribe, () => seenAtLoad || shownThisLoad, () => false);
  const [phase, setPhase] = useState<"in" | "out" | "gone">("in");
  useEffect(() => {
    if (skip) return;
    const seen = seenAtLoad;
    shownThisLoad = true;
    const installed = matchMedia("(display-mode: standalone)").matches;
    const hold = seen ? 0 : Math.max(0, (installed ? MIN_MS_INSTALLED : MIN_MS) - performance.now());
    const t1 = setTimeout(() => setPhase(seen ? "gone" : "out"), hold);
    const t2 = setTimeout(() => setPhase("gone"), hold + FADE_MS);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, [skip]);
  if (skip || phase === "gone") return null;
  return (
    <div id="pp-splash" aria-hidden data-phase={phase}
      className="pp-splash fixed inset-0 z-[100] flex flex-col items-center justify-center gap-7 bg-[#021022]">
      <div className="pp-splash-glow pointer-events-none absolute h-[60vmin] w-[60vmin] rounded-full" />
      {/* eslint-disable-next-line @next/next/no-img-element -- must paint with the HTML, before any JS */}
      <img src="/brand/logo-dark.png" alt="" width={572} height={144} className="pp-splash-logo relative w-[min(62vw,300px)]" />
      <div className="pp-splash-bar relative h-[3px] w-[min(36vw,150px)] overflow-hidden rounded-full bg-white/10">
        <span className="absolute inset-y-0 w-2/5 rounded-full bg-[#1f6ffc]" />
      </div>
    </div>
  );
}

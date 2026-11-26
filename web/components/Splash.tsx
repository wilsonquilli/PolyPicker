"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
const MIN_MS = 900;         
const MIN_MS_INSTALLED = 400; 
const FADE_MS = 380;
let seenAtLoad = false;
try { seenAtLoad = typeof window !== "undefined" && !(window as { __ppSplashFirst?: number }).__ppSplashFirst; } catch {}
let shownThisLoad = false;
const noSubscribe = () => () => {};

export function Splash() {
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

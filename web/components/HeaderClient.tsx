"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { useLang, useSwitchLanguage, useT } from "@/components/i18n";
import { LeagueFlag } from "@/components/LeagueFlag";
import { SPORTS } from "@/lib/sports";
import { LEAGUE_SLUGS, REGIONS, SOCCER_LEAGUES, soccerSlugOf, type LeagueSlug } from "@/lib/soccer";

const NBA_SECTIONS = [
  { href: "/nba", view: "today", label: "Today" },
  { href: "/nba?view=tomorrow", view: "tomorrow", label: "Tomorrow" },
  { href: "/nba?view=upcoming", view: "upcoming", label: "Upcoming" },
  { href: "/nba?view=results", view: "results", label: "Results" },
  { href: "/nba/standings", label: "Standings" },
  { href: "/nba/teams", label: "Teams" },
  { href: "/nba/value", label: "Value" },
  { href: "/nba/accuracy", label: "Accuracy" },
];

const NFL_SECTIONS = [
  { href: "/nfl", label: "Games" },
  { href: "/nfl/standings", label: "Standings" },
  { href: "/nfl/teams", label: "Teams" },
  { href: "/nfl/accuracy", label: "Accuracy" },
];

const soccerSections = (base: string) => [
  { href: base, label: "Matches" },
  { href: `${base}/table`, label: SOCCER_LEAGUES[soccerSlugOf(base)].kind === "cup" ? "Standings" : "Table" },
  { href: `${base}/teams`, label: "Clubs" },
  { href: `${base}/accuracy`, label: "Accuracy" },
];

/** Dropdown of every soccer competition, grouped by region. Fixed-position panel so the scrolling nav row can't clip it. */
function LeaguePicker({ current }: { current: string | null }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ left: 16, top: 100 });
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open]);
  const lg = current ? SOCCER_LEAGUES[current as keyof typeof SOCCER_LEAGUES] : null;
  return (
    <div ref={ref}>
      <button type="button" aria-haspopup="menu" aria-expanded={open}
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          setPos({ left: Math.max(8, Math.min(r.left, window.innerWidth - 296)), top: r.bottom + 6 });
          setOpen((o) => !o);
        }}
        className={`${pill(true)} ring-1 ring-line`}>
        {lg ? <><LeagueFlag slug={lg.slug as LeagueSlug} />{lg.name}</> : t("All competitions")}
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden><path d="m6 9 6 6 6-6" /></svg>
      </button>
      {open && (
        <div role="menu" style={{ left: pos.left, top: pos.top }}
          className="fixed z-50 w-72 rounded-xl border border-line bg-surface p-2 shadow-lg">
          <Link role="menuitem" href="/soccer" className="block rounded-lg px-3 py-1.5 text-sm text-muted hover:bg-surface-2 hover:text-ink">{t("All competitions")}</Link>
          {REGIONS.map((region) => (
            <div key={region} className="mt-1">
              <div className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-faint">{t(region)}</div>
              {LEAGUE_SLUGS.filter((s) => SOCCER_LEAGUES[s].region === region).map((s) => (
                <Link key={s} role="menuitem" href={`/soccer/${s}`} aria-current={s === current ? "page" : undefined}
                  className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm ${s === current ? "bg-surface-2 font-semibold text-ink" : "text-muted hover:bg-surface-2 hover:text-ink"}`}>
                  <span className="flex w-5 justify-center"><LeagueFlag slug={s} /></span>{SOCCER_LEAGUES[s].name}
                  <span className="ml-auto text-[11px] text-faint">{t(SOCCER_LEAGUES[s].country)}</span>
                </Link>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const pill = (active: boolean) =>
  `flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 font-medium transition ${
    active ? "bg-surface-2 text-ink" : "text-muted hover:text-ink"}`;

/** Sport tabs (all screens) + the current sport's sections (desktop; phones use the bottom tab bar). */
export function CategoryNav() {
  const t = useT();
  const path = usePathname();
  const view = useSearchParams().get("view") ?? "today";
  const inNba = path === "/nba" || path.startsWith("/nba/");
  const soccerSlug = soccerSlugOf(path);
  return (
    <nav aria-label={t("Sports")} className="mx-auto max-w-[1400px] px-4 sm:px-6">
      <ul className="no-scrollbar flex items-center gap-1 overflow-x-auto pb-2 text-sm">
        <li>
          <Link href="/" aria-current={path === "/" ? "page" : undefined} className={`${pill(path === "/")} font-semibold`}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M3 17l6-6 4 4 8-8M15 7h6v6" /></svg>
            {t("Trending")}
          </Link>
        </li>
        <li aria-hidden className="mx-1 h-5 w-px bg-line" />
        {SPORTS.map((s) => {
          const active = path === s.prefix || path.startsWith(`${s.prefix}/`);
          return (
            <li key={s.prefix}>
              <Link href={s.href} aria-current={active ? "page" : undefined} className={`${pill(active)} font-semibold`}>
                <span aria-hidden>{s.icon}</span>{t(s.label)}
                {!s.ready && <span className="rounded bg-accent-soft px-1.5 py-px text-[10px] font-semibold text-accent">{t("Soon")}</span>}
              </Link>
            </li>
          );
        })}
        {path.startsWith("/soccer") && (
          <>
            {/* Competition picker: shown on every screen size (phones have no other way to change competition). */}
            <li aria-hidden className="mx-2 h-5 w-px bg-line" />
            <li><LeaguePicker key={path} current={path === "/soccer" ? null : soccerSlug} /></li>{/* remount = close on navigation */}
            {path !== "/soccer" && <li aria-hidden className="mx-2 hidden h-5 w-px bg-line md:block" />}
            {path !== "/soccer" && soccerSections(`/soccer/${soccerSlug}`).map((c) => {
              const base = `/soccer/${soccerSlug}`;
              const active = c.href === base ? path === base || path.startsWith(`${base}/games`) : path.startsWith(c.href);
              return (
                <li key={c.label} className="hidden md:block">
                  <Link href={c.href} aria-current={active ? "page" : undefined} className={pill(active)}>{t(c.label)}</Link>
                </li>
              );
            })}
          </>
        )}
        {path.startsWith("/nfl") && (
          <>
            <li aria-hidden className="mx-2 hidden h-5 w-px bg-line md:block" />
            {NFL_SECTIONS.map((c) => {
              const active = c.href === "/nfl" ? path === "/nfl" || path.startsWith("/nfl/games") : path.startsWith(c.href);
              return (
                <li key={c.label} className="hidden md:block">
                  <Link href={c.href} aria-current={active ? "page" : undefined} className={pill(active)}>{t(c.label)}</Link>
                </li>
              );
            })}
          </>
        )}
        {inNba && (
          <>
            <li aria-hidden className="mx-2 hidden h-5 w-px bg-line md:block" />
            {NBA_SECTIONS.map((c) => {
              const active = c.view ? path === "/nba" && view === c.view : path.startsWith(c.href);
              return (
                <li key={c.label} className="hidden md:block">
                  <Link href={c.href} aria-current={active ? "page" : undefined} className={pill(active)}>{t(c.label)}</Link>
                </li>
              );
            })}
          </>
        )}
      </ul>
    </nav>
  );
}

// The <html> "dark" class is the source of truth (set before paint by the inline script in layout.tsx).
function subscribeTheme(cb: () => void) {
  const obs = new MutationObserver(cb);
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => obs.disconnect();
}

export function ThemeToggle() {
  const t = useT();
  const dark = useSyncExternalStore(subscribeTheme, () => document.documentElement.classList.contains("dark"), () => false);
  const toggle = () => {
    const next = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", next);
    try { localStorage.setItem("pp-theme", next ? "dark" : "light"); } catch {}
  };
  return (
    <button onClick={toggle} aria-label={dark ? t("Switch to light mode") : t("Switch to dark mode")}
      className="rounded-lg p-2 text-muted hover:bg-surface-2 hover:text-ink">
      {dark ? (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>
      ) : (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" /></svg>
      )}
    </button>
  );
}

/** EN | ES switch. The choice is saved in a cookie (proxy.ts serves that language on every page) and survives visits. */
export function LanguageToggle() {
  const lang = useLang();
  const t = useT();
  const [pending, switchTo] = useSwitchLanguage();
  return (
    <div role="group" aria-label={t("Language")} aria-busy={pending}
      className={`flex items-center rounded-lg bg-surface-2 p-0.5 text-xs font-semibold transition-opacity ${pending ? "opacity-60" : ""}`}>
      {(["en", "es"] as const).map((l) => (
        <button key={l} type="button" lang={l} aria-pressed={lang === l} disabled={pending} onClick={() => lang !== l && switchTo(l)}
          title={l === "en" ? "English" : "Español"}
          className={`rounded-md px-2 py-1 uppercase transition ${lang === l ? "bg-surface text-ink shadow-card" : "text-muted hover:text-ink"}`}>
          {l}
        </button>
      ))}
    </div>
  );
}

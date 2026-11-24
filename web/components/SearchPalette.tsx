"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";

import { useT } from "@/components/i18n";
import { LeagueFlag } from "@/components/LeagueFlag";
import { TeamBadge } from "@/components/ui";
import { teamCss, SOCCER_LEAGUES, LEAGUE_SLUGS, type LeagueSlug } from "@/lib/soccer";

/**
 * One search across every sport and competition (`GET /search/all`), as a command palette: the header bar on
 * desktop, a search icon on phones, or ⌘K / Ctrl+K / "/" anywhere. Results are grouped by sport; before typing it
 * shows recent picks and quick links. Keyboard: ↑ ↓ to move, Enter to open, Esc to close.
 */
const API = process.env.NEXT_PUBLIC_API_URL ?? "/api/v1";
const RECENT_KEY = "pp-recent-search";

type Sport = "nba" | "nfl" | "soccer";
interface Result { sport: Sport; type: "team" | "player"; id: string; name: string; abbr: string | null; subtitle: string | null;
  href: string; color?: string | null; league?: LeagueSlug }

// Open state shared by the triggers (header bar, phone icon) and the single dialog.
let open = false;
const listeners = new Set<() => void>();
const setOpen = (v: boolean) => { open = v; listeners.forEach((l) => l()); };
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };
const useOpen = () => useSyncExternalStore(subscribe, () => open, () => false);

const SPORT_META: Record<Sport, { label: string; icon: string }> = {
  nba: { label: "NBA", icon: "🏀" }, nfl: { label: "NFL", icon: "🏈" }, soccer: { label: "Soccer", icon: "⚽" },
};

function readRecent(): Result[] {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]").slice(0, 5); } catch { return []; }
}
function saveRecent(r: Result) {
  try { localStorage.setItem(RECENT_KEY, JSON.stringify([r, ...readRecent().filter((x) => x.href !== r.href)].slice(0, 5))); } catch {}
}

const SearchIcon = ({ size = 16 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
);

/** Desktop: looks like a search field. Phone: an icon button. Both open the palette. */
export function SearchTrigger({ variant }: { variant: "bar" | "icon" }) {
  const t = useT();
  if (variant === "icon") {
    return (
      <button type="button" onClick={() => setOpen(true)} aria-label={t("Search")} className="rounded-lg p-2 text-muted hover:bg-surface-2 hover:text-ink">
        <SearchIcon size={18} />
      </button>
    );
  }
  return (
    <button type="button" onClick={() => setOpen(true)}
      className="flex h-10 w-full max-w-xl items-center gap-2.5 rounded-xl border border-line bg-surface-2 px-3 text-left text-sm text-faint transition hover:border-accent/40 hover:text-muted">
      <SearchIcon />
      <span className="flex-1 truncate">{t("Search teams, players and clubs")}</span>
      <kbd className="hidden rounded border border-line px-1.5 text-[10px] sm:block">⌘K</kbd>
    </button>
  );
}

export function SearchPalette() {
  const isOpen = useOpen();
  // Global shortcuts: ⌘K / Ctrl+K anywhere, "/" when not typing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes((document.activeElement as HTMLElement)?.tagName);
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing)) {
        e.preventDefault();
        setOpen(true);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  // Mounted fresh on every open, so the query, results and recent list start clean. Rendered into <body>: inside
  // the sticky header (backdrop-filter) a fixed overlay would be positioned relative to the header, not the screen.
  return isOpen ? createPortal(<Palette />, document.body) : null;
}

function Palette() {
  const t = useT();
  const router = useRouter();
  const [q, setQ] = useState("");
  const [res, setRes] = useState<{ q: string; items: Result[]; error: boolean } | null>(null);
  const [active, setActive] = useState(0);
  const [recent] = useState<Result[]>(readRecent);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();

  // Focus the input and lock page scroll while open.
  useEffect(() => {
    const id = requestAnimationFrame(() => inputRef.current?.focus());
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { cancelAnimationFrame(id); document.body.style.overflow = prev; };
  }, []);

  const term = q.trim();
  useEffect(() => {
    if (term.length < 2) return;
    const ctrl = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const r = await fetch(`${API}/search/all?q=${encodeURIComponent(term)}`, { signal: ctrl.signal });
        const body = await r.json();
        setRes({ q: term, items: body.data ?? [], error: !r.ok });
        setActive(0);
      } catch (e) {
        if ((e as Error).name !== "AbortError") setRes({ q: term, items: [], error: true });
      }
    }, 140);
    return () => { clearTimeout(timer); ctrl.abort(); };
  }, [term]);

  const items = useMemo(() => (term.length >= 2 ? (res?.items ?? []) : recent), [term, res, recent]);
  const groups = useMemo(() => {
    const out: { sport: Sport; rows: { r: Result; i: number }[] }[] = [];
    items.forEach((r, i) => {
      const g = out.find((x) => x.sport === r.sport) ?? (out.push({ sport: r.sport, rows: [] }), out[out.length - 1]);
      g.rows.push({ r, i });
    });
    return out;
  }, [items]);

  const close = () => setOpen(false);
  const go = (r: Result) => { saveRecent(r); close(); router.push(r.href); };

  const loading = term.length >= 2 && res?.q !== term;
  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center bg-black/40 backdrop-blur-[2px] sm:p-4 sm:pt-[10vh]"
      onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
      <div role="dialog" aria-modal="true" aria-label={t("Search")}
        className="flex h-full w-full flex-col overflow-hidden bg-surface shadow-2xl sm:h-auto sm:max-h-[75vh] sm:max-w-2xl sm:rounded-2xl sm:border sm:border-line">
        <div className="flex items-center gap-2 border-b border-line px-4 pt-[env(safe-area-inset-top)]">
          <span className="text-faint"><SearchIcon size={18} /></span>
          <input ref={inputRef} type="text" inputMode="search" enterKeyHint="search" autoComplete="off" spellCheck={false} role="combobox" aria-expanded={items.length > 0} aria-controls={listId}
            aria-activedescendant={items.length ? `${listId}-${active}` : undefined} value={q}
            placeholder={t("Search teams, players and clubs")} aria-label={t("Search teams, players and clubs")}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") { e.preventDefault(); close(); }
              if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, items.length - 1)); }
              if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
              if (e.key === "Enter" && items[active]) { e.preventDefault(); go(items[active]); }
            }}
            className="h-14 flex-1 bg-transparent text-base outline-none placeholder:text-faint no-focus-ring" />
          {loading && <span className="h-4 w-4 animate-spin rounded-full border-2 border-line border-t-accent" aria-hidden />}
          <button type="button" onClick={close} className="rounded-lg px-2 py-1 text-xs font-medium text-muted hover:bg-surface-2">
            <span className="sm:hidden">{t("Cancel")}</span><kbd className="hidden sm:inline">Esc</kbd>
          </button>
        </div>

        <div className="flex-1 overflow-y-auto overscroll-contain p-2">
          {term.length >= 2 && res?.q === term && res.error && <p className="px-3 py-6 text-center text-sm text-muted">{t("Search is unavailable right now.")}</p>}
          {term.length >= 2 && res?.q === term && !res.error && items.length === 0 && (
            <p className="px-3 py-6 text-center text-sm text-muted">{t("No teams, players or clubs match “{q}”.", { q: term })}</p>
          )}
          {term.length < 2 && recent.length > 0 && <p className="px-3 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wide text-faint">{t("Recent")}</p>}
          <ul id={listId} role="listbox" aria-label={t("Results")}>
            {groups.map((g) => (
              <li key={g.sport} role="presentation">
                {term.length >= 2 && (
                  <p className="flex items-center gap-1.5 px-3 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-faint">
                    <span aria-hidden>{SPORT_META[g.sport].icon}</span>{t(SPORT_META[g.sport].label)}
                  </p>
                )}
                <ul role="presentation">
                  {g.rows.map(({ r, i }) => (
                    <li key={r.href} id={`${listId}-${i}`} role="option" aria-selected={i === active}>
                      <Link href={r.href} onClick={(e) => { e.preventDefault(); go(r); }} onMouseEnter={() => setActive(i)}
                        className={`flex items-center gap-3 rounded-xl px-3 py-2.5 ${i === active ? "bg-surface-2" : ""}`}>
                        <ResultIcon r={r} />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium">{r.name}</span>
                          <span className="block truncate text-xs text-muted">
                            {r.type === "team" ? (r.sport === "soccer" ? t("Club") : t("Team")) : t("Player")}{r.subtitle ? ` · ${t(r.subtitle)}` : ""}
                          </span>
                        </span>
                        {i === active && <span className="hidden text-xs text-faint sm:inline" aria-hidden>↵</span>}
                      </Link>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>

          {term.length < 2 && <QuickLinks onPick={close} />}
        </div>
        <div className="hidden items-center gap-4 border-t border-line px-4 py-2 text-[11px] text-faint sm:flex">
          <span><kbd>↑</kbd> <kbd>↓</kbd> {t("to move")}</span><span><kbd>↵</kbd> {t("to open")}</span><span><kbd>Esc</kbd> {t("to close")}</span>
        </div>
      </div>
    </div>
  );
}

function ResultIcon({ r }: { r: Result }) {
  if (r.type === "team" && r.abbr) {
    return r.sport === "soccer"
      ? <TeamBadge abbr={r.abbr} size={32} league="soccer" color={teamCss({ color: r.color ?? null })} logoId={Number(r.id)} />
      : <TeamBadge abbr={r.abbr} size={32} league={r.sport} />;
  }
  const initials = r.name.split(" ").map((s) => s[0]).slice(0, 2).join("");
  return <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-3 text-[11px] font-bold text-muted">{initials}</span>;
}

function QuickLinks({ onPick }: { onPick: () => void }) {
  const t = useT();
  const links = [
    { href: "/nba", label: "NBA · Today", icon: "🏀" }, { href: "/nfl", label: "NFL · This week", icon: "🏈" },
    { href: "/nba/value", label: "NBA value finder", icon: "💡" }, { href: "/nba/accuracy", label: "NBA accuracy", icon: "🎯" },
  ];
  return (
    <div className="flex flex-col gap-3 px-1 pb-2 pt-3">
      <p className="px-2 text-[11px] font-semibold uppercase tracking-wide text-faint">{t("Jump to")}</p>
      <div className="grid grid-cols-2 gap-1.5 px-1">
        {links.map((l) => (
          <Link key={l.href} href={l.href} onClick={onPick} className="flex items-center gap-2 rounded-xl border border-line px-3 py-2.5 text-sm hover:bg-surface-2">
            <span aria-hidden>{l.icon}</span><span className="truncate">{t(l.label)}</span>
          </Link>
        ))}
      </div>
      <p className="px-2 pt-1 text-[11px] font-semibold uppercase tracking-wide text-faint">{t("Soccer competitions")}</p>
      <div className="flex flex-wrap gap-1.5 px-1">
        {LEAGUE_SLUGS.map((s) => (
          <Link key={s} href={`/soccer/${s}`} onClick={onPick}
            className="flex items-center gap-1.5 rounded-full border border-line px-3 py-1.5 text-xs font-medium hover:bg-surface-2">
            <LeagueFlag slug={s} />{SOCCER_LEAGUES[s].name}
          </Link>
        ))}
      </div>
    </div>
  );
}

"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";

import type { SearchResult } from "@/lib/types";
import { teamColor } from "@/lib/teams";

const API = process.env.NEXT_PUBLIC_API_URL ?? "/api/v1";

const CATEGORIES = [
  { href: "/", view: "today", label: "Today" },
  { href: "/?view=tomorrow", view: "tomorrow", label: "Tomorrow" },
  { href: "/?view=upcoming", view: "upcoming", label: "Upcoming" },
  { href: "/?view=results", view: "results", label: "Results" },
  { href: "/standings", label: "Standings" },
  { href: "/teams", label: "Teams" },
  { href: "/accuracy", label: "Accuracy" },
  { href: "/methodology", label: "How it works" },
];

export function CategoryNav() {
  const path = usePathname();
  const view = useSearchParams().get("view") ?? "today";
  return (
    <nav aria-label="Categories" className="mx-auto max-w-[1400px] px-4 sm:px-6">
      <ul className="no-scrollbar flex gap-1 overflow-x-auto pb-2 text-sm">
        {CATEGORIES.map((c) => {
          const active = c.view ? path === "/" && view === c.view : path.startsWith(c.href);
          return (
            <li key={c.label}>
              <Link
                href={c.href}
                aria-current={active ? "page" : undefined}
                className={`block whitespace-nowrap rounded-lg px-3 py-1.5 font-medium transition ${
                  active ? "bg-surface-2 text-ink" : "text-muted hover:text-ink"}`}
              >
                {c.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export function SearchBox() {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [error, setError] = useState(false);
  const router = useRouter();
  const listId = useId();
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (q.trim().length < 2) return;
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      try {
        const r = await fetch(`${API}/search?q=${encodeURIComponent(q.trim())}&limit=8`, { signal: ctrl.signal });
        const body = await r.json();
        setResults(body.data ?? []);
        setError(false);
        setActive(0);
      } catch (e) {
        if ((e as Error).name !== "AbortError") setError(true);
      }
    }, 180);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [q]);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && document.activeElement?.tagName !== "INPUT") {
        e.preventDefault();
        boxRef.current?.querySelector("input")?.focus();
      }
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  const shown = q.trim().length >= 2 ? results : [];
  const hrefFor = (r: SearchResult) => (r.type === "team" ? `/teams/${r.subtitle}` : `/players/${r.id}`);

  return (
    <div ref={boxRef} className="relative w-full max-w-xl">
      <svg className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
        <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
      </svg>
      <input
        type="search"
        role="combobox"
        aria-expanded={open && shown.length > 0}
        aria-controls={listId}
        aria-label="Search teams and players"
        placeholder="Search teams and players"
        value={q}
        onChange={(e) => { setQ(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, shown.length - 1)); }
          if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          if (e.key === "Enter" && shown[active]) { router.push(hrefFor(shown[active])); setOpen(false); setQ(""); }
          if (e.key === "Escape") setOpen(false);
        }}
        className="h-10 w-full rounded-xl border border-line bg-surface-2 pl-9 pr-10 text-sm outline-none placeholder:text-faint focus:border-accent/50 focus:bg-surface"
      />
      <kbd className="pointer-events-none absolute right-3 top-1/2 hidden -translate-y-1/2 rounded border border-line px-1.5 text-[10px] text-faint sm:block">/</kbd>
      {open && q.trim().length >= 2 && (
        <ul id={listId} role="listbox" className="absolute left-0 right-0 top-12 z-50 overflow-hidden rounded-xl border border-line bg-surface py-1 shadow-lg">
          {error && <li className="px-4 py-3 text-sm text-muted">Search is unavailable right now.</li>}
          {!error && shown.length === 0 && <li className="px-4 py-3 text-sm text-muted">No teams or players match “{q}”.</li>}
          {shown.map((r, i) => (
            <li key={`${r.type}-${r.id}`} role="option" aria-selected={i === active}>
              <Link
                href={hrefFor(r)}
                onClick={() => { setOpen(false); setQ(""); }}
                onMouseEnter={() => setActive(i)}
                className={`flex items-center gap-3 px-4 py-2 text-sm ${i === active ? "bg-surface-2" : ""}`}
              >
                <span className="inline-flex h-7 w-7 items-center justify-center rounded-md text-[10px] font-bold text-white"
                  style={{ background: r.subtitle ? teamColor(r.subtitle) : "var(--text-faint)" }}>
                  {r.type === "team" ? r.subtitle : r.name.split(" ").map((s) => s[0]).slice(0, 2).join("")}
                </span>
                <span className="flex-1 truncate font-medium">{r.name}</span>
                <span className="text-xs text-muted">{r.type === "team" ? "Team" : r.subtitle}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// The <html> "dark" class is the source of truth (set before paint by the inline script in layout.tsx).
function subscribeTheme(cb: () => void) {
  const obs = new MutationObserver(cb);
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => obs.disconnect();
}

export function ThemeToggle() {
  const dark = useSyncExternalStore(subscribeTheme, () => document.documentElement.classList.contains("dark"), () => false);
  const toggle = () => {
    const next = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", next);
    try { localStorage.setItem("pp-theme", next ? "dark" : "light"); } catch {}
  };
  return (
    <button onClick={toggle} aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
      className="rounded-lg p-2 text-muted hover:bg-surface-2 hover:text-ink">
      {dark ? (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>
      ) : (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" /></svg>
      )}
    </button>
  );
}

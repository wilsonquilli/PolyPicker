import Link from "next/link";
import type { ReactNode } from "react";

import { teamColor } from "@/lib/teams";

export function TeamBadge({ abbr, size = 32, className = "" }: { abbr: string; size?: number; className?: string }) {
  return (
    <span
      aria-hidden
      className={`inline-flex shrink-0 items-center justify-center rounded-lg font-bold tracking-tight text-white ${className}`}
      style={{ width: size, height: size, background: teamColor(abbr), fontSize: Math.max(9, size * 0.32) }}
    >
      {abbr}
    </span>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-2xl border border-line bg-surface shadow-card ${className}`}>{children}</div>;
}

export function SectionTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-3 flex items-end justify-between gap-3">
      <h2 className="text-lg font-semibold tracking-tight">{children}</h2>
      {action}
    </div>
  );
}

type PillTone = "neutral" | "accent" | "win" | "loss" | "warn";
const TONES: Record<PillTone, string> = {
  neutral: "bg-surface-2 text-muted",
  accent: "bg-accent-soft text-accent",
  win: "bg-win-soft text-win",
  loss: "bg-loss-soft text-loss",
  warn: "bg-warn-soft text-warn",
};

export function Pill({ children, tone = "neutral", className = "" }: { children: ReactNode; tone?: PillTone; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-semibold ${TONES[tone]} ${className}`}>
      {children}
    </span>
  );
}

export function StatusPill({ status }: { status: string }) {
  const map: Record<string, [string, PillTone]> = {
    final: ["Final", "neutral"], scheduled: ["Upcoming", "accent"], live: ["Live", "loss"],
    postponed: ["Postponed", "warn"], canceled: ["Canceled", "warn"], unknown: ["Awaiting result", "warn"],
  };
  const [label, tone] = map[status] ?? [status, "neutral"];
  return <Pill tone={tone}>{label}</Pill>;
}

/** Two-segment probability bar. Widths encode probability; text labels carry the same info. */
export function ProbBar({ home, homeAbbr, awayAbbr, height = 6 }: { home: number; homeAbbr: string; awayAbbr: string; height?: number }) {
  const h = Math.round(home * 1000) / 10;
  return (
    <div
      role="img"
      aria-label={`${awayAbbr} ${(100 - h).toFixed(0)}%, ${homeAbbr} ${h.toFixed(0)}%`}
      className="flex w-full overflow-hidden rounded-full bg-surface-3"
      style={{ height }}
    >
      <div style={{ width: `${100 - h}%`, background: home < 0.5 ? "var(--accent)" : "var(--text-faint)", opacity: home < 0.5 ? 1 : 0.4 }} />
      <div className="w-[2px] bg-surface" />
      <div style={{ width: `${h}%`, background: home >= 0.5 ? "var(--accent)" : "var(--text-faint)", opacity: home >= 0.5 ? 1 : 0.4 }} />
    </div>
  );
}

/** Semi-circular "% chance" gauge. */
export function Gauge({ p, label = "chance", size = 92 }: { p: number; label?: string; size?: number }) {
  const r = 40, c = Math.PI * r, val = Math.max(0, Math.min(1, p));
  return (
    <div className="relative" style={{ width: size, height: size * 0.62 }} role="img" aria-label={`${Math.round(val * 100)}% ${label}`}>
      <svg viewBox="0 0 100 60" width={size} height={size * 0.6}>
        <path d="M10 55 A40 40 0 0 1 90 55" fill="none" stroke="var(--surface-3)" strokeWidth="9" strokeLinecap="round" />
        <path d="M10 55 A40 40 0 0 1 90 55" fill="none" stroke="var(--accent)" strokeWidth="9" strokeLinecap="round"
          strokeDasharray={`${c * val} ${c}`} />
      </svg>
      <div className="absolute inset-x-0 bottom-0 text-center leading-none">
        <div className="tabular text-lg font-bold">{Math.round(val * 100)}%</div>
        <div className="text-[10px] text-muted">{label}</div>
      </div>
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-2xl border border-dashed border-line px-6 py-14 text-center">
      <p className="font-semibold">{title}</p>
      {children && <div className="mx-auto mt-1 max-w-md text-sm text-muted">{children}</div>}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-lg bg-surface-2 ${className}`} />;
}

export function Breadcrumbs({ items }: { items: { href?: string; label: string }[] }) {
  return (
    <nav aria-label="Breadcrumb" className="mb-3 flex flex-wrap items-center gap-1.5 text-xs text-muted">
      {items.map((it, i) => (
        <span key={i} className="flex items-center gap-1.5">
          {i > 0 && <span className="text-faint">›</span>}
          {it.href ? <Link href={it.href} className="hover:text-ink">{it.label}</Link> : <span>{it.label}</span>}
        </span>
      ))}
    </nav>
  );
}

export function StatTile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="rounded-xl bg-surface-2 px-3 py-2.5">
      <div className="text-[11px] font-medium uppercase tracking-wide text-muted">{label}</div>
      <div className="tabular mt-0.5 text-lg font-semibold">{value}</div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </div>
  );
}

export function FormChips({ results }: { results: { result: "W" | "L"; opponent: string; home: boolean }[] }) {
  return (
    <div className="flex gap-1">
      {[...results].reverse().map((r, i) => (
        <span key={i} title={`${r.result} ${r.home ? "vs" : "@"} ${r.opponent}`}
          className={`inline-flex h-6 w-6 items-center justify-center rounded-md text-[11px] font-bold ${r.result === "W" ? "bg-win-soft text-win" : "bg-loss-soft text-loss"}`}>
          {r.result}
        </span>
      ))}
    </div>
  );
}

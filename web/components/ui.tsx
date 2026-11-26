import type { ReactNode } from "react";
import { NFL_COLORS } from "@/lib/nfl";
import soccerLogoIds from "@/lib/soccer-logos.json";
import { hasTeamLogo, teamColor } from "@/lib/teams";
export { Breadcrumbs, FormChips, Gauge, ProbBar, StatusPill, TriBar } from "@/components/ui-client";

const SOCCER_LOGOS = new Set<number>(soccerLogoIds);

export function TeamBadge({ abbr, size = 32, className = "", league = "nba", color, logoId }: {
  abbr: string; size?: number; className?: string; league?: "nba" | "nfl" | "soccer";
  color?: string; logoId?: number;
}) {
  if (league === "soccer" && logoId !== undefined && SOCCER_LOGOS.has(logoId)) {
    return (
      <span aria-hidden className={`inline-flex shrink-0 items-center justify-center rounded-lg bg-white ring-1 ring-black/5 ${className}`}
        style={{ width: size, height: size, padding: Math.max(2, Math.round(size * 0.1)) }}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={`/logos/soccer/${logoId}.webp`} alt="" width={size} height={size} loading="lazy" decoding="async" className="h-full w-full object-contain" />
      </span>
    );
  }
  if (league !== "soccer" && (league === "nfl" ? abbr in NFL_COLORS : hasTeamLogo(abbr))) {
    return (
      <span aria-hidden className={`inline-flex shrink-0 items-center justify-center rounded-lg bg-white ring-1 ring-black/5 ${className}`}
        style={{ width: size, height: size, padding: Math.max(2, Math.round(size * 0.1)) }}>
        {/* eslint-disable-next-line @next/next/no-img-element -- pre-sized 256px WebP in public/logos/nba, cached by the service worker */}
        <img src={`/logos/${league}/${abbr}.webp`} alt="" width={size} height={size} loading="lazy" decoding="async" draggable={false}
          className="h-full w-full object-contain" />
      </span>
    );
  }
  return (
    <span
      aria-hidden
      className={`inline-flex shrink-0 items-center justify-center rounded-lg font-bold tracking-tight text-white ${className}`}
      style={{ width: size, height: size, background: color ?? (league === "nfl" ? (NFL_COLORS[abbr] ?? "#5f6878") : teamColor(abbr)),
        fontSize: Math.max(9, size * (abbr.length > 2 ? 0.32 : 0.38)) }}
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

export type PillTone = "neutral" | "accent" | "win" | "loss" | "warn";
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


export function StatTile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="rounded-xl bg-surface-2 px-3 py-2.5">
      <div className="text-[11px] font-medium uppercase tracking-wide text-muted">{label}</div>
      <div className="tabular mt-0.5 text-lg font-semibold">{value}</div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </div>
  );
}
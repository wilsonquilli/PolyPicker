"use client";

import Link from "next/link";
import { useT } from "@/components/i18n";
import { keyed } from "@/lib/i18n";
import { Pill, type PillTone } from "@/components/ui";

export function StatusPill({ status }: { status: string }) {
  const t = useT();
  const map: Record<string, [string, PillTone]> = {
    final: ["Final", "neutral"], scheduled: ["Upcoming", "accent"], live: ["Live", "loss"],
    postponed: ["Postponed", "warn"], canceled: ["Canceled", "warn"], unknown: ["Awaiting result", "warn"],
  };
  const [label, tone] = map[status] ?? [status, "neutral"];
  return <Pill tone={tone}>{keyed(t, `status:${status}`, label)}</Pill>;
}

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

export function Gauge({ p, label: label_ = "chance", size = 92 }: { p: number; label?: string; size?: number }) {
  const label = useT()(label_);
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

export function Breadcrumbs({ items }: { items: { href?: string; label: string }[] }) {
  const t = useT();
  return (
    <nav aria-label={t("Breadcrumb")} className="mb-3 flex flex-wrap items-center gap-1.5 text-xs text-muted">
      {items.map((it, i) => (
        <span key={i} className="flex items-center gap-1.5">
          {i > 0 && <span className="text-faint">›</span>}
          {it.href ? <Link href={it.href} className="hover:text-ink">{t(it.label)}</Link> : <span>{t(it.label)}</span>}
        </span>
      ))}
    </nav>
  );
}

export function FormChips({ results }: { results: { result: "W" | "L" | "T" | "D"; opponent: string; home: boolean }[] }) {
  const t = useT();
  const letter = (r: string) => keyed(t, `form:${r}`, r);
  return (
    <div className="flex gap-1">
      {[...results].reverse().map((r, i) => (
        <span key={i} title={`${letter(r.result)} ${r.home ? t("vs") : "@"} ${r.opponent}`}
          className={`inline-flex h-6 w-6 items-center justify-center rounded-md text-[11px] font-bold ${r.result === "W" ? "bg-win-soft text-win" : r.result === "T" || r.result === "D" ? "bg-surface-2 text-muted" : "bg-loss-soft text-loss"}`}>
          {letter(r.result)}
        </span>
      ))}
    </div>
  );
}

export function TriBar({ home, draw, away, labels, height = 6 }: { home: number; draw: number; away: number; labels: [string, string]; height?: number }) {
  const t = useT();
  const top = Math.max(home, draw, away);
  const seg = (p: number, k: string) => (
    <div key={k} style={{ width: `${p * 100}%`, background: p === top ? "var(--accent)" : "var(--text-faint)", opacity: p === top ? 1 : 0.4 }} />
  );
  return (
    <div role="img" aria-label={`${labels[0]} ${Math.round(home * 100)}%, ${t("draw")} ${Math.round(draw * 100)}%, ${labels[1]} ${Math.round(away * 100)}%`}
      className="flex w-full gap-[2px] overflow-hidden rounded-full bg-surface-3" style={{ height }}>
      {seg(home, "h")}{seg(draw, "d")}{seg(away, "a")}
    </div>
  );
}
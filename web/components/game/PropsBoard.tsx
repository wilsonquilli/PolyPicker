"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { teamColor } from "@/lib/teams";
import type { PlayerForecast, ThresholdRow } from "@/lib/types";

type Stat = "points" | "assists" | "rebounds";

const AVAIL: Record<string, { label: string; cls: string }> = {
  available: { label: "Played last game", cls: "bg-win-soft text-win" },
  uncertain: { label: "Uncertain", cls: "bg-warn-soft text-warn" },
  doubtful: { label: "Doubtful", cls: "bg-loss-soft text-loss" },
  likely_out: { label: "Likely out", cls: "bg-loss-soft text-loss" },
};

/** Multi-outcome list, prediction-market style: one row per player, threshold chips with % chance. */
export function PropsBoard({ players, teams, excluded }: {
  players: PlayerForecast[];
  teams: { team_id: number; abbreviation: string }[];
  excluded: { player_id: number; full_name: string | null; reason: string; market: string }[];
}) {
  const [stat, setStat] = useState<Stat>("points");
  const [team, setTeam] = useState<number | "all">("all");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const abbr = (tid?: number) => teams.find((t) => t.team_id === tid)?.abbreviation ?? "";

  const rows = useMemo(() => players
    .filter((p) => p.markets[stat]?.expected !== undefined)
    .filter((p) => team === "all" || p.team_id === team)
    .filter((p) => !q || (p.full_name ?? "").toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => (b.markets[stat]!.expected ?? 0) - (a.markets[stat]!.expected ?? 0)), [players, stat, team, q]);
  const excludedNow = excluded.filter((e) => e.market === `player_${stat}`);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="flex gap-1 rounded-xl bg-surface-2 p-1 text-sm font-medium" role="tablist" aria-label="Stat">
          {(["points", "assists", "rebounds"] as Stat[]).map((s) => (
            <button key={s} role="tab" aria-selected={stat === s} onClick={() => setStat(s)}
              className={`flex items-center gap-1 rounded-lg px-3 py-1.5 capitalize ${stat === s ? "bg-surface text-ink shadow-card" : "text-muted"}`}>
              {s}{s === "rebounds" && <span className="rounded bg-warn-soft px-1 text-[10px] font-semibold text-warn">Beta</span>}
            </button>
          ))}
        </div>
        <div className="flex gap-1 rounded-xl bg-surface-2 p-1 text-sm font-medium">
          {[{ id: "all" as const, label: "Both" }, ...teams.map((t) => ({ id: t.team_id, label: t.abbreviation }))].map((t) => (
            <button key={String(t.id)} onClick={() => setTeam(t.id)} aria-pressed={team === t.id}
              className={`rounded-lg px-3 py-1.5 ${team === t.id ? "bg-surface text-ink shadow-card" : "text-muted"}`}>{t.label}</button>
          ))}
        </div>
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter players" aria-label="Filter players"
          className="h-9 min-w-[160px] flex-1 rounded-xl border border-line bg-surface-2 px-3 text-sm outline-none focus:border-accent/50" />
      </div>

      {rows.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line px-4 py-8 text-center text-sm text-muted">No player forecasts match.</p>
      ) : (
        <ul className="divide-y divide-line overflow-hidden rounded-2xl border border-line">
          {rows.map((p) => {
            const m = p.markets[stat]!;
            const av = p.availability ? AVAIL[p.availability.status] : undefined;
            const expanded = open === p.player_id;
            return (
              <li key={p.player_id} className="bg-surface">
                <div className="flex flex-wrap items-center gap-3 px-4 py-3">
                  <button onClick={() => setOpen(expanded ? null : p.player_id)} aria-expanded={expanded}
                    className="flex min-w-[200px] flex-1 items-center gap-3 text-left">
                    <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[11px] font-bold text-white"
                      style={{ background: teamColor(abbr(p.team_id)) }}>
                      {(p.full_name ?? "?").split(" ").map((s) => s[0]).slice(0, 2).join("")}
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-semibold">{p.full_name}</span>
                      <span className="flex items-center gap-1.5 text-[11px] text-muted">
                        {abbr(p.team_id)}{p.position ? ` · ${p.position}` : ""}
                        {av && <span className={`rounded px-1 font-semibold ${av.cls}`} title={p.availability?.reason ?? undefined}>{av.label}</span>}
                      </span>
                    </span>
                  </button>
                  <div className="text-right">
                    <div className="tabular text-lg font-bold">{m.expected?.toFixed(1)}</div>
                    <div className="text-[10px] uppercase tracking-wide text-muted">expected</div>
                  </div>
                  <div className="no-scrollbar flex w-full gap-1.5 overflow-x-auto sm:w-auto">
                    {m.thresholds_available ? m.thresholds.map((t) => <Chip key={`${t.kind}${t.threshold}`} t={t} />)
                      : <span className="text-xs text-muted">Thresholds not published (calibration gate)</span>}
                  </div>
                </div>
                {expanded && (
                  <div className="grid gap-3 border-t border-line bg-surface-2/50 px-4 py-3 text-sm sm:grid-cols-2">
                    <div>
                      <div className="mb-1 text-xs font-semibold text-muted">What the forecast is based on</div>
                      <ul className="list-disc space-y-0.5 pl-4 text-xs">
                        {m.explanation?.map((e) => <li key={e}>{e}</li>)}
                      </ul>
                    </div>
                    <div className="text-xs">
                      <div className="mb-1 font-semibold text-muted">Availability</div>
                      <p>{p.availability?.reason ?? "Played in the team’s last game."}</p>
                      <p className="mt-1 text-muted">{p.availability?.source}</p>
                      <Link href={`/players/${p.player_id}`} className="mt-2 inline-block font-semibold text-accent hover:underline">Game log →</Link>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {excludedNow.length > 0 && (
        <p className="mt-3 text-xs text-muted">
          <b>No forecast:</b> {excludedNow.map((e) => `${e.full_name} (${e.reason.replace(/^No forecast: /, "")})`).join("; ")}
        </p>
      )}
    </div>
  );
}

function Chip({ t }: { t: ThresholdRow }) {
  const o = t.outcome;
  const tone = o?.state === "final" ? (o.result === "hit" ? "ring-2 ring-win" : o.result === "miss" ? "opacity-60" : "") : "";
  const short = t.kind === "at_least" ? `${t.threshold}+` : `o${t.threshold}`;
  return (
    <span title={`${t.label}: ${(t.probability * 100).toFixed(1)}%${o?.state === "final" ? ` · result: ${o.result}` : ""}`}
      className={`flex min-w-[58px] shrink-0 flex-col items-center rounded-lg border border-line px-2 py-1 ${t.kind === "over" ? "bg-accent-soft" : "bg-surface"} ${tone}`}>
      <span className="text-[10px] font-medium text-muted">{short}</span>
      <span className="tabular text-sm font-bold">{Math.round(t.probability * 100)}%</span>
    </span>
  );
}

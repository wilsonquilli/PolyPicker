"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { teamColor } from "@/lib/teams";
import type { PlayerForecast, ThresholdRow } from "@/lib/types";
import { useT } from "@/components/i18n";
import { keyed } from "@/lib/i18n";

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
  const t = useT();
  const [stat, setStat] = useState<Stat>("points");
  const [team, setTeam] = useState<number | "all">("all");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const abbr = (tid?: number) => teams.find((tm) => tm.team_id === tid)?.abbreviation ?? "";

  const rows = useMemo(() => players
    .filter((p) => p.markets[stat]?.expected !== undefined)
    .filter((p) => team === "all" || p.team_id === team)
    .filter((p) => !q || (p.full_name ?? "").toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => (b.markets[stat]!.expected ?? 0) - (a.markets[stat]!.expected ?? 0)), [players, stat, team, q]);
  const excludedNow = excluded.filter((e) => e.market === `player_${stat}`);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="flex gap-1 rounded-xl bg-surface-2 p-1 text-sm font-medium" role="tablist" aria-label={t("Stat")}>
          {(["points", "assists", "rebounds"] as Stat[]).map((s) => (
            <button key={s} role="tab" aria-selected={stat === s} onClick={() => setStat(s)}
              className={`flex items-center gap-1 rounded-lg px-3 py-1.5 capitalize ${stat === s ? "bg-surface text-ink shadow-card" : "text-muted"}`}>
              {t(s)}{s === "rebounds" && <span className="rounded bg-warn-soft px-1 text-[10px] font-semibold text-warn">{t("Beta")}</span>}
            </button>
          ))}
        </div>
        <div className="flex gap-1 rounded-xl bg-surface-2 p-1 text-sm font-medium">
          {[{ id: "all" as const, label: t("Both") }, ...teams.map((tm) => ({ id: tm.team_id, label: tm.abbreviation }))].map((tm) => (
            <button key={String(tm.id)} onClick={() => setTeam(tm.id)} aria-pressed={team === tm.id}
              className={`rounded-lg px-3 py-1.5 ${team === tm.id ? "bg-surface text-ink shadow-card" : "text-muted"}`}>{tm.label}</button>
          ))}
        </div>
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("Filter players")} aria-label={t("Filter players")}
          className="h-9 min-w-[160px] flex-1 rounded-xl border border-line bg-surface-2 px-3 text-sm outline-none focus:border-accent/50" />
      </div>

      {rows.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line px-4 py-8 text-center text-sm text-muted">{t("No player forecasts match.")}</p>
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
                        {av && <span className={`rounded px-1 font-semibold ${av.cls}`} title={p.availability?.reason ? t(p.availability.reason) : undefined}>{t(av.label)}</span>}
                      </span>
                    </span>
                  </button>
                  <div className="text-right">
                    <div className="tabular text-lg font-bold">{m.expected?.toFixed(1)}</div>
                    <div className="text-[10px] uppercase tracking-wide text-muted">{t("expected")}</div>
                  </div>
                  <div className="no-scrollbar flex w-full gap-1.5 overflow-x-auto sm:w-auto">
                    {m.thresholds_available ? m.thresholds.map((tm) => <Chip key={`${tm.kind}${tm.threshold}`} row={tm} />)
                      : <span className="text-xs text-muted">{t("Thresholds not published (calibration gate)")}</span>}
                  </div>
                </div>
                {expanded && (
                  <div className="grid gap-3 border-t border-line bg-surface-2/50 px-4 py-3 text-sm sm:grid-cols-2">
                    <div>
                      <div className="mb-1 text-xs font-semibold text-muted">{t("What the forecast is based on")}</div>
                      <ul className="list-disc space-y-0.5 pl-4 text-xs">
                        {m.explanation?.map((e) => <li key={e}>{t(e)}</li>)}
                      </ul>
                    </div>
                    <div className="text-xs">
                      <div className="mb-1 font-semibold text-muted">{t("Availability")}</div>
                      <p>{t(p.availability?.reason ?? "Played in the team’s last game.")}</p>
                      {p.availability?.source && <p className="mt-1 text-muted">{t(p.availability.source)}</p>}
                      <Link href={`/nba/players/${p.player_id}`} className="mt-2 inline-block font-semibold text-accent hover:underline">{t("Game log →")}</Link>
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
          <b>{t("No forecast:")}</b> {excludedNow.map((e) => `${e.full_name} (${t(e.reason.replace(/^No forecast: /, ""))})`).join("; ")}
        </p>
      )}
    </div>
  );
}

function Chip({ row }: { row: ThresholdRow }) {
  const t = useT();
  const o = row.outcome;
  const tone = o?.state === "final" ? (o.result === "hit" ? "ring-2 ring-win" : o.result === "miss" ? "opacity-60" : "") : "";
  const short = row.kind === "at_least" ? `${row.threshold}+` : `o${row.threshold}`;
  return (
    <span title={`${t(row.label)}: ${(row.probability * 100).toFixed(1)}%${o?.state === "final" ? ` · ${t("result")}: ${keyed(t, `result:${o.result}`, o.result ?? "")}` : ""}`}
      className={`flex min-w-[58px] shrink-0 flex-col items-center rounded-lg border border-line px-2 py-1 ${row.kind === "over" ? "bg-accent-soft" : "bg-surface"} ${tone}`}>
      <span className="text-[10px] font-medium text-muted">{short}</span>
      <span className="tabular text-sm font-bold">{Math.round(row.probability * 100)}%</span>
    </span>
  );
}

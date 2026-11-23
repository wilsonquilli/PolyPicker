"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { NFL_COLORS, STAT_LABEL, STAT_ORDER, type NflPlayerForecast, type NflStat, type NflThreshold } from "@/lib/nfl";
import { useT } from "@/components/i18n";
import { keyed } from "@/lib/i18n";

const AVAIL: Record<string, { label: string; cls: string }> = {
  available: { label: "No injury designation", cls: "bg-win-soft text-win" },
  questionable: { label: "Questionable", cls: "bg-warn-soft text-warn" },
  out: { label: "Out", cls: "bg-loss-soft text-loss" },
};

/** NFL player props, prediction-market style: one row per player, threshold chips with % chance. */
export function NflPropsBoard({ players, teams, excluded }: {
  players: NflPlayerForecast[];
  teams: { team_id: number; abbreviation: string }[];
  excluded: { player_id: string; full_name: string | null; reason: string; markets: string[] }[];
}) {
  const t = useT();
  const available = STAT_ORDER.filter((s) => players.some((p) => p.markets[s]));
  const [stat, setStat] = useState<NflStat>(available.includes("receiving_yards") ? "receiving_yards" : available[0] ?? "passing_yards");
  const [team, setTeam] = useState<number | "all">("all");
  const [open, setOpen] = useState<string | null>(null);
  const abbr = (tid?: number) => teams.find((tm) => tm.team_id === tid)?.abbreviation ?? "";

  const rows = useMemo(() => players
    .filter((p) => p.markets[stat]?.expected !== undefined)
    .filter((p) => team === "all" || p.team_id === team)
    .sort((a, b) => (b.markets[stat]!.expected ?? 0) - (a.markets[stat]!.expected ?? 0)), [players, stat, team]);
  const excludedNow = excluded.filter((e) => e.markets.some((m) => m.endsWith(stat === "scrimmage_tds" ? "anytime_td" : stat)));

  if (available.length === 0) {
    return <p className="rounded-xl border border-dashed border-line px-4 py-8 text-center text-sm text-muted">{t("No player forecasts for this game.")}</p>;
  }
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="no-scrollbar flex max-w-full gap-1 overflow-x-auto rounded-xl bg-surface-2 p-1 text-sm font-medium" role="tablist" aria-label={t("Stat")}>
          {available.map((s) => (
            <button key={s} role="tab" aria-selected={stat === s} onClick={() => setStat(s)}
              className={`flex shrink-0 items-center gap-1 whitespace-nowrap rounded-lg px-3 py-1.5 ${stat === s ? "bg-surface text-ink shadow-card" : "text-muted"}`}>
              {t(STAT_LABEL[s])}
              {(s === "passing_tds" || s === "scrimmage_tds") && <span className="rounded bg-warn-soft px-1 text-[10px] font-semibold text-warn">{t("Beta")}</span>}
            </button>
          ))}
        </div>
        <div className="flex gap-1 rounded-xl bg-surface-2 p-1 text-sm font-medium">
          {[{ id: "all" as const, label: t("Both") }, ...teams.map((tm) => ({ id: tm.team_id, label: tm.abbreviation }))].map((tm) => (
            <button key={String(tm.id)} onClick={() => setTeam(tm.id)} aria-pressed={team === tm.id}
              className={`rounded-lg px-3 py-1.5 ${team === tm.id ? "bg-surface text-ink shadow-card" : "text-muted"}`}>{tm.label}</button>
          ))}
        </div>
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
                      style={{ background: NFL_COLORS[abbr(p.team_id)] ?? "#5f6878" }}>
                      {(p.full_name ?? "?").split(" ").map((s) => s[0]).slice(0, 2).join("")}
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-semibold">{p.full_name}</span>
                      <span className="flex items-center gap-1.5 text-[11px] text-muted">
                        {abbr(p.team_id)}{p.position ? ` · ${p.position}` : ""}
                        {av && p.availability?.status !== "available" && (
                          <span className={`rounded px-1 font-semibold ${av.cls}`} title={p.availability?.reason ? t(p.availability.reason) : undefined}>{t(av.label)}</span>)}
                      </span>
                    </span>
                  </button>
                  <div className="text-right">
                    <div className="tabular text-lg font-bold">
                      {stat === "scrimmage_tds" ? `${Math.round((m.thresholds.find((th) => th.threshold === 1)?.probability ?? 0) * 100)}%` : m.expected?.toFixed(stat === "receptions" || stat === "passing_tds" ? 1 : 0)}
                    </div>
                    <div className="text-[10px] uppercase tracking-wide text-muted">{stat === "scrimmage_tds" ? t("scores a TD") : t("expected")}</div>
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
                      <ul className="list-disc space-y-0.5 pl-4 text-xs">{m.explanation?.map((e) => <li key={e}>{t(e)}</li>)}</ul>
                      {m.outcome?.state === "final" && m.outcome.observed !== null && (
                        <p className="mt-2 text-xs"><b>{t("Actual:")}</b> {m.outcome.observed}</p>)}
                    </div>
                    <div className="text-xs">
                      <div className="mb-1 font-semibold text-muted">{t("Availability")}</div>
                      <p>{t(p.availability?.reason ?? "No injury designation on this week’s report.")}</p>
                      {p.availability?.source && <p className="mt-1 text-muted">{t(p.availability.source)}</p>}
                      <Link href={`/nfl/players/${p.player_id}`} className="mt-2 inline-block font-semibold text-accent hover:underline">{t("Game log →")}</Link>
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

function Chip({ row }: { row: NflThreshold }) {
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

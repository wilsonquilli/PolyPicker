"use client";

import { useEffect, useMemo, useState } from "react";

import { STAT_LABEL, STAT_MARKET, STAT_ORDER, type NflPlayerForecast, type NflStat } from "@/lib/nfl";
import type { CustomLineResult } from "@/lib/types";
import { useT } from "@/components/i18n";

const API = process.env.NEXT_PUBLIC_API_URL ?? "/api/v1";
type Tab = "spread" | "total" | "player";

/** Pick a line, see the model's probability. Nothing is bought or sold. */
export function NflLineExplorer({ gameId, homeAbbr, awayAbbr, spreadMean, totalMean, linesAvailable, players }: {
  gameId: number; homeAbbr: string; awayAbbr: string; spreadMean?: number; totalMean?: number;
  linesAvailable: boolean; players: NflPlayerForecast[];
}) {
  const t = useT();
  const eligible = useMemo(() => players.filter((p) => STAT_ORDER.some((s) => p.markets[s]?.thresholds_available)), [players]);
  const tabs: Tab[] = [...(linesAvailable ? (["spread", "total"] as Tab[]) : []), ...(eligible.length ? (["player"] as Tab[]) : [])];
  const [tab, setTab] = useState<Tab>(tabs[0] ?? "player");
  const [side, setSide] = useState<"home" | "away">(spreadMean !== undefined && spreadMean < 0 ? "away" : "home");
  const [playerId, setPlayerId] = useState<string | undefined>(eligible[0]?.player_id);
  const player = eligible.find((p) => p.player_id === playerId);
  const stats = STAT_ORDER.filter((s) => player?.markets[s]?.thresholds_available);
  const [statPick, setStat] = useState<NflStat | undefined>(undefined);
  const stat = statPick && stats.includes(statPick) ? statPick : stats[0];
  const [kind, setKind] = useState<"over" | "at_least">("at_least");

  const defaultLine = (): number => {
    if (tab === "spread" && spreadMean !== undefined) return -(Math.floor(side === "home" ? spreadMean : -spreadMean) + 0.5);
    if (tab === "total" && totalMean !== undefined) return Math.floor(totalMean) + 0.5;
    const e = (stat && player?.markets[stat]?.expected) ?? 10;
    if (stat === "scrimmage_tds" || stat === "passing_tds") return kind === "at_least" ? 1 : 0.5;
    return kind === "at_least" ? Math.max(1, Math.round(e)) : Math.floor(e) + 0.5;
  };
  const ctxKey = `${tab}|${side}|${playerId}|${stat}|${kind}`;
  const [edit, setEdit] = useState<{ key: string; line: number } | null>(null);
  const line = edit?.key === ctxKey ? edit.line : defaultLine();
  const big = stat === "passing_yards" || stat === "rushing_yards" || stat === "receiving_yards";
  const step = tab === "player" ? (big ? 5 : 1) : 0.5;
  const setLine = (f: (l: number) => number) => setEdit({ key: ctxKey, line: f(line) });

  const params = new URLSearchParams({ threshold: String(line) });
  if (tab === "spread") { params.set("market", "spread"); params.set("side", side); }
  if (tab === "total") { params.set("market", "total"); params.set("threshold_kind", "over"); }
  if (tab === "player" && stat && playerId) {
    params.set("market", STAT_MARKET[stat]); params.set("player_id", playerId); params.set("threshold_kind", kind);
  }
  const query = params.toString();
  const [result, setResult] = useState<{ query: string; res: CustomLineResult | null; err: string | null } | null>(null);
  const loading = result?.query !== query;

  useEffect(() => {
    if (tab === "player" && (!playerId || !stat)) return;
    const ctrl = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const r = await fetch(`${API}/nfl/games/${gameId}/predictions/custom?${query}`, { signal: ctrl.signal });
        const body = await r.json();
        setResult(r.ok ? { query, res: body.data, err: null } : { query, res: null, err: body.error?.message ?? "Unavailable" });
      } catch (e) {
        if ((e as Error).name !== "AbortError") setResult({ query, res: null, err: "Calculator unavailable" });
      }
    }, 120);
    return () => { clearTimeout(timer); ctrl.abort(); };
  }, [query, gameId, tab, playerId, stat]);

  if (!tabs.length) return null;
  const res = result?.res ?? null, err = result?.err ?? null;
  const minLine = tab === "player" ? (kind === "at_least" ? 1 : 0.5) : tab === "total" ? 10 : -40;

  return (
    <div className="rounded-2xl border border-line bg-surface p-4 shadow-card">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold">{t("Line explorer")}</h2>
        <span className="text-[11px] text-muted">{t("Model probability")}</span>
      </div>
      <div className="mb-4 grid gap-1 rounded-xl bg-surface-2 p-1 text-sm font-medium" style={{ gridTemplateColumns: `repeat(${tabs.length}, 1fr)` }} role="tablist">
        {tabs.map((tm) => (
          <button key={tm} role="tab" aria-selected={tab === tm} onClick={() => setTab(tm)}
            className={`rounded-lg py-1.5 capitalize ${tab === tm ? "bg-surface text-ink shadow-card" : "text-muted"}`}>{t(tm === "player" ? "Player" : tm === "spread" ? "Spread" : "Total")}</button>
        ))}
      </div>
      {tab === "spread" && (
        <div className="mb-3 grid grid-cols-2 gap-2">
          {(["away", "home"] as const).map((s) => (
            <button key={s} onClick={() => setSide(s)} aria-pressed={side === s}
              className={`rounded-xl border py-2.5 text-sm font-semibold ${side === s ? "border-accent bg-accent-soft text-accent" : "border-line text-muted"}`}>
              {s === "home" ? homeAbbr : awayAbbr}
            </button>
          ))}
        </div>
      )}
      {tab === "player" && (
        <div className="mb-3 flex flex-col gap-2">
          <label className="text-xs text-muted" htmlFor="nfl-lc-player">{t("Player")}</label>
          <select id="nfl-lc-player" value={playerId} onChange={(e) => setPlayerId(e.target.value)}
            className="h-10 rounded-xl border border-line bg-surface-2 px-3 text-sm">
            {eligible.map((p) => <option key={p.player_id} value={p.player_id}>{p.full_name ?? p.player_id}{p.position ? ` (${p.position})` : ""}</option>)}
          </select>
          <div className="flex flex-wrap gap-1 rounded-xl bg-surface-2 p-1 text-xs font-medium">
            {stats.map((s) => (
              <button key={s} onClick={() => setStat(s)} aria-pressed={stat === s}
                className={`flex-1 whitespace-nowrap rounded-lg px-2 py-1.5 ${stat === s ? "bg-surface text-ink shadow-card" : "text-muted"}`}>{t(STAT_LABEL[s])}</button>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-1 rounded-xl bg-surface-2 p-1 text-xs font-medium">
            {([["at_least", t("At least (k+)")], ["over", t("Over line")]] as const).map(([k, l]) => (
              <button key={k} onClick={() => setKind(k)} aria-pressed={kind === k}
                className={`rounded-lg py-1.5 ${kind === k ? "bg-surface text-ink shadow-card" : "text-muted"}`}>{l}</button>
            ))}
          </div>
        </div>
      )}
      <div className="mb-4">
        <div className="mb-1 flex justify-between text-xs text-muted">
          <span>{t(tab === "spread" ? "Handicap" : tab === "total" ? "Total line" : kind === "at_least" ? "Threshold" : "Line")}</span>
          <span className="tabular">{t("expected")} {res ? res.expected.toFixed(1) : "—"}</span>
        </div>
        <div className="flex items-center gap-2">
          <button aria-label={t("Decrease line")} onClick={() => setLine((l) => Math.max(minLine, +(l - step).toFixed(1)))}
            className="h-11 w-11 rounded-xl border border-line text-lg font-semibold hover:bg-surface-2">−</button>
          <div className="tabular flex h-11 flex-1 items-center justify-center rounded-xl bg-surface-2 text-lg font-bold" aria-live="polite">
            {tab === "spread" && line > 0 ? "+" : ""}{line}{tab === "player" && kind === "at_least" ? "+" : ""}
          </div>
          <button aria-label={t("Increase line")} onClick={() => setLine((l) => +(l + step).toFixed(1))}
            className="h-11 w-11 rounded-xl border border-line text-lg font-semibold hover:bg-surface-2">+</button>
        </div>
      </div>
      <div className={`rounded-xl bg-accent-soft p-4 transition-opacity ${loading ? "opacity-60" : ""}`} aria-live="polite">
        {err ? <p className="text-sm text-muted">{t(err)}</p> : res ? (
          <>
            <div className="text-xs font-medium text-accent">{t(res.label).replace(/^(home|el local)\b/, homeAbbr).replace(/^(away|el visitante)\b/, awayAbbr)}</div>
            <div className="tabular mt-1 text-3xl font-bold text-accent">{(res.p_hit * 100).toFixed(1)}%</div>
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
              <div className="rounded-lg bg-surface px-2 py-1.5"><span className="text-muted">{t("Doesn’t hit")}{" "}</span><b className="tabular">{(res.p_miss * 100).toFixed(1)}%</b></div>
              <div className="rounded-lg bg-surface px-2 py-1.5"><span className="text-muted">{t("Push")}{" "}</span><b className="tabular">{res.p_push > 0 ? `${(res.p_push * 100).toFixed(1)}%` : t("n/a")}</b></div>
            </div>
          </>
        ) : <p className="text-sm text-muted">{t("Choose a line.")}</p>}
      </div>
      <p className="mt-3 text-[11px] leading-relaxed text-muted">
        {t("Computed from the published forecast distribution. Whole-number lines can push; “60+” means 60 or more, “over 59.5” means 60 or more. Informational only.")}
      </p>
    </div>
  );
}

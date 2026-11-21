"use client";

import { useEffect, useMemo, useState } from "react";

import type { CustomLineResult, PlayerForecast } from "@/lib/types";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

type Tab = "spread" | "total" | "player";
type Stat = "points" | "assists" | "rebounds";

/**
 * The side panel. Where a prediction market has an order ticket, PolyPicker has a line explorer:
 * pick a line and see the model's probability for it. Nothing is bought or sold.
 */
export function LineCalculator({ gameId, homeAbbr, awayAbbr, spreadMean, totalMean, linesAvailable, players }: {
  gameId: number; homeAbbr: string; awayAbbr: string; spreadMean?: number; totalMean?: number;
  linesAvailable: boolean; players: PlayerForecast[];
}) {
  const tabs: Tab[] = linesAvailable ? ["spread", "total", "player"] : ["player"];
  const [tab, setTab] = useState<Tab>(tabs[0]);
  const [side, setSide] = useState<"home" | "away">(spreadMean !== undefined && spreadMean < 0 ? "away" : "home");
  const eligible = useMemo(() => players.filter((p) => p.markets.points?.thresholds_available), [players]);
  const [playerId, setPlayerId] = useState<number | undefined>(eligible[0]?.player_id);
  const [stat, setStat] = useState<Stat>("points");
  const [kind, setKind] = useState<"over" | "at_least">("at_least");

  const player = eligible.find((p) => p.player_id === playerId);
  const defaultLine = (t: Tab): number => {
    if (t === "spread" && spreadMean !== undefined) {
      const m = side === "home" ? spreadMean : -spreadMean;
      return -(Math.floor(m) + 0.5);
    }
    if (t === "total" && totalMean !== undefined) return Math.floor(totalMean) + 0.5;
    const e = player?.markets[stat]?.expected ?? 10;
    return kind === "at_least" ? Math.round(e) : Math.floor(e) + 0.5;
  };
  // The line resets to a sensible default whenever the context (tab/side/player/stat/kind) changes;
  // a user edit only applies to the context it was made in. Derived, so no effect is needed.
  const ctxKey = `${tab}|${side}|${playerId}|${stat}|${kind}`;
  const [edit, setEdit] = useState<{ key: string; line: number } | null>(null);
  const line = edit?.key === ctxKey ? edit.line : defaultLine(tab);
  const setLine = (f: (l: number) => number) => setEdit({ key: ctxKey, line: f(line) });

  const params = new URLSearchParams({ threshold: String(line) });
  if (tab === "spread") { params.set("market", "spread"); params.set("side", side); }
  if (tab === "total") { params.set("market", "total"); params.set("threshold_kind", "over"); }
  if (tab === "player") {
    params.set("market", `player_${stat}`); params.set("player_id", String(playerId)); params.set("threshold_kind", kind);
  }
  const query = params.toString();
  const [result, setResult] = useState<{ query: string; res: CustomLineResult | null; err: string | null } | null>(null);
  const loading = result?.query !== query;
  const res = result?.res ?? null;
  const err = result?.err ?? null;

  useEffect(() => {
    if (tab === "player" && !playerId) return;
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      try {
        const r = await fetch(`${API}/games/${gameId}/predictions/custom?${query}`, { signal: ctrl.signal });
        const body = await r.json();
        setResult(r.ok ? { query, res: body.data, err: null } : { query, res: null, err: body.error?.message ?? "Unavailable" });
      } catch (e) {
        if ((e as Error).name !== "AbortError") setResult({ query, res: null, err: "Calculator unavailable" });
      }
    }, 120);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [query, gameId, tab, playerId]);

  const step = tab === "player" && kind === "at_least" ? 1 : tab === "player" ? 1 : 0.5;
  const minLine = tab === "player" ? (kind === "at_least" ? 1 : 0.5) : tab === "total" ? 150 : -40;

  return (
    <div className="rounded-2xl border border-line bg-surface p-4 shadow-card">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold">Line explorer</h2>
        <span className="text-[11px] text-muted">Model probability</span>
      </div>
      <div className="mb-4 grid gap-1 rounded-xl bg-surface-2 p-1 text-sm font-medium" style={{ gridTemplateColumns: `repeat(${tabs.length}, 1fr)` }} role="tablist">
        {tabs.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
            className={`rounded-lg py-1.5 capitalize ${tab === t ? "bg-surface text-ink shadow-card" : "text-muted"}`}>
            {t === "player" ? "Player" : t}
          </button>
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
        eligible.length === 0 ? <p className="mb-3 text-sm text-muted">No player forecasts with calibrated thresholds for this game.</p> : (
          <div className="mb-3 flex flex-col gap-2">
            <label className="text-xs text-muted" htmlFor="lc-player">Player</label>
            <select id="lc-player" value={playerId} onChange={(e) => setPlayerId(Number(e.target.value))}
              className="h-10 rounded-xl border border-line bg-surface-2 px-3 text-sm">
              {eligible.map((p) => <option key={p.player_id} value={p.player_id}>{p.full_name ?? p.player_id}</option>)}
            </select>
            <div className="grid grid-cols-3 gap-1 rounded-xl bg-surface-2 p-1 text-xs font-medium">
              {(["points", "assists", "rebounds"] as Stat[]).map((s) => (
                <button key={s} onClick={() => setStat(s)} aria-pressed={stat === s}
                  className={`rounded-lg py-1.5 capitalize ${stat === s ? "bg-surface text-ink shadow-card" : "text-muted"}`}>{s}</button>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-1 rounded-xl bg-surface-2 p-1 text-xs font-medium">
              {([["at_least", "At least (k+)"], ["over", "Over line"]] as const).map(([k, l]) => (
                <button key={k} onClick={() => setKind(k)} aria-pressed={kind === k}
                  className={`rounded-lg py-1.5 ${kind === k ? "bg-surface text-ink shadow-card" : "text-muted"}`}>{l}</button>
              ))}
            </div>
          </div>
        )
      )}

      <div className="mb-4">
        <div className="mb-1 flex justify-between text-xs text-muted">
          <span>{tab === "spread" ? "Handicap" : tab === "total" ? "Total line" : kind === "at_least" ? "Threshold" : "Line"}</span>
          <span className="tabular">expected {res ? res.expected.toFixed(1) : "—"}</span>
        </div>
        <div className="flex items-center gap-2">
          <button aria-label="Decrease line" onClick={() => setLine((l) => Math.max(minLine, +(l - step).toFixed(1)))}
            className="h-11 w-11 rounded-xl border border-line text-lg font-semibold hover:bg-surface-2">−</button>
          <div className="tabular flex h-11 flex-1 items-center justify-center rounded-xl bg-surface-2 text-lg font-bold" aria-live="polite">
            {tab === "spread" && line > 0 ? "+" : ""}{line}{tab === "player" && kind === "at_least" ? "+" : ""}
          </div>
          <button aria-label="Increase line" onClick={() => setLine((l) => +(l + step).toFixed(1))}
            className="h-11 w-11 rounded-xl border border-line text-lg font-semibold hover:bg-surface-2">+</button>
        </div>
      </div>

      <div className={`rounded-xl bg-accent-soft p-4 transition-opacity ${loading ? "opacity-60" : ""}`} aria-live="polite">
        {err ? <p className="text-sm text-muted">{err}</p> : res ? (
          <>
            <div className="text-xs font-medium text-accent">{res.label.replace(/^home\b/, homeAbbr).replace(/^away\b/, awayAbbr)}</div>
            <div className="tabular mt-1 text-3xl font-bold text-accent">{(res.p_hit * 100).toFixed(1)}%</div>
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
              <div className="rounded-lg bg-surface px-2 py-1.5"><span className="text-muted">Doesn’t hit </span><b className="tabular">{(res.p_miss * 100).toFixed(1)}%</b></div>
              <div className="rounded-lg bg-surface px-2 py-1.5"><span className="text-muted">Push </span><b className="tabular">{res.p_push > 0 ? `${(res.p_push * 100).toFixed(1)}%` : "n/a"}</b></div>
            </div>
          </>
        ) : <p className="text-sm text-muted">Choose a line.</p>}
      </div>
      <p className="mt-3 text-[11px] leading-relaxed text-muted">
        Computed from the published forecast distribution. Whole-number lines can push (exact landing); “20+” means 20 or more,
        “over 20.5” means 21 or more. Informational only.
      </p>
    </div>
  );
}

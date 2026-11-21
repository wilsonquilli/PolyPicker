import Link from "next/link";
import { Suspense } from "react";

import { CalibrationChart } from "@/components/charts";
import { Card, EmptyState, Pill, SectionTitle, Skeleton, StatTile } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";

export const metadata = { title: "Accuracy" };

type SP = Promise<{ [k: string]: string | string[] | undefined }>;

interface MarketPerf {
  graded_states?: Record<string, number>;
  probability_metrics?: { n: number; brier: number; log_loss: number; hit_rate: number };
  mae?: { n: number; value: number };
  calibration?: { bucket: number; n: number; mean_predicted: number; observed_rate: number }[];
  winner_accuracy?: { n: number; value: number };
}
interface Perf {
  markets: Record<string, MarketPerf>;
  active_models: { target: string; version: string; metrics: Record<string, unknown>; validation_start: string; validation_end: string }[];
}
interface HistoryRow {
  prediction_id: number; game_id: number; game_date: string; kind: string; market: string; label: string; side: string | null;
  probability: number | null; projection: number | null; graded_state: string; outcome: string | null; observed_value: number | null;
  matchup: string; home_score: number | null; away_score: number | null; subject_name: string | null;
}

const MARKETS = [
  { id: "moneyline", label: "Winner" }, { id: "spread", label: "Spread" }, { id: "total", label: "Total" },
  { id: "player_points", label: "Points" }, { id: "player_assists", label: "Assists" }, { id: "player_rebounds", label: "Rebounds" },
];

export default function AccuracyPage({ searchParams }: { searchParams: SP }) {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Accuracy &amp; calibration</h1>
        <p className="max-w-3xl text-sm text-muted">
          Every published forecast is kept unchanged and graded against the final result. A well-calibrated model’s 70% calls
          should happen about 70% of the time.
        </p>
      </div>
      <Suspense fallback={<div className="grid gap-4 lg:grid-cols-3"><Skeleton className="h-72" /><Skeleton className="h-72 lg:col-span-2" /></div>}>
        <Accuracy searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function Accuracy({ searchParams }: { searchParams: SP }) {
  const sp = await searchParams;
  const kind = sp.kind === "live" ? "live" : "backtest";
  const market = MARKETS.some((m) => m.id === sp.market) ? String(sp.market) : "moneyline";
  const [perfRes, histRes] = await Promise.all([
    api<Perf>("/models/performance", { kind }),
    api<HistoryRow[]>("/predictions/history", { kind, category: market === "moneyline" ? "winner" : market.replace("player_", ""), state: "final", probabilities_only: "true", favorites_only: "true", limit: 25 }),
  ]);
  const perf = perfRes?.data;
  const ml = perf?.markets.moneyline;
  const m = perf?.markets[market];
  const gameModel = perf?.active_models.find((x) => x.target === "game");
  const val = gameModel?.metrics as { moneyline?: { brier: number; accuracy: number }; moneyline_baseline_elo?: { brier: number };
    moneyline_baseline_home_rate?: { brier: number } } | undefined;
  const q = (o: Record<string, string>) => `/accuracy?${new URLSearchParams({ kind, market, ...o })}`;

  return (
    <>
      <div className="flex flex-wrap gap-2">
        <div className="flex gap-1 rounded-xl bg-surface-2 p-1 text-sm font-medium">
          {(["backtest", "live"] as const).map((k) => (
            <Link key={k} href={q({ kind: k })} aria-current={kind === k ? "page" : undefined}
              className={`rounded-lg px-3 py-1.5 ${kind === k ? "bg-surface text-ink shadow-card" : "text-muted"}`}>
              {k === "live" ? "Published live" : "Backtest 2026-27"}
            </Link>
          ))}
        </div>
        <div className="no-scrollbar flex gap-1 overflow-x-auto rounded-xl bg-surface-2 p-1 text-sm font-medium">
          {MARKETS.map((x) => (
            <Link key={x.id} href={q({ market: x.id })} aria-current={market === x.id ? "page" : undefined}
              className={`whitespace-nowrap rounded-lg px-3 py-1.5 ${market === x.id ? "bg-surface text-ink shadow-card" : "text-muted"}`}>{x.label}</Link>
          ))}
        </div>
      </div>

      {kind === "backtest" && (
        <p className="rounded-xl bg-warn-soft px-4 py-2.5 text-sm text-warn">
          Backtest forecasts were regenerated after the games, using only data available before each game day and models
          trained on earlier seasons. They show expected accuracy; they were not published live.
        </p>
      )}

      {!ml?.winner_accuracy ? (
        <EmptyState title="No graded forecasts yet">
          {kind === "live" ? "Live forecasts are graded as games finish. Check back after the next slate." : "Run a backtest to populate this view."}
        </EmptyState>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatTile label="Winner accuracy" value={`${(ml.winner_accuracy.value * 100).toFixed(1)}%`} sub={`${ml.winner_accuracy.n} games · favorite won`} />
            <StatTile label="Winner Brier" value={ml.probability_metrics?.brier.toFixed(3) ?? "—"} sub="lower is better · coin flip = 0.250" />
            <StatTile label="Score error" value={perf?.markets.team_points?.mae ? `${perf.markets.team_points.mae.value.toFixed(1)} pts` : "—"} sub="avg. per team" />
            <StatTile label="Points prop error" value={perf?.markets.player_points?.mae ? `${perf.markets.player_points.mae.value.toFixed(2)} pts` : "—"} sub="avg. per player" />
          </div>

          <div className="grid items-start gap-6 lg:grid-cols-[380px_minmax(0,1fr)]">
            <Card className="p-5">
              <SectionTitle>Calibration · {MARKETS.find((x) => x.id === market)?.label}</SectionTitle>
              {m?.calibration?.length ? <CalibrationChart bins={m.calibration} /> : <p className="text-sm text-muted">No probability forecasts graded for this market.</p>}
              {m?.probability_metrics && (
                <div className="tabular mt-3 grid grid-cols-3 gap-2 text-center text-xs">
                  <div className="rounded-lg bg-surface-2 p-2"><div className="text-muted">Brier</div><b>{m.probability_metrics.brier.toFixed(3)}</b></div>
                  <div className="rounded-lg bg-surface-2 p-2"><div className="text-muted">Log loss</div><b>{m.probability_metrics.log_loss.toFixed(3)}</b></div>
                  <div className="rounded-lg bg-surface-2 p-2"><div className="text-muted">Graded</div><b>{m.probability_metrics.n.toLocaleString()}</b></div>
                </div>
              )}
              {m?.graded_states && (
                <p className="mt-3 text-xs text-muted">
                  {Object.entries(m.graded_states).map(([k, v]) => `${v.toLocaleString()} ${k}`).join(" · ")}
                  {m.graded_states.void ? " (void = player didn’t play)" : ""}
                </p>
              )}
            </Card>

            <Card className="overflow-x-auto">
              <div className="px-5 pt-4"><SectionTitle>Recently graded</SectionTitle></div>
              <table className="tabular w-full whitespace-nowrap text-sm">
                <thead><tr className="text-[11px] uppercase tracking-wide text-muted">
                  {["Date", "Game", "Forecast", "Prob.", "Actual", "Result"].map((h) => <th key={h} className="px-3 py-2 text-left font-semibold first:pl-5">{h}</th>)}
                </tr></thead>
                <tbody>
                  {(histRes?.data ?? []).map((r) => (
                    <tr key={r.prediction_id} className="border-t border-line">
                      <td className="py-2 pl-5 pr-3"><Link href={`/games/${r.game_id}`} className="hover:underline">{shortDate(r.game_date)}</Link></td>
                      <td className="px-3 text-muted">{r.matchup}</td>
                      <td className="max-w-[300px] truncate px-3">{describe(r)}</td>
                      <td className="px-3 font-semibold">{r.probability !== null ? `${Math.round(r.probability * 100)}%` : r.projection?.toFixed(1)}</td>
                      <td className="px-3 text-muted">{actual(r)}</td>
                      <td className="px-3">{r.outcome ? <Pill tone={r.outcome === "hit" ? "win" : r.outcome === "miss" ? "loss" : "neutral"}>{r.outcome}</Pill> : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          </div>
        </>
      )}

      {val?.moneyline && (
        <Card className="p-5">
          <SectionTitle>Held-out validation ({gameModel?.validation_start?.slice(0, 4)}-{gameModel?.validation_end?.slice(2, 4)} season)</SectionTitle>
          <p className="mb-3 text-sm text-muted">Measured before the model was deployed, against simple baselines. Lower Brier is better.</p>
          <div className="grid gap-2 sm:grid-cols-3">
            <StatTile label="PolyPicker model" value={val.moneyline.brier.toFixed(4)} sub={`${(val.moneyline.accuracy * 100).toFixed(1)}% winners`} />
            <StatTile label="Elo rating baseline" value={val.moneyline_baseline_elo?.brier.toFixed(4) ?? "—"} />
            <StatTile label="Always pick home" value={val.moneyline_baseline_home_rate?.brier.toFixed(4) ?? "—"} />
          </div>
        </Card>
      )}
    </>
  );
}

function describe(r: HistoryRow): string {
  if (r.market === "moneyline") return `${r.subject_name} to win`;
  if (r.market.startsWith("player_")) return `${r.subject_name}: ${r.label}`;
  const [away, home] = r.matchup.split(" @ ");
  return r.label.replace(/^home\b/, home).replace(/^away\b/, away);
}

function actual(r: HistoryRow): string {
  if (r.market === "moneyline" || r.market === "spread") {
    const [away, home] = r.matchup.split(" @ ");
    return `${away} ${r.away_score}–${r.home_score} ${home}`;
  }
  return r.observed_value !== null ? String(r.observed_value) : "—";
}

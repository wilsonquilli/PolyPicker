import Link from "next/link";
import { Suspense } from "react";

import { CalibrationChart } from "@/components/charts";
import { Card, EmptyState, Pill, SectionTitle, Skeleton, StatTile } from "@/components/ui";
import { api } from "@/lib/api";
import { getT } from "@/lib/i18n/server";

export async function generateMetadata() {
  const t = await getT();
  return { title: t("NFL accuracy") };
}

type SP = Promise<{ [k: string]: string | string[] | undefined }>;
interface MarketPerf {
  graded_states?: Record<string, number>;
  probability_metrics?: { n: number; brier: number; log_loss: number; hit_rate: number; mean_predicted: number };
  mae?: { n: number; value: number };
  calibration?: { bucket: number; n: number; mean_predicted: number; observed_rate: number }[];
  winner_accuracy?: { n: number; value: number };
}
interface Perf {
  markets: Record<string, MarketPerf>;
  market_benchmark: { n: number; brier: number; accuracy: number; margin_mae: number; total_mae: number; note: string } | null;
  active_models: { target: string; version: string; metrics: Record<string, unknown>; validation_start: string; validation_end: string }[];
}

const MARKETS = [
  { id: "moneyline", label: "Winner" }, { id: "spread", label: "Spread" }, { id: "total", label: "Total" },
  { id: "player_passing_yards", label: "Pass yds" }, { id: "player_passing_tds", label: "Pass TD" },
  { id: "player_rushing_yards", label: "Rush yds" }, { id: "player_receptions", label: "Receptions" },
  { id: "player_receiving_yards", label: "Rec yds" }, { id: "player_anytime_td", label: "Anytime TD" },
];
const TARGET: Record<string, string> = {
  player_passing_yards: "nfl_player_passing_yards", player_passing_tds: "nfl_player_passing_tds",
  player_rushing_yards: "nfl_player_rushing_yards", player_receptions: "nfl_player_receptions",
  player_receiving_yards: "nfl_player_receiving_yards", player_anytime_td: "nfl_player_scrimmage_tds",
};

export default async function NflAccuracyPage({ searchParams }: { searchParams: SP }) {
  const t = await getT();
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{t("{league} accuracy & calibration", { league: "NFL" })}</h1>
        <p className="max-w-3xl text-sm text-muted">
          {t("Every forecast is kept unchanged and graded against the final result. A well-calibrated model’s 70% calls should happen about 70% of the time. The betting market is shown only as a yardstick; it is never a model input.")}
        </p>
      </div>
      <Suspense fallback={<div className="grid gap-4 lg:grid-cols-3"><Skeleton className="h-72" /><Skeleton className="h-72 lg:col-span-2" /></div>}>
        <Accuracy searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function Accuracy({ searchParams }: { searchParams: SP }) {
  const t = await getT();
  const sp = await searchParams;
  const kind = sp.kind === "live" ? "live" : "backtest";
  const market = MARKETS.some((m) => m.id === sp.market) ? String(sp.market) : "moneyline";
  const perf = (await api<Perf>("/nfl/models/performance", { kind }))?.data;
  const ml = perf?.markets.moneyline;
  const m = perf?.markets[market];
  const bench = perf?.market_benchmark;
  const gameModel = perf?.active_models.find((x) => x.target === "nfl_game");
  const q = (o: Record<string, string>) => `/nfl/accuracy?${new URLSearchParams({ kind, market, ...o })}`;
  const val = gameModel?.metrics as { moneyline?: { brier: number; accuracy: number }; moneyline_benchmark_market?: { brier: number; accuracy: number };
    moneyline_baseline_elo?: { brier: number }; moneyline_baseline_home_rate?: { brier: number }; validation_games?: number } | undefined;

  return (
    <>
      <div className="flex flex-wrap gap-2">
        <div className="flex gap-1 rounded-xl bg-surface-2 p-1 text-sm font-medium">
          {(["backtest", "live"] as const).map((k) => (
            <Link key={k} href={q({ kind: k })} aria-current={kind === k ? "page" : undefined}
              className={`rounded-lg px-3 py-1.5 ${kind === k ? "bg-surface text-ink shadow-card" : "text-muted"}`}>
              {k === "backtest" ? t("Backtest {s}", { s: "2026" }) : t("Published live")}</Link>
          ))}
        </div>
        <div className="no-scrollbar flex max-w-full gap-1 overflow-x-auto rounded-xl bg-surface-2 p-1 text-sm font-medium">
          {MARKETS.map((x) => (
            <Link key={x.id} href={q({ market: x.id })} aria-current={market === x.id ? "page" : undefined}
              className={`shrink-0 whitespace-nowrap rounded-lg px-3 py-1.5 ${market === x.id ? "bg-surface text-ink shadow-card" : "text-muted"}`}>{t(x.label)}</Link>
          ))}
        </div>
      </div>
      {kind === "backtest" && (
        <p className="rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn">
          {t("Backtest forecasts were regenerated after the games, using only data available before each game day and models trained on earlier seasons. They show expected accuracy; they were not published live.")}
        </p>
      )}
      {!ml?.winner_accuracy ? (
        <EmptyState title={t("Nothing graded yet")}>{kind === "live" ? t("Live accuracy builds up as published forecasts are graded after each game.") : t("No backtest results.")}</EmptyState>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label={t("Winner accuracy")} value={`${(ml.winner_accuracy.value * 100).toFixed(1)}%`} sub={t("{n} games · favorite won", { n: ml.winner_accuracy.n })} />
            <StatTile label={t("Winner Brier")} value={ml.probability_metrics ? (ml.probability_metrics.brier).toFixed(3) : "—"} sub={t("lower is better · coin flip = 0.250")} />
            <StatTile label={t("Market Brier (same games)")} value={bench ? bench.brier.toFixed(3) : "—"} sub={bench ? t("market favorite won {p}%", { p: (bench.accuracy * 100).toFixed(1) }) : t("yardstick only")} />
            <StatTile label={t("Score error")} value={perf?.markets.team_points?.mae ? `${perf.markets.team_points.mae.value.toFixed(1)} ${t("pts")}` : "—"} sub={t("avg. per team")} />
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            <Card className="p-5">
              <SectionTitle>{t("Calibration")} · {t(MARKETS.find((x) => x.id === market)?.label ?? "")}</SectionTitle>
              {m?.calibration?.length ? <CalibrationChart bins={m.calibration} /> : <p className="text-sm text-muted">{t("No graded probabilities for this market yet.")}</p>}
              {m?.probability_metrics && (
                <p className="mt-2 text-xs text-muted">{t("Average forecast {a}% vs. actual hit rate {h}% over {n} graded lines.", { a: (m.probability_metrics.mean_predicted * 100).toFixed(1), h: (m.probability_metrics.hit_rate * 100).toFixed(1), n: m.probability_metrics.n.toLocaleString() })}</p>
              )}
            </Card>
            <Card className="overflow-x-auto p-5 lg:col-span-2">
              <SectionTitle>{t("All markets")}</SectionTitle>
              <table className="tabular w-full text-sm">
                <thead><tr className="text-[11px] uppercase tracking-wide text-muted">
                  <th className="py-2 text-left font-semibold">{t("Market")}</th><th className="text-right font-semibold">{t("Graded lines")}</th>
                  <th className="text-right font-semibold">{t("Avg forecast")}</th><th className="text-right font-semibold">{t("Hit rate")}</th>
                  <th className="text-right font-semibold">Brier</th><th className="text-right font-semibold">{t("MAE")}</th></tr></thead>
                <tbody>
                  {MARKETS.map((x) => {
                    const r = perf?.markets[x.id];
                    const pm = r?.probability_metrics;
                    return (
                      <tr key={x.id} className="border-t border-line">
                        <td className="py-2"><Link href={q({ market: x.id })} className="hover:underline">{t(x.label)}</Link>
                          {(x.id === "player_passing_tds" || x.id === "player_anytime_td") && <Pill tone="warn" className="ml-1">{t("Beta")}</Pill>}</td>
                        <td className="text-right">{pm?.n?.toLocaleString() ?? "—"}</td>
                        <td className="text-right">{pm ? `${(pm.mean_predicted * 100).toFixed(1)}%` : "—"}</td>
                        <td className="text-right">{pm ? `${(pm.hit_rate * 100).toFixed(1)}%` : "—"}</td>
                        <td className="text-right">{pm ? pm.brier.toFixed(3) : "—"}</td>
                        <td className="text-right">{r?.mae ? r.mae.value.toFixed(2) : "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <p className="mt-2 text-[11px] text-muted">{t("For well-calibrated thresholds, “avg forecast” and “hit rate” should be close. MAE is for the expected value (projected points, margin, total or player stat).")}</p>
            </Card>
          </div>
        </>
      )}
      {gameModel && val?.moneyline && (
        <Card className="overflow-x-auto p-5">
          <SectionTitle>{t("Validation when the models were trained · {n} games held out ({s} season)", { n: val.validation_games ?? 0, s: gameModel.validation_start?.slice(0, 4) ?? "" })}</SectionTitle>
          <table className="tabular w-full text-sm">
            <thead><tr className="text-[11px] uppercase tracking-wide text-muted"><th className="py-2 text-left font-semibold">{t("Winner forecasts")}</th>
              <th className="text-right font-semibold">Brier</th><th className="text-right font-semibold">{t("Accuracy")}</th></tr></thead>
            <tbody>
              <tr className="border-t border-line"><td className="py-2 font-semibold">{t("PolyPicker model")}</td><td className="text-right">{val.moneyline.brier.toFixed(4)}</td><td className="text-right">{(val.moneyline.accuracy * 100).toFixed(1)}%</td></tr>
              {val.moneyline_baseline_elo && <tr className="border-t border-line"><td className="py-2">{t("Elo rating only")}</td><td className="text-right">{val.moneyline_baseline_elo.brier.toFixed(4)}</td><td /></tr>}
              {val.moneyline_baseline_home_rate && <tr className="border-t border-line"><td className="py-2">{t("Home team always 55%")}</td><td className="text-right">{val.moneyline_baseline_home_rate.brier.toFixed(4)}</td><td /></tr>}
              {val.moneyline_benchmark_market && <tr className="border-t border-line text-muted"><td className="py-2">{t("Betting market closing line (yardstick)")}</td><td className="text-right">{val.moneyline_benchmark_market.brier.toFixed(4)}</td><td className="text-right">{(val.moneyline_benchmark_market.accuracy * 100).toFixed(1)}%</td></tr>}
            </tbody>
          </table>
          <PropValidation models={perf!.active_models} />
          {bench && <p className="mt-3 text-[11px] text-muted">{t(bench.note)}</p>}
        </Card>
      )}
    </>
  );
}

async function PropValidation({ models }: { models: Perf["active_models"] }) {
  const t = await getT();
  const rows = MARKETS.filter((m) => TARGET[m.id]).map((m) => ({ m, model: models.find((x) => x.target === TARGET[m.id]) }))
    .filter((r) => r.model);
  if (!rows.length) return null;
  return (
    <table className="tabular mt-5 w-full text-sm">
      <thead><tr className="text-[11px] uppercase tracking-wide text-muted"><th className="py-2 text-left font-semibold">{t("Player props")}</th>
        <th className="text-right font-semibold">{t("Model MAE")}</th><th className="text-right font-semibold">{t("Season-avg MAE")}</th>
        <th className="text-right font-semibold">{t("Threshold calibration error")}</th></tr></thead>
      <tbody>
        {rows.map(({ m, model }) => {
          const x = model!.metrics as { mae: number; mae_baseline_season_avg: number; threshold?: { ece: number } };
          return (
            <tr key={m.id} className="border-t border-line">
              <td className="py-2">{t(m.label)}</td>
              <td className={`text-right ${x.mae < x.mae_baseline_season_avg ? "font-semibold" : ""}`}>{x.mae.toFixed(2)}</td>
              <td className="text-right text-muted">{x.mae_baseline_season_avg.toFixed(2)}</td>
              <td className="text-right">{x.threshold ? x.threshold.ece.toFixed(3) : "—"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

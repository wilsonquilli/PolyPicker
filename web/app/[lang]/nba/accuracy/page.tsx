import Link from "next/link";
import { Suspense } from "react";

import { CalibrationChart } from "@/components/charts";
import { Card, EmptyState, Pill, SectionTitle, Skeleton, StatTile } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { keyed, type T } from "@/lib/i18n";
import { getLang, getT } from "@/lib/i18n/server";

export async function generateMetadata() {
  const t = await getT();
  return { title: t("Accuracy") };
}

type SP = Promise<{ [k: string]: string | string[] | undefined }>;

interface MarketPerf {
  graded_states?: Record<string, number>;
  probability_metrics?: { n: number; brier: number; log_loss: number; hit_rate: number };
  mae?: { n: number; value: number };
  calibration?: { bucket: number; n: number; mean_predicted: number; observed_rate: number }[];
  winner_accuracy?: { n: number; value: number };
  vs_market?: VsMarket;
  vs_polymarket?: VsMarket;
}
type Score = { accuracy: number; log_loss: number; brier: number };
interface VsMarket { n: number; source?: string; model: Score; market: Score; disagreements?: { n: number; model_right: number | null } }
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

export default async function AccuracyPage({ searchParams }: { searchParams: SP }) {
  const t = await getT();
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{t("Accuracy & calibration")}</h1>
        <p className="max-w-3xl text-sm text-muted">
          {t("Every published forecast is kept unchanged and graded against the final result. A well-calibrated model’s 70% calls should happen about 70% of the time.")}
        </p>
      </div>
      <Suspense fallback={<div className="grid gap-4 lg:grid-cols-3"><Skeleton className="h-72" /><Skeleton className="h-72 lg:col-span-2" /></div>}>
        <Accuracy searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function Accuracy({ searchParams }: { searchParams: SP }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const sp = await searchParams;
  const kind = sp.kind === "live" ? "live" : "backtest";
  const market = MARKETS.some((m) => m.id === sp.market) ? String(sp.market) : "moneyline";
  const [perfRes, histRes] = await Promise.all([
    api<Perf>("/models/performance", { kind }),
    api<HistoryRow[]>("/predictions/history", { kind, category: market === "moneyline" ? "winner" : market.replace("player_", ""), state: "final", probabilities_only: "true", one_per_game: "true", limit: 25 }),
  ]);
  const perf = perfRes?.data;
  const ml = perf?.markets.moneyline;
  const m = perf?.markets[market];
  const gameModel = perf?.active_models.find((x) => x.target === "game");
  const val = gameModel?.metrics as { moneyline?: { brier: number; accuracy: number }; moneyline_baseline_elo?: { brier: number };
    moneyline_baseline_home_rate?: { brier: number }; vs_market?: VsMarket; vs_polymarket?: VsMarket } | undefined;
  const q = (o: Record<string, string>) => `/nba/accuracy?${new URLSearchParams({ kind, market, ...o })}`;

  return (
    <>
      <div className="flex flex-wrap gap-2">
        <div className="flex gap-1 rounded-xl bg-surface-2 p-1 text-sm font-medium">
          {(["backtest", "live"] as const).map((k) => (
            <Link key={k} href={q({ kind: k })} aria-current={kind === k ? "page" : undefined}
              className={`rounded-lg px-3 py-1.5 ${kind === k ? "bg-surface text-ink shadow-card" : "text-muted"}`}>
              {k === "live" ? t("Published live") : t("Backtest {s}", { s: "2026-27" })}
            </Link>
          ))}
        </div>
        <div className="no-scrollbar flex gap-1 overflow-x-auto rounded-xl bg-surface-2 p-1 text-sm font-medium">
          {MARKETS.map((x) => (
            <Link key={x.id} href={q({ market: x.id })} aria-current={market === x.id ? "page" : undefined}
              className={`whitespace-nowrap rounded-lg px-3 py-1.5 ${market === x.id ? "bg-surface text-ink shadow-card" : "text-muted"}`}>{t(x.label)}</Link>
          ))}
        </div>
      </div>

      {kind === "backtest" && (
        <p className="rounded-xl bg-warn-soft px-4 py-2.5 text-sm text-warn">
          {t("Backtest forecasts were regenerated after the games, using only data available before each game day and models trained on earlier seasons. They show expected accuracy; they were not published live.")}
        </p>
      )}

      {!ml?.winner_accuracy ? (
        <EmptyState title={t("No graded forecasts yet")}>
          {kind === "live" ? t("Live forecasts are graded as games finish. Check back after the next slate.") : t("Run a backtest to populate this view.")}
        </EmptyState>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatTile label={t("Winner accuracy")} value={`${(ml.winner_accuracy.value * 100).toFixed(1)}%`} sub={t("{n} games · favorite won", { n: ml.winner_accuracy.n })} />
            <StatTile label={t("Winner Brier")} value={ml.probability_metrics?.brier.toFixed(3) ?? "—"} sub={t("lower is better · coin flip = 0.250")} />
            <StatTile label={t("Score error")} value={perf?.markets.team_points?.mae ? `${perf.markets.team_points.mae.value.toFixed(1)} ${t("pts")}` : "—"} sub={t("avg. per team")} />
            <StatTile label={t("Points prop error")} value={perf?.markets.player_points?.mae ? `${perf.markets.player_points.mae.value.toFixed(2)} ${t("pts")}` : "—"} sub={t("avg. per player")} />
          </div>

          {ml.vs_market && <VsMarketCard v={ml.vs_market} label={t("Sportsbook closing line")} title={kind === "live" ? t("PolyPicker vs the sportsbooks (published forecasts)") : t("PolyPicker vs the sportsbooks (this season’s backtest)")}
            blurb={t("The closing line is the sportsbooks’ final price before tip-off, with their margin removed — the toughest public benchmark there is.")} />}
          {ml.vs_polymarket && <VsMarketCard v={ml.vs_polymarket} label={t("Polymarket")} title={kind === "live" ? t("PolyPicker vs Polymarket (published forecasts)") : t("PolyPicker vs Polymarket (this season’s backtest)")}
            blurb={t("Polymarket’s last traded price for the game-winner market before tip-off: real-money crowd probabilities.")} />}

          <div className="grid items-start gap-6 lg:grid-cols-[380px_minmax(0,1fr)]">
            <Card className="p-5">
              <SectionTitle>{t("Calibration")} · {t(MARKETS.find((x) => x.id === market)?.label ?? "")}</SectionTitle>
              {m?.calibration?.length ? <CalibrationChart bins={m.calibration} /> : <p className="text-sm text-muted">{t("No probability forecasts graded for this market.")}</p>}
              {m?.probability_metrics && (
                <div className="tabular mt-3 grid grid-cols-3 gap-2 text-center text-xs">
                  <div className="rounded-lg bg-surface-2 p-2"><div className="text-muted">Brier</div><b>{m.probability_metrics.brier.toFixed(3)}</b></div>
                  <div className="rounded-lg bg-surface-2 p-2"><div className="text-muted">{t("Log loss")}</div><b>{m.probability_metrics.log_loss.toFixed(3)}</b></div>
                  <div className="rounded-lg bg-surface-2 p-2"><div className="text-muted">{t("Graded")}</div><b>{m.probability_metrics.n.toLocaleString()}</b></div>
                </div>
              )}
              {m?.graded_states && (
                <p className="mt-3 text-xs text-muted">
                  {Object.entries(m.graded_states).map(([k, v]) => `${v.toLocaleString()} ${keyed(t, `state:${k}`, k)}`).join(" · ")}
                  {m.graded_states.void ? ` (${t("void = player didn’t play")})` : ""}
                </p>
              )}
            </Card>

            <Card className="overflow-x-auto">
              <div className="px-5 pt-4"><SectionTitle>{t("Recently graded")}</SectionTitle></div>
              <table className="tabular w-full whitespace-nowrap text-sm">
                <thead><tr className="text-[11px] uppercase tracking-wide text-muted">
                  {["Date", "Game", "Forecast", "Prob.", "Actual", "Result"].map((h) => <th key={h} className="px-3 py-2 text-left font-semibold first:pl-5">{keyed(t, `col:${h}`, h)}</th>)}
                </tr></thead>
                <tbody>
                  {(histRes?.data ?? []).map(favored).map((r) => (
                    <tr key={r.prediction_id} className="border-t border-line">
                      <td className="py-2 pl-5 pr-3"><Link href={`/nba/games/${r.game_id}`} className="hover:underline">{shortDate(r.game_date, lang)}</Link></td>
                      <td className="px-3 text-muted">{r.matchup}</td>
                      <td className="max-w-[300px] truncate px-3">{describe(r, t)}</td>
                      <td className="px-3 font-semibold">{r.probability !== null ? `${Math.round(r.probability * 100)}%` : r.projection?.toFixed(1)}</td>
                      <td className="px-3 text-muted">{actual(r)}</td>
                      <td className="px-3">{r.outcome ? <Pill tone={r.outcome === "hit" ? "win" : r.outcome === "miss" ? "loss" : "neutral"}>{keyed(t, `result:${r.outcome}`, r.outcome)}</Pill> : "—"}</td>
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
          <SectionTitle>{t("Held-out validation ({s} season)", { s: `${gameModel?.validation_start?.slice(0, 4)}-${gameModel?.validation_end?.slice(2, 4)}` })}</SectionTitle>
          <p className="mb-3 text-sm text-muted">{t("Measured before the model was deployed, against simple baselines. Lower Brier is better.")}</p>
          <div className="grid gap-2 sm:grid-cols-3">
            <StatTile label={t("PolyPicker model")} value={val.moneyline.brier.toFixed(4)} sub={t("{p}% winners", { p: (val.moneyline.accuracy * 100).toFixed(1) })} />
            <StatTile label={t("Elo rating baseline")} value={val.moneyline_baseline_elo?.brier.toFixed(4) ?? "—"} />
            <StatTile label={t("Always pick home")} value={val.moneyline_baseline_home_rate?.brier.toFixed(4) ?? "—"} />
          </div>
          {val.vs_market && (
            <p className="mt-3 text-sm text-muted">{t("Sportsbook closing lines on the same {n} games: Brier {b}, {a}% winners (model on those games: {mb}, {ma}%).", {
              n: val.vs_market.n.toLocaleString(), b: val.vs_market.market.brier.toFixed(4), a: (val.vs_market.market.accuracy * 100).toFixed(1),
              mb: val.vs_market.model.brier.toFixed(4), ma: (val.vs_market.model.accuracy * 100).toFixed(1) })}</p>
          )}
          {val.vs_polymarket && (
            <p className="mt-1 text-sm text-muted">{t("Polymarket pregame prices on {n} of those games: Brier {b}, {a}% winners (model: {mb}, {ma}%).", {
              n: val.vs_polymarket.n.toLocaleString(), b: val.vs_polymarket.market.brier.toFixed(4), a: (val.vs_polymarket.market.accuracy * 100).toFixed(1),
              mb: val.vs_polymarket.model.brier.toFixed(4), ma: (val.vs_polymarket.model.accuracy * 100).toFixed(1) })}</p>
          )}
        </Card>
      )}
    </>
  );
}

async function VsMarketCard({ v, title, label, blurb }: { v: VsMarket; title: string; label: string; blurb: string }) {
  const t = await getT();
  const rows = [[t("PolyPicker model"), v.model], [label, v.market]] as const;
  const best = (k: keyof Score, lowerBetter: boolean) => (lowerBetter ? v.model[k] <= v.market[k] : v.model[k] >= v.market[k]) ? 0 : 1;
  const win = { accuracy: best("accuracy", false), log_loss: best("log_loss", true), brier: best("brier", true) };
  return (
    <Card className="overflow-x-auto p-5">
      <SectionTitle>{title}</SectionTitle>
      <p className="mb-3 text-sm text-muted">{t("Same {n} games.", { n: v.n.toLocaleString() })} {blurb}</p>
      <table className="tabular w-full text-sm">
        <thead><tr className="text-[11px] uppercase tracking-wide text-muted">
          <th className="py-2 text-left font-semibold">{t("Forecaster")}</th><th className="text-right font-semibold">{t("Winners right")}</th>
          <th className="text-right font-semibold">{t("Log loss")}</th><th className="text-right font-semibold">Brier</th></tr></thead>
        <tbody>
          {rows.map(([label, s], i) => (
            <tr key={label} className="border-t border-line">
              <td className="py-2 font-medium">{label}</td>
              <td className={`text-right ${win.accuracy === i ? "font-bold text-accent" : ""}`}>{(s.accuracy * 100).toFixed(1)}%</td>
              <td className={`text-right ${win.log_loss === i ? "font-bold text-accent" : ""}`}>{s.log_loss.toFixed(3)}</td>
              <td className={`text-right ${win.brier === i ? "font-bold text-accent" : ""}`}>{s.brier.toFixed(3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {v.disagreements && v.disagreements.n > 0 && v.disagreements.model_right !== null && (
        <p className="mt-3 text-xs text-muted">{t("They picked different winners in {n} games; the model was right in {p}% of those.", { n: v.disagreements.n, p: (v.disagreements.model_right * 100).toFixed(0) })}</p>
      )}
      <p className="mt-1 text-[11px] text-faint">{t("Source: {s}. Lower log loss / Brier is better.", { s: t(v.source ?? "") })}</p>
    </Card>
  );
}

// Line markets store the "over"/"home covers" side; show whichever side the model favored, like the winner rows.
function favored(r: HistoryRow): HistoryRow {
  if (r.market === "moneyline" || r.probability === null || r.probability >= 0.5) return r;
  const label = r.label
    .replace(/^(home|away) ([+-])/, (_, t, s) => `${t === "home" ? "away" : "home"} ${s === "-" ? "+" : "-"}`)
    .replace(/^over (\S+) (.*?)( \(i\.e\. .*\))?$/, "under $1 $2");
  const outcome = r.outcome === "hit" ? "miss" : r.outcome === "miss" ? "hit" : r.outcome;
  return { ...r, label, probability: 1 - r.probability, outcome };
}

function describe(r: HistoryRow, t: T): string {
  if (r.market === "moneyline") return t("{team} to win", { team: r.subject_name ?? "" });
  if (r.market.startsWith("player_")) return `${r.subject_name}: ${t(r.label)}`;
  const [away, home] = r.matchup.split(" @ ");
  return t(r.label).replace(/^(home|el local)\b/, home).replace(/^(away|el visitante)\b/, away);
}

function actual(r: HistoryRow): string {
  if (r.market === "moneyline" || r.market === "spread") {
    const [away, home] = r.matchup.split(" @ ");
    return `${away} ${r.away_score}–${r.home_score} ${home}`;
  }
  return r.observed_value !== null ? String(r.observed_value) : "—";
}

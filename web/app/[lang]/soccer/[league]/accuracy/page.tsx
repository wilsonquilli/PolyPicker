import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { AccuracyHero, CalibrationCard, KindNote, mergeBins } from "@/components/accuracy";
import { CalibrationChart } from "@/components/charts";
import { EmptyState, SectionTitle, Skeleton, StatTile } from "@/components/ui";
import { api } from "@/lib/api";
import { soccerLeague, type SoccerLeague } from "@/lib/soccer";
import { LineMovement } from "@/components/LineMovement";
import { getT } from "@/lib/i18n/server";

type LParams = Promise<{ league: string }>;
export async function generateMetadata({ params }: { params: LParams }) {
  const lg = soccerLeague((await params).league);
  const t = await getT();
  return lg ? { title: t("{league} accuracy", { league: lg.name }) } : {};
}
type SP = Promise<{ [k: string]: string | string[] | undefined }>;
type Bin = { bucket: number; n: number; mean_predicted: number; observed_rate: number };
type Three = { n: number; rps: number; log_loss: number; brier: number; accuracy: number };
interface Perf {
  match_result: { n: number; accuracy: number; rps: number; log_loss: number; mean_draw: number; draw_rate: number; home_rate: number;
    strong_n: number; strong_accuracy: number | null; strong_threshold: number; dnb_n: number; dnb_accuracy: number | null; dc_accuracy: number } | null;
  calibration: Record<string, Bin[]>;
  lines: { market: string; threshold: number | null; n: number; mean_predicted: number; hit_rate: number; brier: number }[];
  mae: Record<string, { n: number; value: number }>;
  active_model: { version: string; validation_start: string; metrics: { validation_matches: number; match_result: Three;
    match_result_baseline_frequencies: Three; match_result_baseline_elo: Three; draw_rate_actual: number; draw_rate_predicted: number;
    goals_mae: number; goals_mae_baseline_train_mean: number } } | null;
}

export default function AccuracyPage({ params, searchParams }: { params: LParams; searchParams: SP }) {
  return <Suspense fallback={<Skeleton className="h-96" />}><AccuracyBody params={params} searchParams={searchParams} /></Suspense>;
}

async function AccuracyBody({ params, searchParams }: { params: LParams; searchParams: SP }) {
  const t = await getT();
  const lg = soccerLeague((await params).league);
  if (!lg) notFound();
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{t("{league} accuracy", { league: lg.name })}</h1>
      </div>
      <Suspense fallback={<Skeleton className="h-72" />}><Accuracy lg={lg} searchParams={searchParams} /></Suspense>
    </div>
  );
}

async function Accuracy({ lg, searchParams }: { lg: SoccerLeague; searchParams: SP }) {
  const t = await getT();
  const sp = await searchParams;
  const kind = sp.kind === "live" ? "live" : "backtest";
  const perf = (await api<Perf>(`${lg.api}/models/performance`, { kind }))?.data;
  const mr = perf?.match_result;
  const v = perf?.active_model?.metrics;
  const tab = (k: string, label: string) => (
    <Link key={k} href={`${lg.base}/accuracy?kind=${k}`} aria-current={kind === k ? "page" : undefined}
      className={`rounded-lg px-3 py-1.5 ${kind === k ? "bg-surface text-ink shadow-card" : "text-muted"}`}>{label}</Link>
  );
  return (
    <>
      <div className="flex gap-1 self-start rounded-xl bg-surface-2 p-1 text-sm font-medium">{tab("backtest", t("Backtest this season"))}{tab("live", t("Published live"))}</div>
      <KindNote kind={kind} unit="match" />
      {!mr ? <EmptyState title={t("Nothing graded yet")}>{kind === "live" ? t("Live accuracy builds up as published forecasts are graded after each match.") : t("No backtest results.")}</EmptyState> : (
        <>
          {mr.strong_n > 0 && mr.strong_accuracy !== null ? (
            <AccuracyHero n={mr.strong_n} value={mr.strong_accuracy} unit="match"
              title={t("Strong picks: right in {w} of {n} matches", { w: Math.round(mr.strong_accuracy * mr.strong_n).toLocaleString(), n: mr.strong_n.toLocaleString() })}
              sub={t("We only make a strong pick when one result is rated at least {t} likely ({n} of {m} matches). The rest are close to a coin flip. How that compares:", {
                t: `${Math.round(mr.strong_threshold * 100)}%`, n: mr.strong_n.toLocaleString(), m: mr.n.toLocaleString() })}
              rows={[
                { label: t("Strong picks"), value: mr.strong_accuracy, ours: true },
                { label: t("All matches (every pick)"), value: mr.accuracy, sub: t("{n} matches", { n: mr.n.toLocaleString() }) },
                { label: t("Always pick the home team"), value: mr.home_rate },
                { label: t("Random guess"), value: 1 / 3 },
              ]} />
          ) : (
            <AccuracyHero n={mr.n} value={mr.accuracy} unit="match"
              title={t("Called the right result in {w} of {n} matches", { w: Math.round(mr.accuracy * mr.n).toLocaleString(), n: mr.n.toLocaleString() })}
              sub={t("The result we rated most likely (home win, draw or away win) happened {p} of the time. Soccer has three possible results, so this is harder than it sounds. How that compares:", { p: `${(mr.accuracy * 100).toFixed(1)}%` })}
              rows={[
                { label: t("PolyPicker model"), value: mr.accuracy, ours: true },
                { label: t("Always pick the home team"), value: mr.home_rate },
                { label: t("Random guess"), value: 1 / 3 },
              ]} />
          )}
          <div>
            <SectionTitle>{t("Safer picks")}</SectionTitle>
            <div className="tabular grid grid-cols-1 gap-2 sm:grid-cols-2">
              <StatTile label={t("Double chance: favourite or draw")} value={`${(mr.dc_accuracy * 100).toFixed(1)}%`} sub={t("{n} matches", { n: mr.n.toLocaleString() })} />
              {mr.dnb_accuracy !== null && <StatTile label={t("Draw no bet: favourite (draws void)")} value={`${(mr.dnb_accuracy * 100).toFixed(1)}%`} sub={t("{n} matches without a draw", { n: mr.dnb_n.toLocaleString() })} />}
            </div>
            <p className="mt-2 text-xs text-muted">{t("Two lower-risk ways to back the same forecast, graded on the same matches.")}</p>
          </div>
          <p className="-mt-2 text-sm text-muted">{t("Draws are the hardest result to call: we expected a draw in {a}% of matches on average, and {b}% ended level.", { a: (mr.mean_draw * 100).toFixed(0), b: (mr.draw_rate * 100).toFixed(0) })}</p>
          <CalibrationCard label={t("Home / draw / away")}
            bins={mergeBins(["match_result:home", "match_result:draw", "match_result:away"].map((k) => perf?.calibration[k] ?? []))}
            advanced={(
              <>
                <div className="tabular mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
                  <StatTile label={t("RPS")} value={mr.rps.toFixed(3)} sub={t("lower is better")} />
                  <StatTile label={t("Log loss")} value={mr.log_loss.toFixed(3)} sub={t("always guessing ⅓ each = 1.099")} />
                </div>
                <p className="mt-2 text-xs text-muted">{t("The ranked probability score (RPS) is the standard measure for home / draw / away forecasts: lower is better.")}</p>
                <div className="mt-3 grid gap-4 sm:grid-cols-3">
                  {[["match_result:home", "Home win"], ["match_result:draw", "Draw"], ["match_result:away", "Away win"]].map(([k, l]) => (
                    <div key={k}>
                      <div className="mb-1 text-xs font-semibold text-muted">{t(l)}</div>
                      {perf?.calibration[k]?.length ? <CalibrationChart bins={perf.calibration[k]} size={260} /> : <p className="text-sm text-muted">{t("No data.")}</p>}
                    </div>
                  ))}
                </div>
              </>
            )} />
          <Suspense fallback={<Skeleton className="h-64" />}><LineMovement base={lg.api} kind={kind} unit="match" /></Suspense>
          <details className="rounded-2xl border border-line bg-surface shadow-card">
            <summary className="cursor-pointer px-5 py-4 font-semibold">{t("Advanced stats: goal lines & training validation")}</summary>
            <div className="overflow-x-auto px-5 pb-5">
          {perf!.lines.length > 0 && (
            <div className="mb-6">
              <SectionTitle>{t("Goal lines")}</SectionTitle>
              <table className="tabular w-full text-sm">
                <thead><tr className="text-[11px] uppercase tracking-wide text-muted"><th className="py-2 text-left font-semibold">{t("Market")}</th>
                  <th className="text-right font-semibold">{t("Graded")}</th><th className="text-right font-semibold">{t("Avg forecast")}</th>
                  <th className="text-right font-semibold">{t("Happened")}</th><th className="text-right font-semibold">Brier</th></tr></thead>
                <tbody>
                  {perf!.lines.map((l) => (
                    <tr key={`${l.market}${l.threshold}`} className="border-t border-line">
                      <td className="py-2">{l.market === "btts" ? t("Both teams score") : t("Over {n} goals", { n: l.threshold ?? "" })}</td>
                      <td className="text-right">{l.n}</td><td className="text-right">{(l.mean_predicted * 100).toFixed(1)}%</td>
                      <td className="text-right">{(l.hit_rate * 100).toFixed(1)}%</td><td className="text-right">{l.brier.toFixed(3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {perf!.mae.team_goals && <p className="mb-6 text-xs text-muted">{t("Expected goals per team were off by {n} goals on average.", { n: perf!.mae.team_goals.value.toFixed(2) })}</p>}
      {v && (
        <div>
          <SectionTitle>{t("Validation when the model was trained · {n} matches held out ({s} season)", { n: v.validation_matches, s: `${perf!.active_model!.validation_start.slice(0, 4)}-${String(Number(perf!.active_model!.validation_start.slice(0, 4)) + 1).slice(2)}` })}</SectionTitle>
          <table className="tabular w-full text-sm">
            <thead><tr className="text-[11px] uppercase tracking-wide text-muted"><th className="py-2 text-left font-semibold">{t("Home / draw / away")}</th>
              <th className="text-right font-semibold">RPS</th><th className="text-right font-semibold">{t("Log loss")}</th><th className="text-right font-semibold">{t("Pick right")}</th></tr></thead>
            <tbody>
              {([["PolyPicker model", v.match_result, true], ["Elo rating + home advantage only", v.match_result_baseline_elo, false],
                ["Historical home / draw / away frequencies", v.match_result_baseline_frequencies, false]] as const).map(([l, x, bold]) => (
                <tr key={l} className="border-t border-line"><td className={`py-2 ${bold ? "font-semibold" : ""}`}>{t(l)}</td>
                  <td className="text-right">{x.rps.toFixed(4)}</td><td className="text-right">{x.log_loss.toFixed(4)}</td><td className="text-right">{(x.accuracy * 100).toFixed(1)}%</td></tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-[11px] text-muted">{t("Draws: model forecast {a}% on average, {b}% happened. Goals per team: model error {c} vs {d} for the league average.", {
            a: (v.draw_rate_predicted * 100).toFixed(1), b: (v.draw_rate_actual * 100).toFixed(1), c: v.goals_mae.toFixed(2), d: v.goals_mae_baseline_train_mean.toFixed(2) })}</p>
        </div>
      )}
            </div>
          </details>
        </>
      )}
    </>
  );
}

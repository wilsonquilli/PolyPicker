import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { CalibrationChart } from "@/components/charts";
import { Card, EmptyState, SectionTitle, Skeleton, StatTile } from "@/components/ui";
import { api } from "@/lib/api";
import { soccerLeague, type SoccerLeague } from "@/lib/soccer";
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
  match_result: { n: number; accuracy: number; rps: number; log_loss: number; mean_draw: number; draw_rate: number; home_rate: number } | null;
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
        <h1 className="text-2xl font-bold tracking-tight">{t("{league} accuracy & calibration", { league: lg.name })}</h1>
        <p className="max-w-3xl text-sm text-muted">{t("Every forecast is kept unchanged and graded after the match. Soccer has three outcomes, so a coin-flip pick would be right about a third of the time. The ranked probability score (RPS) is the standard measure for home / draw / away forecasts: lower is better.")}</p>
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
      {kind === "backtest" && <p className="rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn">{t("Backtest forecasts were regenerated after the matches using only data available before each match day, with a model trained on earlier seasons. They show expected accuracy; they were not published live.")}</p>}
      {!mr ? <EmptyState title={t("Nothing graded yet")}>{kind === "live" ? t("Live accuracy builds up as published forecasts are graded after each match.") : t("No backtest results.")}</EmptyState> : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label={t("Most likely result happened")} value={`${(mr.accuracy * 100).toFixed(1)}%`} sub={t("{n} matches", { n: mr.n })} />
            <StatTile label={t("RPS")} value={mr.rps.toFixed(3)} sub={t("lower is better")} />
            <StatTile label={t("Log loss")} value={mr.log_loss.toFixed(3)} sub={t("always guessing ⅓ each = 1.099")} />
            <StatTile label={t("Draws")} value={t("{p}% forecast", { p: (mr.mean_draw * 100).toFixed(0) })} sub={t("{p}% happened", { p: (mr.draw_rate * 100).toFixed(0) })} />
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            {[["match_result:home", "Home win"], ["match_result:draw", "Draw"], ["match_result:away", "Away win"]].map(([k, l]) => (
              <Card key={k} className="p-5">
                <SectionTitle>{t("Calibration")} · {t(l)}</SectionTitle>
                {perf?.calibration[k]?.length ? <CalibrationChart bins={perf.calibration[k]} size={260} /> : <p className="text-sm text-muted">{t("No data.")}</p>}
              </Card>
            ))}
          </div>
          {perf!.lines.length > 0 && (
            <Card className="overflow-x-auto p-5">
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
              {perf!.mae.team_goals && <p className="mt-2 text-[11px] text-muted">{t("Expected goals per team were off by {n} goals on average.", { n: perf!.mae.team_goals.value.toFixed(2) })}</p>}
            </Card>
          )}
        </>
      )}
      {v && (
        <Card className="overflow-x-auto p-5">
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
        </Card>
      )}
    </>
  );
}

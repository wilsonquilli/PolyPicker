// Plain-language building blocks shared by the NBA, NFL and soccer accuracy pages.
import type { ReactNode } from "react";
import { Card, SectionTitle } from "@/components/ui";
import { getT } from "@/lib/i18n/server";

export type CalBin = { bucket: number; n: number; mean_predicted: number; observed_rate: number };
/** log_loss is optional: some benchmarks only report Brier. */
export type Score = { accuracy: number; brier: number; log_loss?: number };
export interface VsMarket { n: number; model: Score; market: Score }

/** Below this many graded games, numbers swing a lot from week to week; say so. */
export const SMALL_SAMPLE = 100;
const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

/** One line under the tabs explaining what "backtest" and "published live" mean. */
export async function KindNote({ kind, unit = "game" }: { kind: "backtest" | "live"; unit?: "game" | "match" }) {
  const t = await getT();
  const text = kind === "live"
    ? unit === "match" ? t("Published live: every forecast was locked in before kick-off and never edited afterwards.") : t("Published live: every forecast was locked in before tip-off and never edited afterwards.")
    : unit === "match" ? t("Backtest: how the model would have done this season, using only information available before each match.") : t("Backtest: how the model would have done this season, using only information available before each game.");
  return <p className="-mt-3 text-sm text-muted">{text}</p>;
}

export type HeroRow = { label: string; value: number; sub?: string; ours?: boolean };

/** Headline ("Picked the winner in 168 of 236 games") over bars people can compare against. */
export async function AccuracyHero({ title, value, sub, rows, n, unit = "game" }: {
  title: string; value: number; sub: string; rows: HeroRow[]; n: number; unit?: "game" | "match";
}) {
  const t = await getT();
  const sorted = [...rows].sort((a, b) => b.value - a.value || Number(!!b.ours) - Number(!!a.ours));
  return (
    <Card className="p-5">
      <div className="text-xl font-bold tracking-tight sm:text-2xl">{title} <span className="text-accent">({pct(value)})</span></div>
      <p className="mt-1 text-sm text-muted">{sub}</p>
      <div className="mt-4 flex flex-col gap-2">
        {sorted.map((r) => (
          <div key={r.label} className="grid grid-cols-[minmax(0,10rem)_1fr_3.5rem] items-center gap-3 text-sm">
            <div className={`truncate ${r.ours ? "font-semibold" : "text-muted"}`}>{r.label}{r.sub && <div className="truncate text-[11px] text-muted">{r.sub}</div>}</div>
            <div className="h-2.5 rounded-full bg-surface-2">
              <div className={`h-full rounded-full ${r.ours ? "bg-accent" : "bg-faint"}`} style={{ width: `${r.value * 100}%` }} />
            </div>
            <div className={`tabular text-right ${r.ours ? "font-bold text-accent" : ""}`}>{pct(r.value)}</div>
          </div>
        ))}
      </div>
      {n < SMALL_SAMPLE && (
        <p className="mt-3 text-xs text-warn">
          {unit === "match" ? t("Only {n} matches graded so far, so these numbers can still swing a lot.", { n }) : t("Only {n} games graded so far, so these numbers can still swing a lot.", { n })}
        </p>
      )}
    </Card>
  );
}

/** Calibration as plain rows: "when we said ~75%, it happened 75% of the time (52 forecasts)".
 *  `winner`: two-way winner calibration counts both teams of each game, so only the favorite half is shown. */
export async function CalibrationList({ bins, winner = false }: { bins: CalBin[]; winner?: boolean }) {
  const t = await getT();
  const shown = bins.filter((b) => b.n > 0 && (!winner || b.mean_predicted >= 0.5)).sort((a, b) => b.mean_predicted - a.mean_predicted);
  const total = bins.reduce((s, b) => s + b.n, 0);
  const gap = total ? bins.reduce((s, b) => s + b.n * Math.abs(b.mean_predicted - b.observed_rate), 0) / total : 0;
  return (
    <>
      <div className="flex flex-col gap-1.5">
        <div className="grid grid-cols-[4.5rem_1fr_4.5rem] gap-3 text-[11px] font-semibold uppercase tracking-wide text-muted">
          <div>{t("We said")}</div><div>{t("It happened")}</div><div className="text-right">{t("Forecasts")}</div>
        </div>
        {shown.map((b) => (
          <div key={b.bucket} className={`tabular grid grid-cols-[4.5rem_1fr_4.5rem] items-center gap-3 text-sm ${b.n < 20 ? "opacity-50" : ""}`}>
            <div className="font-medium">~{Math.round(b.mean_predicted * 100)}%</div>
            <div className="relative h-5 rounded bg-surface-2">
              <div className="h-full rounded bg-accent/70" style={{ width: `${b.observed_rate * 100}%` }} />
              <div className="absolute inset-y-0 w-0.5 bg-ink" style={{ left: `${b.mean_predicted * 100}%` }} title={t("What we said")} />
              <span className="absolute inset-y-0 right-1.5 flex items-center text-xs font-semibold">{Math.round(b.observed_rate * 100)}%</span>
            </div>
            <div className="text-right text-muted">{b.n.toLocaleString()}</div>
          </div>
        ))}
      </div>
      <p className="mt-3 text-xs text-muted">
        {t("Bar = how often it happened; black line = what we said. On average our calls were {g} points off their stated odds.", { g: (gap * 100).toFixed(1) })}
        {shown.some((b) => b.n < 20) ? ` ${t("Faded rows have fewer than 20 forecasts.")}` : ""}
      </p>
    </>
  );
}

/** Merge several calibration series (e.g. home / draw / away) into one, bucket by bucket. */
export function mergeBins(series: CalBin[][]): CalBin[] {
  const by = new Map<number, { n: number; p: number; o: number }>();
  for (const b of series.flat()) {
    const x = by.get(b.bucket) ?? { n: 0, p: 0, o: 0 };
    x.n += b.n; x.p += b.n * b.mean_predicted; x.o += b.n * b.observed_rate;
    by.set(b.bucket, x);
  }
  return [...by.entries()].filter(([, x]) => x.n > 0)
    .map(([bucket, x]) => ({ bucket, n: x.n, mean_predicted: x.p / x.n, observed_rate: x.o / x.n }));
}

/** Card titled "When we say X%, ..." with the plain list on top and the chart / raw stats folded away. */
export async function CalibrationCard({ label, bins, winner, advanced }: { label: string; bins: CalBin[] | undefined; winner?: boolean; advanced?: ReactNode }) {
  const t = await getT();
  return (
    <Card className="p-5">
      <SectionTitle>{t("When we say X%, does it happen X% of the time?")} · {label}</SectionTitle>
      <p className="mb-3 text-sm text-muted">{t("A trustworthy forecast’s 70% calls should come true about 70% of the time.")}</p>
      {bins?.length ? <CalibrationList bins={bins} winner={winner} /> : <p className="text-sm text-muted">{t("No probability forecasts graded for this market.")}</p>}
      {advanced && (
        <details className="mt-4 text-sm">
          <summary className="cursor-pointer font-medium text-muted">{t("Chart & advanced stats")}</summary>
          {advanced}
        </details>
      )}
    </Card>
  );
}

/** Model vs a market on the same games: plain verdict, a ✓ table, raw scores folded away. */
export async function VsMarketCard({ v, title, label, who, blurb, footnote }: {
  v: VsMarket; title: string; label: string; who: string; blurb: string; footnote?: string;
}) {
  const t = await getT();
  const rows = [[t("PolyPicker model"), v.model], [label, v.market]] as const;
  const accDiff = v.model.accuracy - v.market.accuracy;
  const hasLL = v.model.log_loss !== undefined && v.market.log_loss !== undefined;
  // Probability quality: only call a winner when every available score agrees.
  const lower = (a: Score, b: Score) => a.brier < b.brier && (!hasLL || a.log_loss! < b.log_loss!);
  const probWinner = lower(v.model, v.market) ? 0 : lower(v.market, v.model) ? 1 : null;
  const winners = Math.abs(accDiff) < 0.005
    ? t("Picked as many winners as {who}.", { who })
    : accDiff > 0 ? t("Picked more winners than {who} ({a} vs {b}).", { who, a: pct(v.model.accuracy), b: pct(v.market.accuracy) })
    : t("Picked fewer winners than {who} ({a} vs {b}).", { who, a: pct(v.model.accuracy), b: pct(v.market.accuracy) });
  const probs = probWinner === 0 ? t("Our percentages were closer to what actually happened.")
    : probWinner === 1 ? t("Their percentages were a bit closer to what actually happened.")
    : t("Our percentages were about as accurate as theirs.");
  const accBest = accDiff >= 0 ? 0 : 1;
  return (
    <Card className="overflow-x-auto p-5">
      <SectionTitle>{title}</SectionTitle>
      <p className="font-medium">{winners} {probs}</p>
      <p className="mb-3 mt-1 text-sm text-muted">{t("Same {n} games.", { n: v.n.toLocaleString() })} {blurb}</p>
      <table className="tabular w-full text-sm">
        <thead><tr className="text-[11px] uppercase tracking-wide text-muted">
          <th className="py-2 text-left font-semibold">{t("Forecaster")}</th>
          <th className="text-right font-semibold">{t("Winners right")}</th>
          <th className="text-right font-semibold">{t("Closer percentages")}</th></tr></thead>
        <tbody>
          {rows.map(([name, s], i) => (
            <tr key={name} className="border-t border-line">
              <td className="py-2 font-medium">{name}</td>
              <td className={`text-right ${accBest === i ? "font-bold text-accent" : ""}`}>{pct(s.accuracy)}</td>
              <td className="text-right">{probWinner === i ? <span className="font-bold text-accent">✓</span> : probWinner === null ? <span className="text-muted">{t("tie")}</span> : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <details className="mt-3 text-sm">
        <summary className="cursor-pointer font-medium text-muted">{t("Advanced stats")}</summary>
        <table className="tabular mt-2 w-full text-sm">
          <thead><tr className="text-[11px] uppercase tracking-wide text-muted">
            <th className="py-2 text-left font-semibold">{t("Forecaster")}</th>
            <th className="text-right font-semibold">Brier</th>{hasLL && <th className="text-right font-semibold">{t("Log loss")}</th>}</tr></thead>
          <tbody>
            {rows.map(([name, s]) => (
              <tr key={name} className="border-t border-line">
                <td className="py-2">{name}</td><td className="text-right">{s.brier.toFixed(4)}</td>
                {hasLL && <td className="text-right">{s.log_loss!.toFixed(4)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-xs text-muted">{t("Brier score and log loss measure how close each forecaster’s percentages were to what happened. Lower is better.")}</p>
        {footnote && <p className="mt-1 text-xs text-muted">{footnote}</p>}
      </details>
    </Card>
  );
}

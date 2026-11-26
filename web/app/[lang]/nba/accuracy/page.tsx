import Link from "next/link";
import { Suspense } from "react";
import { AccuracyHero, CalibrationCard, KindNote, VsMarketCard, type CalBin, type VsMarket } from "@/components/accuracy";
import { CalibrationChart } from "@/components/charts";
import { Card, EmptyState, Pill, SectionTitle, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { keyed, type T } from "@/lib/i18n";
import { LineMovement } from "@/components/LineMovement";
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
  calibration?: CalBin[];
  winner_accuracy?: { n: number; value: number };
  vs_market?: VsMarket;
  vs_polymarket?: VsMarket;
}
interface Perf {
  markets: Record<string, MarketPerf>;
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
        <h1 className="text-2xl font-bold tracking-tight">{t("Accuracy")}</h1>
      </div>
      <Suspense fallback={<div className="grid gap-4 lg:grid-cols-3"><Skeleton className="h-72" /><Skeleton className="h-72 lg:col-span-2" /></div>}>
        <Accuracy searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

/** Share of graded games the home team won: the "always pick the home team" baseline. */
async function homeWinRate(kind: string): Promise<number | null> {
  let home = 0, total = 0;
  for (let offset = 0; offset < 3000; offset += 500) {
    const res = await api<HistoryRow[]>("/predictions/history", { kind, category: "winner", state: "final", probabilities_only: "true", one_per_game: "true", limit: 500, offset });
    const rows = res?.data ?? [];
    for (const r of rows) {
      if (r.home_score === null || r.away_score === null || r.home_score === r.away_score) continue;
      total++;
      if (r.home_score > r.away_score) home++;
    }
    if (rows.length < 500) break;
  }
  return total ? home / total : null;
}

async function Accuracy({ searchParams }: { searchParams: SP }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const sp = await searchParams;
  const kind = sp.kind === "live" ? "live" : "backtest";
  const market = MARKETS.some((m) => m.id === sp.market) ? String(sp.market) : "moneyline";
  const [perfRes, histRes, homeRate] = await Promise.all([
    api<Perf>("/models/performance", { kind }),
    api<HistoryRow[]>("/predictions/history", { kind, category: market === "moneyline" ? "winner" : market.replace("player_", ""), state: "final", probabilities_only: "true", one_per_game: "true", limit: 25 }),
    homeWinRate(kind),
  ]);
  const perf = perfRes?.data;
  const ml = perf?.markets.moneyline;
  const m = perf?.markets[market];
  const marketLabel = t(MARKETS.find((x) => x.id === market)?.label ?? "");
  const q = (o: Record<string, string>) => `/nba/accuracy?${new URLSearchParams({ kind, market, ...o })}`;
  const recent = (histRes?.data ?? []).map(favored);
  const hits = recent.filter((r) => r.outcome === "hit").length;
  const misses = recent.filter((r) => r.outcome === "miss").length;

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

      <KindNote kind={kind} />
      {!ml?.winner_accuracy ? (
        <EmptyState title={t("No graded forecasts yet")}>
          {kind === "live" ? t("Live forecasts are graded as games finish. Check back after the next slate.") : t("Run a backtest to populate this view.")}
        </EmptyState>
      ) : (
        <>
          <WinnerHero ml={ml} homeRate={homeRate} />

          {ml.vs_market && <VsMarketCard v={ml.vs_market} label={t("Sportsbook closing line")} who={t("the sportsbooks")}
            title={kind === "live" ? t("PolyPicker vs the sportsbooks (published forecasts)") : t("PolyPicker vs the sportsbooks (this season’s backtest)")}
            blurb={t("The closing line is the sportsbooks’ final price before tip-off, with their margin removed — the toughest public benchmark there is.")} />}
          {ml.vs_polymarket && <VsMarketCard v={ml.vs_polymarket} label={t("Polymarket")} who={t("Polymarket")}
            title={kind === "live" ? t("PolyPicker vs Polymarket (published forecasts)") : t("PolyPicker vs Polymarket (this season’s backtest)")}
            blurb={t("Polymarket’s last traded price for the game-winner market before tip-off: real-money crowd probabilities.")} />}

          <div className="grid items-start gap-6 lg:grid-cols-[380px_minmax(0,1fr)]">
            <CalibrationCard label={marketLabel} bins={m?.calibration} winner={market === "moneyline"} advanced={(m?.calibration?.length || m?.probability_metrics) ? (
              <>
                {m?.calibration?.length ? <div className="mt-3"><CalibrationChart bins={m.calibration} /></div> : null}
                {m?.probability_metrics && (
                  <>
                    <div className="tabular mt-3 grid grid-cols-3 gap-2 text-center text-xs">
                      <div className="rounded-lg bg-surface-2 p-2"><div className="text-muted">Brier</div><b>{m.probability_metrics.brier.toFixed(3)}</b></div>
                      <div className="rounded-lg bg-surface-2 p-2"><div className="text-muted">{t("Log loss")}</div><b>{m.probability_metrics.log_loss.toFixed(3)}</b></div>
                      <div className="rounded-lg bg-surface-2 p-2"><div className="text-muted">{t("Graded")}</div><b>{m.probability_metrics.n.toLocaleString()}</b></div>
                    </div>
                    <p className="mt-2 text-xs text-muted">{t("Brier score and log loss measure how close our percentages were to what happened. Lower is better; always saying 50% scores a Brier of 0.250.")}</p>
                  </>
                )}
                {m?.graded_states && (
                  <p className="mt-2 text-xs text-muted">
                    {Object.entries(m.graded_states).map(([k, v]) => `${v.toLocaleString()} ${keyed(t, `state:${k}`, k)}`).join(" · ")}
                    {m.graded_states.void ? ` (${t("void = player didn’t play")})` : ""}
                  </p>
                )}
              </>
            ) : undefined} />

            <Card className="overflow-x-auto">
              <div className="px-5 pt-4">
                <SectionTitle action={hits + misses > 0 ? (
                  <span className="tabular text-sm font-semibold">{t("Last {n}:", { n: hits + misses })} <span className="text-win">{hits}</span>–<span className="text-loss">{misses}</span></span>
                ) : undefined}>{t("Recently graded")}</SectionTitle>
              </div>
              <table className="tabular w-full whitespace-nowrap text-sm">
                <thead><tr className="text-[11px] uppercase tracking-wide text-muted">
                  {["Date", "Game", "Forecast", "Prob.", "Actual", "Result"].map((h) => <th key={h} className="px-3 py-2 text-left font-semibold first:pl-5">{keyed(t, `col:${h}`, h)}</th>)}
                </tr></thead>
                <tbody>
                  {recent.map((r) => (
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
          <Suspense fallback={<Skeleton className="h-64" />}><LineMovement base="" kind={kind} /></Suspense>
        </>
      )}
    </>
  );
}

/** Headline: winners picked, next to the bars people can compare against (coin flip, home team, markets). */
async function WinnerHero({ ml, homeRate }: { ml: MarketPerf; homeRate: number | null }) {
  const t = await getT();
  const wa = ml.winner_accuracy!;
  const p = `${(wa.value * 100).toFixed(1)}%`;
  const games = (n: number) => (n !== wa.n ? t("on {n} games with prices", { n: n.toLocaleString() }) : undefined);
  return (
    <AccuracyHero n={wa.n} value={wa.value}
      title={t("Picked the winner in {w} of {n} games", { w: Math.round(wa.value * wa.n).toLocaleString(), n: wa.n.toLocaleString() })}
      sub={t("The team we gave the better chance won {p} of the time. How that compares:", { p })}
      rows={[
        { label: t("PolyPicker model"), value: wa.value, ours: true },
        ...(ml.vs_market ? [{ label: t("Sportsbooks"), value: ml.vs_market.market.accuracy, sub: games(ml.vs_market.n) }] : []),
        ...(ml.vs_polymarket ? [{ label: t("Polymarket"), value: ml.vs_polymarket.market.accuracy, sub: games(ml.vs_polymarket.n) }] : []),
        ...(homeRate !== null ? [{ label: t("Always pick the home team"), value: homeRate }] : []),
        { label: t("Coin flip"), value: 0.5 },
      ]} />
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

import Link from "next/link";
import { Suspense } from "react";

import { Card, EmptyState, Pill, SectionTitle, Skeleton, StatTile, TeamBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTimeET, shortDate } from "@/lib/format";
import { keyed, type T } from "@/lib/i18n";
import { getLang, getT } from "@/lib/i18n/server";

export async function generateMetadata() {
  const t = await getT();
  return { title: t("NBA value finder"), description: t("Where PolyPicker's probability beats the best available price, and how those bets have done.") };
}

interface Pick {
  id: number; game_id: number; market: string; player_id: number | null; player_name: string | null; side: string; line: number | null;
  our_p: number; price_dec: number; price_american: string | null; book: string; ev: number; kelly: number;
  flagged_price_dec: number; tipoff_utc: string; home: string; away: string; player_team: string | null;
}
interface Bucket { min_ev: number; bets: number; win_rate?: number | null; roi_flat?: number; units_flat?: number; avg_ev?: number; avg_clv_prob?: number | null; beat_close_rate?: number | null }
interface Record_ {
  live: { market: string; n: number; win_rate: number | null; units: number; roi: number; avg_clv: number | null; beat_close_rate: number | null }[];
  recent: { game_id: number; market: string; player_name: string | null; side: string; line: number | null; price_american: string | null; book: string;
    ev: number; result: string; profit: number; beat_close: boolean | null; game_date_et: string; home: string; away: string }[];
  backtests: { market: string; season: number; created_at: string; report: { priced_at: string; forecast: string; buckets: Bucket[]; games?: number; props_priced?: number } }[];
}

const STAT = { player_points: "points", player_rebounds: "rebounds", player_assists: "assists" } as Record<string, string>;
const pct = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`);
const signedPct = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(d)}%`);
const seasonLabel = (s: number) => `${s}-${String(s + 1).slice(2)}`;

function betLabel(p: { market: string; player_name: string | null; side: string; line: number | null; home: string; away: string }, t: T) {
  if (p.market === "moneyline") return t("{team} to win", { team: p.side === "home" ? p.home : p.away });
  return `${p.player_name ?? t("Player")} ${t(`${p.side} ${p.line} ${STAT[p.market] ?? ""}`.trim())}`;
}

export default async function ValuePage() {
  const t = await getT();
  return (
    <div className="flex flex-col gap-6">
      <div>
        <p className="text-xs font-semibold uppercase tracking-wider text-accent">{t("NBA · Value finder")}</p>
        <h1 className="text-2xl font-bold tracking-tight">{t("Where our numbers beat the price")}</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">{t("A bet has value when PolyPicker’s probability is higher than the price implies. Expected value (EV) is the average profit per $1 staked if our probability is right. We record every pick at the price we saw and check afterwards whether the market moved our way (closing-line value), the best long-run test of a real edge.")}</p>
      </div>
      <p className="rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn">{t("Informational only, not financial advice. Most bettors lose over time; even positive-EV bets lose often. Only bet what you can afford to lose. 21+ where legal. Problem gambling help: 1-800-GAMBLER.")}</p>
      <Suspense fallback={<Skeleton className="h-96" />}><Body /></Suspense>
    </div>
  );
}

async function Body() {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const [picksRes, recRes] = await Promise.all([api<Pick[]>("/value/nba"), api<Record_>("/value/nba/record")]);
  const picks = picksRes?.data ?? [];
  const rec = recRes?.data;
  const winners = picks.filter((p) => p.market === "moneyline"), props = picks.filter((p) => p.market !== "moneyline");
  return (
    <>
      <section>
        <SectionTitle>{t("Upcoming value bets")}</SectionTitle>
        {picks.length === 0 ? <EmptyState title={t("No value bets right now")}>{t("Picks appear once lines are posted and our forecast beats the best price by at least 3%.")}</EmptyState> : (
          <div className="flex flex-col gap-4">
            {winners.length > 0 && <PickTable title={t("Game winners")} picks={winners} />}
            {props.length > 0 && <PickTable title={t("Player props")} picks={props} />}
          </div>
        )}
      </section>

      <section className="flex flex-col gap-3">
        <SectionTitle>{t("Track record (published picks)")}</SectionTitle>
        {!rec?.live.length ? <p className="text-sm text-muted">{t("Published picks are graded after each game. The record starts building with the next slate.")}</p> : (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {rec.live.map((l) => (
              <StatTile key={l.market} label={l.market === "winners" ? t("Winners") : t("Props")} value={signedPct(l.roi)}
                sub={t("{n} bets · {u} units · beat close {p}", { n: l.n, u: `${l.units >= 0 ? "+" : ""}${l.units.toFixed(1)}`, p: pct(l.beat_close_rate, 0) })} />
            ))}
          </div>
        )}
        {rec && rec.recent.length > 0 && (
          <Card className="overflow-x-auto">
            <table className="tabular w-full whitespace-nowrap text-sm">
              <thead><tr className="text-[11px] uppercase tracking-wide text-muted">{["Date", "Bet", "Price", "EV", "Result", "Beat close"].map((h) => <th key={h} className="px-3 py-2 text-left font-semibold first:pl-5">{keyed(t, `col:${h}`, h)}</th>)}</tr></thead>
              <tbody>{rec.recent.map((r, i) => (
                <tr key={i} className="border-t border-line">
                  <td className="py-2 pl-5 pr-3 text-muted">{shortDate(r.game_date_et, lang)}</td>
                  <td className="px-3"><Link href={`/nba/games/${r.game_id}`} className="hover:underline">{betLabel(r, t)}</Link></td>
                  <td className="px-3">{r.price_american} <span className="text-[11px] text-muted">{r.book}</span></td>
                  <td className="px-3">{signedPct(r.ev)}</td>
                  <td className="px-3"><Pill tone={r.result === "win" ? "win" : r.result === "loss" ? "loss" : "neutral"}>{keyed(t, `bet:${r.result}`, r.result)}</Pill></td>
                  <td className="px-3">{r.beat_close === null ? "—" : r.beat_close ? "✓" : "✗"}</td>
                </tr>))}</tbody>
            </table>
          </Card>
        )}
      </section>

      {rec && rec.backtests.length > 0 && (
        <section className="flex flex-col gap-3">
          <SectionTitle>{t("Backtests")}</SectionTitle>
          <p className="text-sm text-muted">{t("Re-run on past games using only what was known before each game, priced at the opening odds, then settled. Small samples are noisy: over a few hundred bets a ±5% return can be pure luck, so “beat the close” is the steadier signal (50% = no edge).")}</p>
          <div className="grid gap-4 lg:grid-cols-2">
            {rec.backtests.map((b) => (
              <Card key={`${b.market}${b.season}`} className="overflow-x-auto p-5">
                <h3 className="text-sm font-semibold">{b.market === "winners" ? t("Game winners") : t("Player props")} · {seasonLabel(b.season)}</h3>
                <p className="mb-3 text-[11px] text-muted">{t("{forecast}; priced at the {priced}.", { forecast: t(b.report.forecast), priced: t(b.report.priced_at) })}</p>
                <table className="tabular w-full text-sm">
                  <thead><tr className="text-[11px] uppercase tracking-wide text-muted"><th className="py-1.5 text-left font-semibold">{t("Min. EV")}</th>
                    <th className="text-right font-semibold">{t("Bets")}</th><th className="text-right font-semibold">{t("Won")}</th><th className="text-right font-semibold">{t("Return")}</th>
                    <th className="text-right font-semibold">{t("Beat close")}</th></tr></thead>
                  <tbody>{b.report.buckets.filter((x) => x.min_ev > 0).map((x) => (
                    <tr key={x.min_ev} className="border-t border-line">
                      <td className="py-1.5">+{(x.min_ev * 100).toFixed(0)}%</td><td className="text-right">{x.bets}</td>
                      <td className="text-right">{pct(x.win_rate, 0)}</td>
                      <td className={`text-right font-semibold ${(x.roi_flat ?? 0) > 0 ? "text-win" : (x.roi_flat ?? 0) < 0 ? "text-loss" : ""}`}>{signedPct(x.roi_flat)}</td>
                      <td className="text-right">{pct(x.beat_close_rate, 0)}</td>
                    </tr>))}</tbody>
                </table>
              </Card>
            ))}
          </div>
        </section>
      )}
    </>
  );
}

async function PickTable({ title, picks }: { title: string; picks: Pick[] }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  return (
    <Card className="overflow-x-auto">
      <div className="px-5 pt-4 text-sm font-semibold">{title}</div>
      <table className="tabular w-full whitespace-nowrap text-sm">
        <thead><tr className="text-[11px] uppercase tracking-wide text-muted">
          {["Game", "Bet", "Our chance", "Best price", "EV", "Stake*"].map((h) => <th key={h} className="px-3 py-2 text-left font-semibold first:pl-5">{keyed(t, `col:${h}`, h)}</th>)}
        </tr></thead>
        <tbody>{picks.map((p) => (
          <tr key={p.id} className="border-t border-line">
            <td className="py-2.5 pl-5 pr-3">
              <Link href={`/nba/games/${p.game_id}`} className="flex items-center gap-1.5 hover:underline">
                <TeamBadge abbr={p.away} size={20} /><span>{p.away} @ {p.home}</span><TeamBadge abbr={p.home} size={20} />
              </Link>
              <div className="text-[11px] text-muted">{dateTimeET(p.tipoff_utc, lang)}</div>
            </td>
            <td className="px-3 font-medium">{betLabel(p, t)}{p.player_team && <span className="ml-1 text-[11px] text-muted">{p.player_team}</span>}</td>
            <td className="px-3">{pct(p.our_p)}</td>
            <td className="px-3">{p.price_american} <span className="text-[11px] text-muted">{p.book}</span></td>
            <td className="px-3 font-semibold text-win">{signedPct(p.ev)}</td>
            <td className="px-3">{pct(p.kelly)}</td>
          </tr>))}</tbody>
      </table>
      <p className="px-5 py-3 text-[11px] text-muted">{t("*Suggested stake as a share of bankroll (quarter-Kelly). Prices move; check the book before betting. Props use a blend of our model (30%) and the market (70%), which tested better than either alone.")}</p>
    </Card>
  );
}

import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { RelativeTime } from "@/components/client";
import { DistributionChart } from "@/components/game/DistributionChart";
import { LineCalculator } from "@/components/game/LineCalculator";
import { PropsBoard } from "@/components/game/PropsBoard";
import { Breadcrumbs, Card, EmptyState, FormChips, Pill, SectionTitle, Skeleton, StatusPill, TeamBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTimeET, longDate, pct, seasonTypeLabel, signed } from "@/lib/format";
import type { Factor, TeamAbsences, GameDetail, GameLineMarket, GamePredictions, GameSide, Outcome, SideContext } from "@/lib/types";
import { keyed, type T } from "@/lib/i18n";
import { rich } from "@/lib/i18n/rich";
import { ReasonList } from "@/components/Reasons";
import { getLang, getT } from "@/lib/i18n/server";

type Params = Promise<{ id: string }>;

export default function GamePage({ params }: { params: Params }) {
  return (
    <Suspense fallback={<GameSkeleton />}>
      <GameContent params={params} />
    </Suspense>
  );
}

async function GameContent({ params }: { params: Params }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const { id } = await params;
  if (!/^\d+$/.test(id)) notFound();
  const [detailRes, predRes] = await Promise.all([
    api<GameDetail>(`/games/${id}`),
    api<GamePredictions>(`/games/${id}/predictions`),
  ]);
  if (!detailRes) notFound();
  const g = detailRes.data;
  const p = predRes?.data;
  const m = p?.markets ?? null;
  const final = g.status === "final";
  const actualMargin = final && g.home.score !== null && g.away.score !== null ? g.home.score - g.away.score : null;
  const actualTotal = final && g.home.score !== null && g.away.score !== null ? g.home.score + g.away.score : null;
  const title = `${g.away.name} vs ${g.home.name}`;

  return (
    <div>
      <Breadcrumbs items={[{ href: "/nba", label: "NBA" }, { label: seasonTypeLabel(g.season_type) },
        { href: `/nba?date=${g.game_date}`, label: longDate(g.game_date, lang) }]} />

      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-3 sm:gap-4">
          <div className="flex shrink-0 gap-1 sm:hidden">
            <TeamBadge abbr={g.away.abbreviation} size={36} />
            <TeamBadge abbr={g.home.abbreviation} size={36} />
          </div>
          <div className="hidden shrink-0 gap-1.5 sm:flex">
            <TeamBadge abbr={g.away.abbreviation} size={52} />
            <TeamBadge abbr={g.home.abbreviation} size={52} />
          </div>
          <div>
            <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">{title}</h1>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted">
              <StatusPill status={g.status} />
              <span>{dateTimeET(g.tipoff_utc, lang)}</span>
              {g.arena.name && <span>· {g.arena.name}{g.arena.city ? `, ${g.arena.city}` : ""}</span>}
              {g.is_cup && <Pill tone="accent">{t("NBA Cup")}{g.sub_label ? ` · ${t(g.sub_label)}` : ""}</Pill>}
            </div>
          </div>
        </div>
        {p?.published_at && (
          <div className="text-xs text-muted sm:text-right">
            <ForecastStatus status={p.forecast_status} />
            <div className="mt-1">{t("Forecast")}{" "}<RelativeTime iso={p.published_at} prefix={`${t("published")} `} /> · {t("rev")} {p.revision}</div>
          </div>
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex min-w-0 flex-col gap-6">
          {!m?.winner ? (
            <EmptyState title={p?.forecast_status === "pending" ? t("Forecast not published yet") : t("No forecast for this game")}>
              {p?.forecast_status === "pending"
                ? t("Forecasts are published ahead of tipoff on a daily schedule. Check back closer to game time.")
                : t("This game was not forecast (for example, it had already tipped off when forecasts ran).")}
            </EmptyState>
          ) : (
            <>
              <Card className="p-5">
                <HeadlineOdds g={g} p={p!} />
                <div className="mt-5">
                  <DistributionChart margin={m.winner.distribution} total={m.total?.distribution}
                    homeAbbr={g.home.abbreviation} awayAbbr={g.away.abbreviation} actualMargin={actualMargin} actualTotal={actualTotal} />
                </div>
              </Card>

              <Card className="overflow-hidden">
                <div className="grid grid-cols-[1fr_auto_auto] items-center gap-x-6 border-b border-line px-5 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
                  <span>{t("Outcome")}</span><span className="text-right">{t("Proj. pts")}</span><span className="w-20 text-right">{t("% chance")}</span>
                </div>
                {([["away", g.away], ["home", g.home]] as const).map(([side, s]) => (
                  <OutcomeRow key={side} side={s} prob={m.winner![side].probability} proj={m.projected_score?.[side]}
                    outcome={m.winner![side].outcome} final={final} t={t} />
                ))}
                {m.polymarket && (
                  <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t border-line bg-surface-2/60 px-5 py-2.5 text-xs text-muted" title={t(m.polymarket.note ?? "")}>
                    <a href={m.polymarket.url} target="_blank" rel="noopener noreferrer" className="hover:text-ink hover:underline">
                      Polymarket · {m.polymarket.price_type === "live" ? t("live price") : t("last price before tip-off")} ↗</a>
                    <span className="tabular">
                      {g.away.abbreviation} <b className="text-ink">{Math.round((1 - m.polymarket.p_home) * 100)}%</b>
                      {" · "}{g.home.abbreviation} <b className="text-ink">{Math.round(m.polymarket.p_home * 100)}%</b>
                      {m.polymarket.volume_usd ? <> · {t("${v} traded", { v: compactUsd(m.polymarket.volume_usd) })}</> : null}
                    </span>
                  </div>
                )}
                {m.sportsbook && (
                  <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t border-line bg-surface-2/60 px-5 py-2.5 text-xs text-muted" title={t(m.sportsbook.note ?? "")}>
                    <span>{m.sportsbook.is_closing ? t("Closing line") : t("Current line")} · {m.sportsbook.provider}</span>
                    <span className="tabular">
                      {g.away.abbreviation} <b className="text-ink">{Math.round((1 - m.sportsbook.p_home) * 100)}%</b>
                      {" · "}{g.home.abbreviation} <b className="text-ink">{Math.round(m.sportsbook.p_home * 100)}%</b>
                      {m.sportsbook.spread_home !== null && <> · {g.home.abbreviation} {m.sportsbook.spread_home > 0 ? "+" : ""}{m.sportsbook.spread_home}</>}
                      {m.sportsbook.total !== null && <> · {t("O/U")} {m.sportsbook.total}</>}
                    </span>
                  </div>
                )}
              </Card>

              <div className="grid gap-4 md:grid-cols-2">
                <LinesCard kind="spread" title={t("Spread")} market={m.spread} unit={t("{team} margin", { team: g.home.abbreviation })} homeAbbr={g.home.abbreviation} awayAbbr={g.away.abbreviation} />
                <LinesCard kind="total" title={t("Total points")} market={m.total} unit={t("combined points")} homeAbbr={g.home.abbreviation} awayAbbr={g.away.abbreviation} />
              </div>

              {m.winner.absences && (m.winner.absences.home?.players.length || m.winner.absences.away?.players.length) ? (
                <Card className="p-5">
                  <SectionTitle>{t("Injuries and absences")}</SectionTitle>
                  <Absences data={m.winner.absences} sides={[["away", g.away], ["home", g.home]]} />
                </Card>
              ) : null}

              {m.winner.factors && m.winner.factors.length > 0 && (
                <Card className="p-5">
                  <SectionTitle>{t("Why the model leans this way")}</SectionTitle>
                  <Factors factors={m.winner.factors} home={g.home.abbreviation} away={g.away.abbreviation} />
                </Card>
              )}

              <section>
                <SectionTitle>{t("Player props")}</SectionTitle>
                <PropsBoard players={p!.players} excluded={p!.excluded_players ?? []}
                  teams={[{ team_id: g.away.team_id, abbreviation: g.away.abbreviation }, { team_id: g.home.team_id, abbreviation: g.home.abbreviation }]} />
              </section>
            </>
          )}

          {g.line_score && <LineScore g={g} />}

          <Card className="p-5">
            <SectionTitle>{t("Team comparison")}</SectionTitle>
            <Comparison g={g} />
          </Card>

          {g.context.head_to_head.length > 0 && (
            <Card className="p-5">
              <SectionTitle>{t("Head to head · recent seasons")}</SectionTitle>
              <ul className="divide-y divide-line text-sm">
                {g.context.head_to_head.map((h) => {
                  const homeAbbr = h.home_team_id === g.home.team_id ? g.home.abbreviation : g.away.abbreviation;
                  const awayAbbr = h.away_team_id === g.home.team_id ? g.home.abbreviation : g.away.abbreviation;
                  const winner = h.winner_team_id === g.home.team_id ? g.home.abbreviation : g.away.abbreviation;
                  return (
                    <li key={h.game_id} className="flex items-center justify-between py-2">
                      <Link href={`/nba/games/${h.game_id}`} className="hover:underline">{longDate(h.date, lang)}</Link>
                      <span className="tabular">{awayAbbr} {h.away_score} – {h.home_score} {homeAbbr}</span>
                      <Pill>{t("{team} won", { team: winner })}</Pill>
                    </li>
                  );
                })}
              </ul>
            </Card>
          )}

          <Rules />
        </div>

        <aside className="flex flex-col gap-4 lg:sticky lg:top-32 lg:self-start">
          {g.status === "scheduled" && g.prediction && (
            <ReasonList reasons={g.reasons} pick={g.prediction.favorite} teams={{ home: g.home.name, away: g.away.name }}
              pickLabel={g.prediction.favorite === "home" ? g.home.name : g.away.name} t={t} lang={lang} />
          )}
          {m?.winner && g.status === "scheduled" ? (
            <LineCalculator gameId={g.game_id} homeAbbr={g.home.abbreviation} awayAbbr={g.away.abbreviation}
              spreadMean={m.spread?.projection} totalMean={m.total?.projection}
              linesAvailable={!!m.spread?.probabilities_available} players={p!.players} />
          ) : m?.winner ? (
            <FinalSummary g={g} p={p!} />
          ) : null}
          {p?.published_at && <AboutForecast p={p} />}
        </aside>
      </div>
    </div>
  );
}

async function ForecastStatus({ status }: { status: GamePredictions["forecast_status"] }) {
  const t = await getT();
  const map = { current: ["Current", "win"], stale: ["Stale: newer data available", "warn"], pending: ["Pending", "neutral"],
    unavailable: ["Unavailable", "neutral"], backtest: ["Backtest (not published live)", "warn"] } as const;
  const [label, tone] = map[status];
  return <Pill tone={tone}>{t(label)}</Pill>;
}

async function HeadlineOdds({ g, p }: { g: GameDetail; p: GamePredictions }) {
  const t = await getT();
  const w = p.markets!.winner!;
  const favHome = w.home.probability >= 0.5;
  const fav = favHome ? g.home : g.away;
  const favPct = favHome ? w.home_pct : w.away_pct;
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <div className="flex items-baseline gap-2">
          <span className="tabular text-4xl font-bold tracking-tight">{favPct.toFixed(0)}%</span>
          <span className="text-lg font-semibold">{fav.name}</span>
        </div>
        <p className="text-sm text-muted">{t("chance to win · full game including overtime")}</p>
      </div>
      <div className="grid w-full grid-cols-2 gap-2 text-sm sm:flex sm:w-auto">
        {([["away", g.away], ["home", g.home]] as const).map(([side, s]) => (
          <div key={side} className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 rounded-xl bg-surface-2 px-3 py-2">
            <TeamBadge abbr={s.abbreviation} size={22} />
            <span className="tabular font-bold">{(side === "home" ? w.home_pct : w.away_pct).toFixed(1)}%</span>
            <span className="text-xs text-muted">{(side === "home") === favHome ? t("favorite") : t("underdog")}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function OutcomeRow({ side, prob, proj, outcome, final, t }: { side: GameSide; prob: number; proj?: number; outcome: Outcome | null; final: boolean; t: T }) {
  return (
    <div className="grid grid-cols-[1fr_auto_auto] items-center gap-x-6 border-b border-line px-5 py-3.5 last:border-0">
      <Link href={`/nba/teams/${side.abbreviation}`} className="flex items-center gap-3 hover:opacity-80">
        <TeamBadge abbr={side.abbreviation} size={36} />
        <div>
          <div className="font-semibold">{side.full_name}</div>
          <div className="text-xs text-muted">{side.record ? t("{record} entering game", { record: side.record }) : ""}
            {final && side.score !== null && <span className="ml-1 font-semibold text-ink">· {t("Final")} {side.score}</span>}
          </div>
        </div>
      </Link>
      <span className="tabular text-right text-sm text-muted">{proj !== undefined ? proj.toFixed(1) : "—"}</span>
      <div className="flex w-20 items-center justify-end gap-2">
        {outcome?.state === "final" && <span aria-label={outcome.result === "hit" ? t("won") : t("lost")}>{outcome.result === "hit" ? "✓" : ""}</span>}
        <span className="tabular text-xl font-bold">{Math.round(prob * 100)}%</span>
      </div>
    </div>
  );
}

async function LinesCard({ kind, title, market, unit, homeAbbr, awayAbbr }: { kind: "spread" | "total"; title: string; market?: GameLineMarket; unit: string; homeAbbr: string; awayAbbr: string }) {
  const t = await getT();
  if (!market) return null;
  const isSpread = kind === "spread";
  const proj = market.projection;
  return (
    <Card className="p-5">
      <div className="mb-1 flex items-baseline justify-between">
        <h3 className="font-semibold">{title}</h3>
        <span className="text-xs text-muted">{unit}</span>
      </div>
      <div className="tabular mb-3 text-sm text-muted">
        {t("Projection:")}{" "}<b className="text-ink">{isSpread ? t("{team} by {n}", { team: proj >= 0 ? homeAbbr : awayAbbr, n: Math.abs(proj).toFixed(1) }) : proj.toFixed(1)}</b>
        {market.projection_outcome?.state === "final" && market.projection_outcome.observed !== null &&
          <> · {t("actual")} {isSpread ? signed(market.projection_outcome.observed, 0) : market.projection_outcome.observed}</>}
      </div>
      {market.probabilities_available ? (
        <ul className="flex flex-col gap-1.5">
          {market.lines.map((l) => (
            <li key={l.line} className="flex items-center gap-3 rounded-xl bg-surface-2 px-3 py-2 text-sm">
              <span className="flex-1">{teamLabel(t(l.label), homeAbbr, awayAbbr)}</span>
              {l.outcome?.state === "final" && <Pill tone={l.outcome.result === "hit" ? "win" : l.outcome.result === "push" ? "neutral" : "loss"}>{keyed(t, `result:${l.outcome.result}`, l.outcome.result ?? "")}</Pill>}
              <span className="tabular w-12 text-right font-bold">{pct(l.probability)}</span>
              <span className="tabular w-16 text-right text-xs text-muted" title={t("Complementary outcome")}>{t("other")} {pct(l.complement_probability)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted">{t("Line probabilities aren’t published for this game: the model’s distribution didn’t pass calibration checks.")}</p>
      )}
    </Card>
  );
}

function compactUsd(v: number): string {
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v);
}

function teamLabel(label: string, home: string, away: string) {
  return label.replace(/^(home|el local)\b/, home).replace(/^(away|el visitante)\b/, away);
}

const STATUS_TONE: Record<string, string> = { out: "bg-loss-soft text-loss", doubtful: "bg-loss-soft text-loss",
  questionable: "bg-warn-soft text-warn", "day-to-day": "bg-warn-soft text-warn" };

async function Absences({ data, sides }: { data: Partial<Record<"home" | "away", TeamAbsences>>;
  sides: [ "away" | "home", GameSide ][] }) {
  const tr = await getT();
  return (
    <div className="grid gap-5 sm:grid-cols-2">
      {sides.map(([side, team]) => {
        const t = data[side];   
        return (
          <div key={side}>
            <div className="mb-2 flex items-center gap-2">
              <TeamBadge abbr={team.abbreviation} size={22} />
              <span className="text-sm font-semibold">{team.name}</span>
              {t && t.minutes_missing_pct > 0 && <span className="ml-auto text-[11px] text-muted">{tr("{p}% of usual minutes", { p: t.minutes_missing_pct.toFixed(0) })}</span>}
            </div>
            {!t || t.players.length === 0 ? <p className="text-sm text-muted">{tr("No rotation players listed.")}</p> : (
              <ul className="flex flex-col divide-y divide-line">
                {t.players.map((p) => (
                  <li key={p.player_id} className="flex items-center gap-2 py-2 text-sm">
                    <Link href={`/nba/players/${p.player_id}`} className="min-w-0 flex-1 truncate font-medium hover:underline">{p.name ?? tr("Player {id}", { id: p.player_id })}</Link>
                    <span className="tabular text-[11px] text-muted">{p.usual_minutes.toFixed(0)} min</span>
                    <span title={[p.detail, p.source].filter(Boolean).map((x) => tr(x!)).join(" · ")}
                      className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${STATUS_TONE[p.status.toLowerCase()] ?? "bg-surface-2 text-muted"}`}>{tr(p.status)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
      <p className="text-xs text-muted sm:col-span-2">{tr("From ESPN’s injury report, or inferred when a player was inactive in his team’s last 2 games. “Usual minutes” is his recent average; the forecast redistributes those minutes to teammates.")}</p>
    </div>
  );
}

async function Factors({ factors, home, away }: { factors: Factor[]; home: string; away: string }) {
  const t = await getT();
  const max = Math.max(...factors.map((f) => Math.abs(f.home_margin_points)), 1);
  return (
    <div>
      <div className="mb-2 flex justify-between text-[11px] font-semibold uppercase tracking-wide text-muted">
        <span>{t("Favors {team}", { team: away })}</span><span>{t("Favors {team}", { team: home })}</span>
      </div>
      <ul className="flex flex-col gap-2.5">
        {factors.map((f) => {
          const w = (Math.abs(f.home_margin_points) / max) * 50;
          return (
            <li key={f.factor}>
              <div className="mb-1 flex justify-between text-sm">
                <span>{t(f.factor)}</span>
                <span className="tabular text-muted">{f.favors === "home" ? home : away} +{Math.abs(f.home_margin_points).toFixed(1)} {t("pts")}</span>
              </div>
              <div className="relative h-2 rounded-full bg-surface-2">
                <div className="absolute inset-y-0 left-1/2 w-px bg-line" />
                <div className="absolute inset-y-0 rounded-full bg-accent" style={f.favors === "home"
                  ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%`, opacity: 0.55 }} />
              </div>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-xs text-muted">{t("Estimated contribution of each input to the projected margin. These are statistical associations, not causes.")}</p>
    </div>
  );
}

async function Comparison({ g }: { g: GameDetail }) {
  const t = await getT();
  const rows: { label: string; get: (c: SideContext) => React.ReactNode }[] = [
    { label: "Last 10", get: (c) => `${c.last10.wins}-${c.last10.losses}` },
    { label: "Avg margin (L10)", get: (c) => (c.last10.avg_margin !== null ? signed(c.last10.avg_margin) : "—") },
    { label: "Avg points (L10)", get: (c) => c.last10.avg_points ?? "—" },
    { label: "Home record", get: (c) => c.home_away_record.home },
    { label: "Road record", get: (c) => c.home_away_record.away },
    { label: "Rest", get: (c) => (c.rest_days === null ? "—" : c.rest_days === 1 ? t("Back-to-back") : t("{n} days", { n: c.rest_days })) },
  ];
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-[11px] uppercase tracking-wide text-muted">
            <th className="py-2 text-left font-semibold"><span className="flex items-center gap-2"><TeamBadge abbr={g.away.abbreviation} size={20} />{g.away.abbreviation}</span></th>
            <th />
            <th className="py-2 text-right font-semibold"><span className="flex items-center justify-end gap-2">{g.home.abbreviation}<TeamBadge abbr={g.home.abbreviation} size={20} /></span></th>
          </tr>
        </thead>
        <tbody className="tabular">
          {rows.map((r) => (
            <tr key={r.label} className="border-t border-line">
              <td className="py-2 font-semibold">{r.get(g.context.away)}</td>
              <td className="py-2 text-center text-xs text-muted">{t(r.label)}</td>
              <td className="py-2 text-right font-semibold">{r.get(g.context.home)}</td>
            </tr>
          ))}
          <tr className="border-t border-line">
            <td className="py-2"><FormChips results={g.context.away.last5.results} /></td>
            <td className="py-2 text-center text-xs text-muted">{t("Last 5")}</td>
            <td className="py-2"><div className="flex justify-end"><FormChips results={g.context.home.last5.results} /></div></td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

async function LineScore({ g }: { g: GameDetail }) {
  const t = await getT();
  const rows = g.line_score!;
  const hasOt = rows.some((r) => (r.ot_points ?? 0) > 0);
  const name = (tid: number) => (tid === g.home.team_id ? g.home : g.away);
  return (
    <Card className="overflow-x-auto p-5">
      <SectionTitle>{t("Final score")}</SectionTitle>
      <table className="tabular w-full text-sm">
        <thead><tr className="text-xs text-muted"><th className="text-left font-medium">{t("Team")}</th>{["Q1", "Q2", "Q3", "Q4", ...(hasOt ? ["OT"] : []), "T"].map((h) => <th key={h} className="w-12 text-right font-medium">{keyed(t, `col:${h}`, h)}</th>)}</tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.team_id} className="border-t border-line">
              <td className="py-2"><span className="flex items-center gap-2"><TeamBadge abbr={name(r.team_id).abbreviation} size={20} />{name(r.team_id).name}</span></td>
              {[r.q1_points, r.q2_points, r.q3_points, r.q4_points, ...(hasOt ? [r.ot_points] : [])].map((v, i) => <td key={i} className="text-right">{v ?? "—"}</td>)}
              <td className="text-right font-bold">{r.points}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

async function FinalSummary({ g, p }: { g: GameDetail; p: GamePredictions }) {
  const t = await getT();
  const w = p.markets!.winner!;
  const favHome = w.home.probability >= 0.5;
  const correct = g.winner_team_id !== null && (favHome ? g.home.team_id : g.away.team_id) === g.winner_team_id;
  const ps = p.markets!.projected_score;
  return (
    <Card className="p-4">
      <h2 className="mb-3 text-sm font-semibold">{t("How the forecast did")}</h2>
      {g.status === "final" ? (
        <div className={`rounded-xl p-4 ${correct ? "bg-win-soft" : "bg-loss-soft"}`}>
          <div className={`text-sm font-semibold ${correct ? "text-win" : "text-loss"}`}>{correct ? `✓ ${t("Favorite won")}` : `✗ ${t("Underdog won")}`}</div>
          <div className="mt-1 text-xs text-muted">{t("Model gave {team} {p}%", { team: favHome ? g.home.abbreviation : g.away.abbreviation, p: Math.round(Math.max(w.home.probability, w.away.probability) * 100) })}</div>
        </div>
      ) : <p className="text-sm text-muted">{t("Waiting for the final result.")}</p>}
      {ps && (
        <div className="tabular mt-3 grid grid-cols-2 gap-2 text-sm">
          <div className="rounded-lg bg-surface-2 p-2"><div className="text-[11px] text-muted">{t("Projected")}</div>{g.away.abbreviation} {ps.away.toFixed(0)} – {ps.home.toFixed(0)} {g.home.abbreviation}</div>
          <div className="rounded-lg bg-surface-2 p-2"><div className="text-[11px] text-muted">{t("Actual")}</div>{g.away.abbreviation} {g.away.score ?? "—"} – {g.home.score ?? "—"} {g.home.abbreviation}</div>
        </div>
      )}
    </Card>
  );
}

async function AboutForecast({ p }: { p: GamePredictions }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  return (
    <Card className="p-4 text-xs">
      <h2 className="mb-2 text-sm font-semibold">{t("About this forecast")}</h2>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-muted">
        <dt>{t("Type")}</dt><dd className="text-ink">{p.kind === "live" ? t("Published before tipoff") : t("Backtest (regenerated after the fact)")}</dd>
        <dt>{t("Revision")}</dt><dd className="text-ink">{t("{a} of {b}", { a: p.revision ?? 1, b: p.revisions?.filter((r) => r.kind === p.kind).length ?? 1 })}</dd>
        <dt>{t("Data cutoff")}</dt><dd className="text-ink">{p.data_cutoff_utc ? dateTimeET(p.data_cutoff_utc, lang) : "—"}</dd>
        <dt>{t("Model")}</dt><dd className="truncate text-ink">{p.model_versions?.moneyline ?? "—"}</dd>
      </dl>
      {p.notes?.kind && <p className="mt-2 text-muted">{t(p.notes.kind)}</p>}
      {p.notes?.availability && <p className="mt-2 text-muted">{t(p.notes.availability)}</p>}
      <p className="mt-2 text-muted">{t("Estimates, not guarantees. PolyPicker does not offer wagering.")}</p>
    </Card>
  );
}

async function Rules() {
  const t = await getT();
  return (
    <Card className="p-5">
      <details>
        <summary className="cursor-pointer text-lg font-semibold tracking-tight">{t("How these forecasts resolve")}</summary>
        <ul className="mt-3 list-disc space-y-1.5 pl-5 text-sm text-muted">
          {["<b>Winner</b>: the full game including overtime. NBA games can’t end tied, so the two probabilities sum to 100%.",
            "<b>Spread</b>: a side “covers” if its final margin plus the handicap is above zero. With a whole-number handicap an exact landing is a <i>push</i>, reported separately, never as a loss.",
            "<b>Total</b>: combined final points, overtime included. Over 228.5 means 229 or more.",
            "<b>Player thresholds</b>: “20+” means at least 20. “Over 20.5” means 21 or more. “Over 20” pushes at exactly 20.",
            "<b>Did not play</b>: if a player doesn’t appear (DNP, inactive), their forecasts are <i>void</i>, not counted as misses.",
            "<b>Postponed or canceled</b> games stay pending or become void. Published forecasts are never edited afterwards.",
          ].map((line) => <li key={line}>{rich(t(line), { b: (c) => <b className="text-ink">{c}</b> })}</li>)}
        </ul>
      </details>
    </Card>
  );
}

function GameSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <Skeleton className="h-16 w-96 max-w-full" />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex flex-col gap-6"><Skeleton className="h-80" /><Skeleton className="h-40" /><Skeleton className="h-64" /></div>
        <Skeleton className="h-96" />
      </div>
    </div>
  );
}
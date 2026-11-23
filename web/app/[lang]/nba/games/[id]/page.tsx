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
import type { Factor, GameDetail, GameLineMarket, GamePredictions, GameSide, Outcome, SideContext } from "@/lib/types";

type Params = Promise<{ id: string }>;

export default function GamePage({ params }: { params: Params }) {
  return (
    <Suspense fallback={<GameSkeleton />}>
      <GameContent params={params} />
    </Suspense>
  );
}

async function GameContent({ params }: { params: Params }) {
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
      <Breadcrumbs items={[{ href: "/", label: "NBA" }, { label: seasonTypeLabel(g.season_type) },
        { href: `/?date=${g.game_date}`, label: longDate(g.game_date) }]} />

      {/* Event header */}
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-4">
          <div className="flex gap-1.5">
            <TeamBadge abbr={g.away.abbreviation} size={52} />
            <TeamBadge abbr={g.home.abbreviation} size={52} />
          </div>
          <div>
            <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">{title}</h1>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted">
              <StatusPill status={g.status} />
              <span>{dateTimeET(g.tipoff_utc)}</span>
              {g.arena.name && <span>· {g.arena.name}{g.arena.city ? `, ${g.arena.city}` : ""}</span>}
              {g.is_cup && <Pill tone="accent">NBA Cup{g.sub_label ? ` · ${g.sub_label}` : ""}</Pill>}
            </div>
          </div>
        </div>
        {p?.published_at && (
          <div className="text-right text-xs text-muted">
            <ForecastStatus status={p.forecast_status} />
            <div className="mt-1">Forecast <RelativeTime iso={p.published_at} prefix="published " /> · rev {p.revision}</div>
          </div>
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex min-w-0 flex-col gap-6">
          {!m?.winner ? (
            <EmptyState title={p?.forecast_status === "pending" ? "Forecast not published yet" : "No forecast for this game"}>
              {p?.forecast_status === "pending"
                ? "Forecasts are published ahead of tipoff on a daily schedule. Check back closer to game time."
                : "This game was not forecast (for example, it had already tipped off when forecasts ran)."}
            </EmptyState>
          ) : (
            <>
              {/* Headline probabilities + chart */}
              <Card className="p-5">
                <HeadlineOdds g={g} p={p!} />
                <div className="mt-5">
                  <DistributionChart margin={m.winner.distribution} total={m.total?.distribution}
                    homeAbbr={g.home.abbreviation} awayAbbr={g.away.abbreviation} actualMargin={actualMargin} actualTotal={actualTotal} />
                </div>
              </Card>

              {/* Outcome rows */}
              <Card className="overflow-hidden">
                <div className="grid grid-cols-[1fr_auto_auto] items-center gap-x-6 border-b border-line px-5 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
                  <span>Outcome</span><span className="text-right">Proj. pts</span><span className="w-20 text-right">% chance</span>
                </div>
                {([["away", g.away], ["home", g.home]] as const).map(([side, s]) => (
                  <OutcomeRow key={side} side={s} prob={m.winner![side].probability} proj={m.projected_score?.[side]}
                    outcome={m.winner![side].outcome} final={final} />
                ))}
              </Card>

              {/* Spread & total lines */}
              <div className="grid gap-4 md:grid-cols-2">
                <LinesCard title="Spread" market={m.spread} unit={`${g.home.abbreviation} margin`} homeAbbr={g.home.abbreviation} awayAbbr={g.away.abbreviation} />
                <LinesCard title="Total points" market={m.total} unit="combined points" homeAbbr={g.home.abbreviation} awayAbbr={g.away.abbreviation} />
              </div>

              {m.winner.factors && m.winner.factors.length > 0 && (
                <Card className="p-5">
                  <SectionTitle>Why the model leans this way</SectionTitle>
                  <Factors factors={m.winner.factors} home={g.home.abbreviation} away={g.away.abbreviation} />
                </Card>
              )}

              <section>
                <SectionTitle>Player props</SectionTitle>
                <PropsBoard players={p!.players} excluded={p!.excluded_players ?? []}
                  teams={[{ team_id: g.away.team_id, abbreviation: g.away.abbreviation }, { team_id: g.home.team_id, abbreviation: g.home.abbreviation }]} />
              </section>
            </>
          )}

          {g.line_score && <LineScore g={g} />}

          <Card className="p-5">
            <SectionTitle>Team comparison</SectionTitle>
            <Comparison g={g} />
          </Card>

          {g.context.head_to_head.length > 0 && (
            <Card className="p-5">
              <SectionTitle>Head to head · recent seasons</SectionTitle>
              <ul className="divide-y divide-line text-sm">
                {g.context.head_to_head.map((h) => {
                  const homeAbbr = h.home_team_id === g.home.team_id ? g.home.abbreviation : g.away.abbreviation;
                  const awayAbbr = h.away_team_id === g.home.team_id ? g.home.abbreviation : g.away.abbreviation;
                  const winner = h.winner_team_id === g.home.team_id ? g.home.abbreviation : g.away.abbreviation;
                  return (
                    <li key={h.game_id} className="flex items-center justify-between py-2">
                      <Link href={`/games/${h.game_id}`} className="hover:underline">{longDate(h.date)}</Link>
                      <span className="tabular">{awayAbbr} {h.away_score} – {h.home_score} {homeAbbr}</span>
                      <Pill>{winner} won</Pill>
                    </li>
                  );
                })}
              </ul>
            </Card>
          )}

          <Rules />
        </div>

        {/* Side panel */}
        <aside className="flex flex-col gap-4 lg:sticky lg:top-32 lg:self-start">
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

function ForecastStatus({ status }: { status: GamePredictions["forecast_status"] }) {
  const map = { current: ["Current", "win"], stale: ["Stale: newer data available", "warn"], pending: ["Pending", "neutral"],
    unavailable: ["Unavailable", "neutral"], backtest: ["Backtest (not published live)", "warn"] } as const;
  const [label, tone] = map[status];
  return <Pill tone={tone}>{label}</Pill>;
}

function HeadlineOdds({ g, p }: { g: GameDetail; p: GamePredictions }) {
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
        <p className="text-sm text-muted">chance to win · full game including overtime</p>
      </div>
      <div className="flex gap-2 text-sm">
        {([["away", g.away], ["home", g.home]] as const).map(([side, s]) => (
          <div key={side} className="flex items-center gap-2 rounded-xl bg-surface-2 px-3 py-2">
            <TeamBadge abbr={s.abbreviation} size={22} />
            <span className="tabular font-bold">{(side === "home" ? w.home_pct : w.away_pct).toFixed(1)}%</span>
            <span className="text-xs text-muted">{(side === "home") === favHome ? "favorite" : "underdog"}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function OutcomeRow({ side, prob, proj, outcome, final }: { side: GameSide; prob: number; proj?: number; outcome: Outcome | null; final: boolean }) {
  return (
    <div className="grid grid-cols-[1fr_auto_auto] items-center gap-x-6 border-b border-line px-5 py-3.5 last:border-0">
      <Link href={`/teams/${side.abbreviation}`} className="flex items-center gap-3 hover:opacity-80">
        <TeamBadge abbr={side.abbreviation} size={36} />
        <div>
          <div className="font-semibold">{side.full_name}</div>
          <div className="text-xs text-muted">{side.record ? `${side.record} entering game` : ""}
            {final && side.score !== null && <span className="ml-1 font-semibold text-ink">· Final {side.score}</span>}
          </div>
        </div>
      </Link>
      <span className="tabular text-right text-sm text-muted">{proj !== undefined ? proj.toFixed(1) : "—"}</span>
      <div className="flex w-20 items-center justify-end gap-2">
        {outcome?.state === "final" && <span aria-label={outcome.result === "hit" ? "won" : "lost"}>{outcome.result === "hit" ? "✓" : ""}</span>}
        <span className="tabular text-xl font-bold">{Math.round(prob * 100)}%</span>
      </div>
    </div>
  );
}

function LinesCard({ title, market, unit, homeAbbr, awayAbbr }: { title: string; market?: GameLineMarket; unit: string; homeAbbr: string; awayAbbr: string }) {
  if (!market) return null;
  const isSpread = title === "Spread";
  const proj = market.projection;
  return (
    <Card className="p-5">
      <div className="mb-1 flex items-baseline justify-between">
        <h3 className="font-semibold">{title}</h3>
        <span className="text-xs text-muted">{unit}</span>
      </div>
      <div className="tabular mb-3 text-sm text-muted">
        Projection: <b className="text-ink">{isSpread ? `${proj >= 0 ? homeAbbr : awayAbbr} by ${Math.abs(proj).toFixed(1)}` : proj.toFixed(1)}</b>
        {market.projection_outcome?.state === "final" && market.projection_outcome.observed !== null &&
          <> · actual {isSpread ? signed(market.projection_outcome.observed, 0) : market.projection_outcome.observed}</>}
      </div>
      {market.probabilities_available ? (
        <ul className="flex flex-col gap-1.5">
          {market.lines.map((l) => (
            <li key={l.line} className="flex items-center gap-3 rounded-xl bg-surface-2 px-3 py-2 text-sm">
              <span className="flex-1">{teamLabel(l.label, homeAbbr, awayAbbr)}</span>
              {l.outcome?.state === "final" && <Pill tone={l.outcome.result === "hit" ? "win" : l.outcome.result === "push" ? "neutral" : "loss"}>{l.outcome.result}</Pill>}
              <span className="tabular w-12 text-right font-bold">{pct(l.probability)}</span>
              <span className="tabular w-16 text-right text-xs text-muted" title="Complementary outcome">other {pct(l.complement_probability)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted">Line probabilities aren’t published for this game: the model’s distribution didn’t pass calibration checks.</p>
      )}
    </Card>
  );
}

function teamLabel(label: string, home: string, away: string) {
  return label.replace(/^home\b/, home).replace(/^away\b/, away);
}

function Factors({ factors, home, away }: { factors: Factor[]; home: string; away: string }) {
  const max = Math.max(...factors.map((f) => Math.abs(f.home_margin_points)), 1);
  return (
    <div>
      <div className="mb-2 flex justify-between text-[11px] font-semibold uppercase tracking-wide text-muted">
        <span>Favors {away}</span><span>Favors {home}</span>
      </div>
      <ul className="flex flex-col gap-2.5">
        {factors.map((f) => {
          const w = (Math.abs(f.home_margin_points) / max) * 50;
          return (
            <li key={f.factor}>
              <div className="mb-1 flex justify-between text-sm">
                <span>{f.factor}</span>
                <span className="tabular text-muted">{f.favors === "home" ? home : away} +{Math.abs(f.home_margin_points).toFixed(1)} pts</span>
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
      <p className="mt-3 text-xs text-muted">Estimated contribution of each input to the projected margin. These are statistical associations, not causes.</p>
    </div>
  );
}

function Comparison({ g }: { g: GameDetail }) {
  const rows: { label: string; get: (c: SideContext) => React.ReactNode }[] = [
    { label: "Last 10", get: (c) => `${c.last10.wins}-${c.last10.losses}` },
    { label: "Avg margin (L10)", get: (c) => (c.last10.avg_margin !== null ? signed(c.last10.avg_margin) : "—") },
    { label: "Avg points (L10)", get: (c) => c.last10.avg_points ?? "—" },
    { label: "Home record", get: (c) => c.home_away_record.home },
    { label: "Road record", get: (c) => c.home_away_record.away },
    { label: "Rest", get: (c) => (c.rest_days === null ? "—" : c.rest_days === 1 ? "Back-to-back" : `${c.rest_days} days`) },
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
              <td className="py-2 text-center text-xs text-muted">{r.label}</td>
              <td className="py-2 text-right font-semibold">{r.get(g.context.home)}</td>
            </tr>
          ))}
          <tr className="border-t border-line">
            <td className="py-2"><FormChips results={g.context.away.last5.results} /></td>
            <td className="py-2 text-center text-xs text-muted">Last 5</td>
            <td className="py-2"><div className="flex justify-end"><FormChips results={g.context.home.last5.results} /></div></td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function LineScore({ g }: { g: GameDetail }) {
  const rows = g.line_score!;
  const hasOt = rows.some((r) => (r.ot_points ?? 0) > 0);
  const name = (tid: number) => (tid === g.home.team_id ? g.home : g.away);
  return (
    <Card className="overflow-x-auto p-5">
      <SectionTitle>Final score</SectionTitle>
      <table className="tabular w-full text-sm">
        <thead><tr className="text-xs text-muted"><th className="text-left font-medium">Team</th>{["Q1", "Q2", "Q3", "Q4", ...(hasOt ? ["OT"] : []), "T"].map((h) => <th key={h} className="w-12 text-right font-medium">{h}</th>)}</tr></thead>
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

function FinalSummary({ g, p }: { g: GameDetail; p: GamePredictions }) {
  const w = p.markets!.winner!;
  const favHome = w.home.probability >= 0.5;
  const correct = g.winner_team_id !== null && (favHome ? g.home.team_id : g.away.team_id) === g.winner_team_id;
  const ps = p.markets!.projected_score;
  return (
    <Card className="p-4">
      <h2 className="mb-3 text-sm font-semibold">How the forecast did</h2>
      {g.status === "final" ? (
        <div className={`rounded-xl p-4 ${correct ? "bg-win-soft" : "bg-loss-soft"}`}>
          <div className={`text-sm font-semibold ${correct ? "text-win" : "text-loss"}`}>{correct ? "✓ Favorite won" : "✗ Underdog won"}</div>
          <div className="mt-1 text-xs text-muted">Model gave {favHome ? g.home.abbreviation : g.away.abbreviation} {Math.round(Math.max(w.home.probability, w.away.probability) * 100)}%</div>
        </div>
      ) : <p className="text-sm text-muted">Waiting for the final result.</p>}
      {ps && (
        <div className="tabular mt-3 grid grid-cols-2 gap-2 text-sm">
          <div className="rounded-lg bg-surface-2 p-2"><div className="text-[11px] text-muted">Projected</div>{g.away.abbreviation} {ps.away.toFixed(0)} – {ps.home.toFixed(0)} {g.home.abbreviation}</div>
          <div className="rounded-lg bg-surface-2 p-2"><div className="text-[11px] text-muted">Actual</div>{g.away.abbreviation} {g.away.score ?? "—"} – {g.home.score ?? "—"} {g.home.abbreviation}</div>
        </div>
      )}
    </Card>
  );
}

function AboutForecast({ p }: { p: GamePredictions }) {
  return (
    <Card className="p-4 text-xs">
      <h2 className="mb-2 text-sm font-semibold">About this forecast</h2>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-muted">
        <dt>Type</dt><dd className="text-ink">{p.kind === "live" ? "Published before tipoff" : "Backtest (regenerated after the fact)"}</dd>
        <dt>Revision</dt><dd className="text-ink">{p.revision} of {p.revisions?.filter((r) => r.kind === p.kind).length ?? 1}</dd>
        <dt>Data cutoff</dt><dd className="text-ink">{p.data_cutoff_utc ? dateTimeET(p.data_cutoff_utc) : "—"}</dd>
        <dt>Model</dt><dd className="truncate text-ink">{p.model_versions?.moneyline ?? "—"}</dd>
      </dl>
      {p.notes?.kind && <p className="mt-2 text-muted">{p.notes.kind}</p>}
      <p className="mt-2 text-muted">{p.notes?.availability}</p>
      <p className="mt-2 text-muted">Estimates, not guarantees. PolyPicker does not offer wagering.</p>
    </Card>
  );
}

function Rules() {
  return (
    <Card className="p-5">
      <details>
        <summary className="cursor-pointer text-lg font-semibold tracking-tight">How these forecasts resolve</summary>
        <ul className="mt-3 list-disc space-y-1.5 pl-5 text-sm text-muted">
          <li><b className="text-ink">Winner</b>: the full game including overtime. NBA games can’t end tied, so the two probabilities sum to 100%.</li>
          <li><b className="text-ink">Spread</b>: a side “covers” if its final margin plus the handicap is above zero. With a whole-number handicap an exact landing is a <i>push</i>, reported separately, never as a loss.</li>
          <li><b className="text-ink">Total</b>: combined final points, overtime included. Over 228.5 means 229 or more.</li>
          <li><b className="text-ink">Player thresholds</b>: “20+” means at least 20. “Over 20.5” means 21 or more. “Over 20” pushes at exactly 20.</li>
          <li><b className="text-ink">Did not play</b>: if a player doesn’t appear (DNP, inactive), their forecasts are <i>void</i>, not counted as misses.</li>
          <li><b className="text-ink">Postponed or canceled</b> games stay pending or become void. Published forecasts are never edited afterwards.</li>
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

import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { RelativeTime } from "@/components/client";
import { DistributionChart } from "@/components/game/DistributionChart";
import { NflLineExplorer } from "@/components/nfl/LineExplorer";
import { NflPropsBoard } from "@/components/nfl/PropsBoard";
import { Breadcrumbs, Card, EmptyState, FormChips, Pill, SectionTitle, Skeleton, StatusPill, TeamBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTimeET, longDate, pct, signed } from "@/lib/format";
import type { NflGameDetail, NflPredictions, NflTeamStats } from "@/lib/nfl";
import type { Factor, GameLineMarket, GameSide, Outcome } from "@/lib/types";
import { keyed } from "@/lib/i18n";
import { rich } from "@/lib/i18n/rich";
import { getLang, getT } from "@/lib/i18n/server";

type Params = Promise<{ id: string }>;

export default function NflGamePage({ params }: { params: Params }) {
  return (
    <Suspense fallback={<GameSkeleton />}>
      <GameContent params={params} />
    </Suspense>
  );
}

type BoxRow = { player_id: string; full_name: string | null; position: string | null; team_id: number; participation: string;
  status_comment: string | null; offense_snaps: number | null; offense_pct: number | null; completions: number | null;
  pass_attempts: number | null; passing_yards: number | null; passing_tds: number | null; interceptions: number | null;
  sacks: number | null; carries: number | null; rushing_yards: number | null; rushing_tds: number | null; targets: number | null;
  receptions: number | null; receiving_yards: number | null; receiving_tds: number | null; fumbles_lost: number | null };

async function GameContent({ params }: { params: Params }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const { id } = await params;
  if (!/^\d+$/.test(id)) notFound();
  const [detailRes, predRes, boxRes] = await Promise.all([
    api<NflGameDetail>(`/nfl/games/${id}`),
    api<NflPredictions>(`/nfl/games/${id}/predictions`),
    api<{ players: BoxRow[]; complete: boolean }>(`/nfl/games/${id}/boxscore`),
  ]);
  if (!detailRes) notFound();
  const g = detailRes.data;
  const p = predRes?.data;
  const m = p?.markets;
  const final = g.status === "final";
  const actualMargin = final && g.home.score !== null && g.away.score !== null ? g.home.score - g.away.score : null;
  const actualTotal = final && g.home.score !== null && g.away.score !== null ? g.home.score + g.away.score : null;
  const box = boxRes?.data.players ?? [];

  return (
    <div>
      <Breadcrumbs items={[{ href: "/nfl", label: "NFL" }, { href: `/nfl?season=${g.season}&week=${g.week}`, label: `${g.season} · ${t(g.label ?? "")}` },
        { label: longDate(g.game_date, lang) }]} />
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-3 sm:gap-4">
          <div className="flex shrink-0 gap-1 sm:gap-1.5">
            <TeamBadge abbr={g.away.abbreviation} size={44} league="nfl" />
            <TeamBadge abbr={g.home.abbreviation} size={44} league="nfl" />
          </div>
          <div>
            <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">{t("{away} at {home}", { away: g.away.name, home: g.home.name })}</h1>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted">
              <StatusPill status={g.status} />
              <span>{dateTimeET(g.tipoff_utc, lang)}</span>
              {g.venue.stadium && <span>· {g.venue.stadium}{g.venue.neutral_site ? ` (${t("neutral site")})` : ""}</span>}
              {g.div_game && <Pill tone="accent">{t("Division game")}</Pill>}
              {g.overtime && <Pill>{t("OT")}</Pill>}
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
                ? t("NFL forecasts are published for the next two weeks of games and updated as injury reports come in.")
                : t("This game was not forecast.")}
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
                {(["away", "home"] as const).map((side) => (
                  <OutcomeRow key={side} side={g[side]} prob={m.winner![side].probability} proj={m.projected_score?.[side]}
                    outcome={m.winner![side].outcome} final={final} />
                ))}
                {(m.winner.tie_probability ?? 0) > 0.001 && (
                  <p className="border-t border-line px-5 py-2 text-xs text-muted">{t("Chance of a tie: {p} (settles as a push).", { p: pct(m.winner.tie_probability, 1) })}</p>
                )}
              </Card>
              <div className="grid gap-4 md:grid-cols-2">
                <LinesCard kind="spread" title={t("Spread")} market={m.spread} unit={t("{team} margin", { team: g.home.abbreviation })} homeAbbr={g.home.abbreviation} awayAbbr={g.away.abbreviation} />
                <LinesCard kind="total" title={t("Total points")} market={m.total} unit={t("combined points")} homeAbbr={g.home.abbreviation} awayAbbr={g.away.abbreviation} />
              </div>
              {m.winner.factors && m.winner.factors.length > 0 && (
                <Card className="p-5">
                  <SectionTitle>{t("Why the model leans this way")}</SectionTitle>
                  <Factors factors={m.winner.factors} home={g.home.abbreviation} away={g.away.abbreviation} />
                </Card>
              )}
              <section>
                <SectionTitle>{t("Player props")}</SectionTitle>
                <NflPropsBoard players={p!.players} excluded={p!.excluded_players ?? []}
                  teams={[{ team_id: g.away.team_id, abbreviation: g.away.abbreviation }, { team_id: g.home.team_id, abbreviation: g.home.abbreviation }]} />
              </section>
            </>
          )}

          {g.line_score && <LineScore g={g} />}
          {g.team_stats.length === 2 && g.team_stats.every((s) => s.plays !== null) && <TeamBox g={g} />}
          {box.some((b) => b.participation === "played") && <PlayerBox g={g} rows={box} />}

          <Card className="p-5">
            <SectionTitle>{t("Recent form")}</SectionTitle>
            <Comparison g={g} />
          </Card>
          {g.context.head_to_head.length > 0 && (
            <Card className="p-5">
              <SectionTitle>{t("Head to head · recent meetings")}</SectionTitle>
              <ul className="divide-y divide-line text-sm">
                {g.context.head_to_head.map((h) => {
                  const ab = (tid: number) => (tid === g.home.team_id ? g.home.abbreviation : g.away.abbreviation);
                  return (
                    <li key={h.game_id} className="flex items-center justify-between py-2">
                      <Link href={`/nfl/games/${h.game_id}`} className="hover:underline">{longDate(h.date, lang)}</Link>
                      <span className="tabular">{ab(h.away_team_id)} {h.away_score} – {h.home_score} {ab(h.home_team_id)}</span>
                      <Pill>{h.winner_team_id === null ? t("Tie") : t("{team} won", { team: ab(h.winner_team_id) })}</Pill>
                    </li>
                  );
                })}
              </ul>
            </Card>
          )}
          <Rules />
        </div>

        <aside className="flex flex-col gap-4 lg:sticky lg:top-32 lg:self-start">
          {m?.winner && g.status === "scheduled" ? (
            <NflLineExplorer gameId={g.game_id} homeAbbr={g.home.abbreviation} awayAbbr={g.away.abbreviation}
              spreadMean={m.spread?.projection} totalMean={m.total?.projection}
              linesAvailable={!!m.spread?.probabilities_available} players={p!.players} />
          ) : m?.winner ? <FinalSummary g={g} p={p!} /> : null}
          {p?.published_at && <AboutForecast p={p} />}
        </aside>
      </div>
    </div>
  );
}

async function ForecastStatus({ status }: { status: NflPredictions["forecast_status"] }) {
  const t = await getT();
  const map = { current: ["Current", "win"], stale: ["Stale: newer data available", "warn"], pending: ["Pending", "neutral"],
    unavailable: ["Unavailable", "neutral"], backtest: ["Backtest (not published live)", "warn"] } as const;
  const [label, tone] = map[status];
  return <Pill tone={tone}>{t(label)}</Pill>;
}

async function HeadlineOdds({ g, p }: { g: NflGameDetail; p: NflPredictions }) {
  const t = await getT();
  const w = p.markets!.winner!;
  const favHome = w.home.probability >= 0.5;
  const fav = favHome ? g.home : g.away;
  const qb = w.qb_out;
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <div className="flex items-baseline gap-2">
          <span className="tabular text-4xl font-bold tracking-tight">{(favHome ? w.home_pct : w.away_pct).toFixed(0)}%</span>
          <span className="text-lg font-semibold">{fav.name}</span>
        </div>
        <p className="text-sm text-muted">{t("chance to win · a tie is a push")}</p>
        {qb && (qb.home || qb.away) && (
          <p className="mt-1 text-xs font-medium text-warn">
            {t("Starting QB listed Out/Doubtful:")} {[qb.away && g.away.abbreviation, qb.home && g.home.abbreviation].filter(Boolean).join(", ")}
          </p>
        )}
      </div>
      <div className="grid w-full grid-cols-2 gap-2 text-sm sm:flex sm:w-auto">
        {(["away", "home"] as const).map((side) => (
          <div key={side} className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 rounded-xl bg-surface-2 px-3 py-2">
            <TeamBadge abbr={g[side].abbreviation} size={22} league="nfl" />
            <span className="tabular font-bold">{(side === "home" ? w.home_pct : w.away_pct).toFixed(1)}%</span>
            <span className="text-xs text-muted">{(side === "home") === favHome ? t("favorite") : t("underdog")}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

async function OutcomeRow({ side, prob, proj, outcome, final }: { side: GameSide; prob: number; proj?: number; outcome: Outcome | null; final: boolean }) {
  const t = await getT();
  return (
    <div className="grid grid-cols-[1fr_auto_auto] items-center gap-x-6 border-b border-line px-5 py-3.5 last:border-0">
      <Link href={`/nfl/teams/${side.abbreviation}`} className="flex items-center gap-3 hover:opacity-80">
        <TeamBadge abbr={side.abbreviation} size={36} league="nfl" />
        <div>
          <div className="font-semibold">{side.full_name}</div>
          <div className="text-xs text-muted">{side.record ? t("{record} entering game", { record: side.record }) : ""}
            {final && side.score !== null && <span className="ml-1 font-semibold text-ink">· {t("Final")} {side.score}</span>}
          </div>
        </div>
      </Link>
      <span className="tabular text-right text-sm text-muted">{proj !== undefined ? proj.toFixed(1) : "—"}</span>
      <div className="flex w-20 items-center justify-end gap-2">
        {outcome?.state === "final" && outcome.result === "hit" && <span aria-label={t("won")}>✓</span>}
        <span className="tabular text-xl font-bold">{Math.round(prob * 100)}%</span>
      </div>
    </div>
  );
}

function teamLabel(label: string, home: string, away: string) {
  // English "home -4.5 covers" / Spanish "el local -4.5 cubre": put the team in place of the side
  return label.replace(/^(home|el local)\b/, home).replace(/^(away|el visitante)\b/, away);
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
        <p className="text-xs text-muted">{t("Line probabilities aren’t published: the model’s distribution didn’t pass calibration checks.")}</p>
      )}
    </Card>
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
              <div className="mb-1 flex justify-between gap-3 text-sm">
                <span>{t(f.factor)}</span>
                <span className="tabular shrink-0 text-muted">{f.favors === "home" ? home : away} +{Math.abs(f.home_margin_points).toFixed(1)} {t("pts")}</span>
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
      <p className="mt-3 text-xs text-muted">{t("Estimated contribution of each input to the projected margin. Statistical associations, not causes.")}</p>
    </div>
  );
}

async function LineScore({ g }: { g: NflGameDetail }) {
  const t = await getT();
  const rows = g.line_score!;
  const hasOt = rows.some((r) => (r.ot_points ?? 0) > 0) || !!g.overtime;
  const team = (tid: number) => (tid === g.home.team_id ? g.home : g.away);
  return (
    <Card className="overflow-x-auto p-5">
      <SectionTitle>{g.status === "final" ? t("Final score") : t("Score")}</SectionTitle>
      <table className="tabular w-full text-sm">
        <thead><tr className="text-xs text-muted"><th className="text-left font-medium">{t("Team")}</th>
          {["1", "2", "3", "4", ...(hasOt ? ["OT"] : []), "T"].map((h) => <th key={h} className="w-12 text-right font-medium">{keyed(t, `col:${h}`, h)}</th>)}</tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.team_id} className="border-t border-line">
              <td className="py-2"><span className="flex items-center gap-2"><TeamBadge abbr={team(r.team_id).abbreviation} size={20} league="nfl" />{team(r.team_id).name}</span></td>
              {[r.q1_points, r.q2_points, r.q3_points, r.q4_points, ...(hasOt ? [r.ot_points] : [])].map((v, i) => <td key={i} className="text-right">{v ?? "—"}</td>)}
              <td className="text-right font-bold">{r.points}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

async function TeamBox({ g }: { g: NflGameDetail }) {
  const t = await getT();
  const by = (tid: number) => g.team_stats.find((s) => s.team_id === tid)!;
  const a = by(g.away.team_id), h = by(g.home.team_id);
  const rows: [string, (s: NflTeamStats) => React.ReactNode][] = [
    ["Total yards", (s) => (s.passing_yards ?? 0) + (s.rushing_yards ?? 0)],
    ["Passing", (s) => `${s.completions}/${s.pass_attempts}, ${s.passing_yards} ${t("yds")}`],
    ["Rushing", (s) => `${s.carries} ${t("car")}, ${s.rushing_yards} ${t("yds")}`],
    ["First downs", (s) => s.first_downs ?? "—"],
    ["Turnovers", (s) => s.turnovers ?? "—"],
    ["Sacks taken", (s) => s.sacks ?? "—"],
    ["Penalties", (s) => `${s.penalties ?? 0}-${s.penalty_yards ?? 0}`],
    ["EPA per play", (s) => s.plays ? signed(((s.passing_epa ?? 0) + (s.rushing_epa ?? 0)) / s.plays, 2) : "—"],
  ];
  return (
    <Card className="overflow-x-auto p-5">
      <SectionTitle>{t("Team stats")}</SectionTitle>
      <table className="tabular w-full text-sm">
        <thead><tr className="text-[11px] uppercase tracking-wide text-muted">
          <th className="py-2 text-left font-semibold">{g.away.abbreviation}</th><th /><th className="py-2 text-right font-semibold">{g.home.abbreviation}</th></tr></thead>
        <tbody>
          {rows.map(([label, f]) => (
            <tr key={label} className="border-t border-line">
              <td className="py-2 font-semibold">{f(a)}</td>
              <td className="py-2 text-center text-xs text-muted">{t(label)}</td>
              <td className="py-2 text-right font-semibold">{f(h)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

async function PlayerBox({ g, rows }: { g: NflGameDetail; rows: BoxRow[] }) {
  const t = await getT();
  const played = rows.filter((r) => r.participation === "played");
  const sections: { title: string; filter: (r: BoxRow) => boolean; cols: [string, (r: BoxRow) => React.ReactNode][]; sort: (r: BoxRow) => number }[] = [
    { title: "Passing", filter: (r) => (r.pass_attempts ?? 0) > 0, sort: (r) => r.passing_yards ?? 0,
      cols: [["C/ATT", (r) => `${r.completions}/${r.pass_attempts}`], ["YDS", (r) => r.passing_yards], ["TD", (r) => r.passing_tds], ["INT", (r) => r.interceptions], ["SK", (r) => r.sacks]] },
    { title: "Rushing", filter: (r) => (r.carries ?? 0) > 0, sort: (r) => r.rushing_yards ?? 0,
      cols: [["CAR", (r) => r.carries], ["YDS", (r) => r.rushing_yards], ["AVG", (r) => r.carries ? ((r.rushing_yards ?? 0) / r.carries).toFixed(1) : "—"], ["TD", (r) => r.rushing_tds]] },
    { title: "Receiving", filter: (r) => (r.targets ?? 0) > 0, sort: (r) => r.receiving_yards ?? 0,
      cols: [["REC", (r) => r.receptions], ["TGT", (r) => r.targets], ["YDS", (r) => r.receiving_yards], ["TD", (r) => r.receiving_tds]] },
  ];
  const teams = [g.away, g.home];
  const inactive = rows.filter((r) => r.participation === "inactive");
  return (
    <Card className="p-5">
      <SectionTitle>{t("Box score")}</SectionTitle>
      <div className="flex flex-col gap-6">
        {sections.map((sec) => (
          <div key={sec.title} className="grid gap-4 lg:grid-cols-2">
            {teams.map((tm) => {
              const rs = played.filter((r) => r.team_id === tm.team_id && sec.filter(r)).sort((x, y) => sec.sort(y) - sec.sort(x));
              return (
                <div key={tm.team_id} className="overflow-x-auto">
                  <div className="mb-1 flex items-center gap-2 text-xs font-semibold text-muted">
                    <TeamBadge abbr={tm.abbreviation} size={18} league="nfl" />{tm.abbreviation} · {t(sec.title)}
                  </div>
                  <table className="tabular w-full text-sm">
                    <thead><tr className="text-[11px] text-muted"><th className="text-left font-medium">{t("Player")}</th>
                      {sec.cols.map(([h]) => <th key={h} className="w-12 text-right font-medium">{keyed(t, `col:${h}`, h)}</th>)}</tr></thead>
                    <tbody>
                      {rs.length === 0 ? <tr><td colSpan={sec.cols.length + 1} className="py-2 text-xs text-muted">—</td></tr> : rs.map((r) => (
                        <tr key={r.player_id} className="border-t border-line">
                          <td className="py-1.5"><Link href={`/nfl/players/${r.player_id}`} className="hover:underline">{r.full_name}</Link>
                            <span className="ml-1 text-[11px] text-muted">{r.position}</span></td>
                          {sec.cols.map(([h, f]) => <td key={h} className="text-right">{f(r) ?? "—"}</td>)}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              );
            })}
          </div>
        ))}
      </div>
      {inactive.length > 0 && (
        <p className="mt-4 text-xs text-muted"><b>{t("Inactive:")}</b> {teams.map((tm) => {
          const names = inactive.filter((r) => r.team_id === tm.team_id).map((r) => `${r.full_name} (${r.position})`);
          return names.length ? `${tm.abbreviation}: ${names.join(", ")}` : null;
        }).filter(Boolean).join(" · ")}</p>
      )}
    </Card>
  );
}

async function Comparison({ g }: { g: NflGameDetail }) {
  const t = await getT();
  const rows: { label: string; get: (c: NflGameDetail["context"]["home"]) => React.ReactNode }[] = [
    { label: "Last 5", get: (c) => `${c.last5.wins}-${c.last5.losses}${c.last5.ties ? `-${c.last5.ties}` : ""}` },
    { label: "Avg margin (L5)", get: (c) => (c.last5.avg_margin !== null ? signed(c.last5.avg_margin) : "—") },
    { label: "Avg points (L5)", get: (c) => c.last5.avg_points ?? "—" },
    { label: "Rest", get: (c) => (c.rest_days === null ? "—" : c.rest_days >= 12 ? t("{n} days (off a bye)", { n: c.rest_days }) : c.rest_days <= 5 ? t("{n} days (short week)", { n: c.rest_days }) : t("{n} days", { n: c.rest_days })) },
  ];
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-[11px] uppercase tracking-wide text-muted">
            <th className="py-2 text-left font-semibold"><span className="flex items-center gap-2"><TeamBadge abbr={g.away.abbreviation} size={20} league="nfl" />{g.away.abbreviation}</span></th>
            <th />
            <th className="py-2 text-right font-semibold"><span className="flex items-center justify-end gap-2">{g.home.abbreviation}<TeamBadge abbr={g.home.abbreviation} size={20} league="nfl" /></span></th>
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
            <td className="py-2 text-center text-xs text-muted">{t("Results")}</td>
            <td className="py-2"><div className="flex justify-end"><FormChips results={g.context.home.last5.results} /></div></td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

async function FinalSummary({ g, p }: { g: NflGameDetail; p: NflPredictions }) {
  const t = await getT();
  const w = p.markets!.winner!;
  const favHome = w.home.probability >= 0.5;
  const tie = g.status === "final" && g.winner_team_id === null;
  const correct = g.winner_team_id !== null && (favHome ? g.home.team_id : g.away.team_id) === g.winner_team_id;
  const ps = p.markets!.projected_score;
  return (
    <Card className="p-4">
      <h2 className="mb-3 text-sm font-semibold">{t("How the forecast did")}</h2>
      {g.status === "final" ? (
        <div className={`rounded-xl p-4 ${tie ? "bg-surface-2" : correct ? "bg-win-soft" : "bg-loss-soft"}`}>
          <div className={`text-sm font-semibold ${tie ? "" : correct ? "text-win" : "text-loss"}`}>{tie ? t("Tie: winner forecasts push") : correct ? `✓ ${t("Favorite won")}` : `✗ ${t("Underdog won")}`}</div>
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

async function AboutForecast({ p }: { p: NflPredictions }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  return (
    <Card className="p-4 text-xs">
      <h2 className="mb-2 text-sm font-semibold">{t("About this forecast")}</h2>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-muted">
        <dt>{t("Type")}</dt><dd className="text-ink">{p.kind === "live" ? t("Published before kickoff") : t("Backtest (regenerated after the fact)")}</dd>
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
          {["<b>Winner</b>: the final result including overtime. NFL regular-season games can end tied; a tie settles both sides as a <i>push</i>.",
            "<b>Spread</b>: a side covers if its final margin plus the handicap is above zero. A whole-number handicap that lands exactly is a push, never a loss.",
            "<b>Total</b>: combined final points, overtime included. Over 44.5 means 45 or more.",
            "<b>Player thresholds</b>: “60+” means at least 60 yards. “Over 59.5” means 60 or more. “Anytime TD” counts rushing and receiving touchdowns.",
            "<b>Did not play</b>: if a player is inactive or doesn’t take an offensive snap, their forecasts are <i>void</i>, not misses. Players listed Out or Doubtful get no forecast.",
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

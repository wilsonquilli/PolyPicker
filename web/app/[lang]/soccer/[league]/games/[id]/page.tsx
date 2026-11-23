import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { RelativeTime } from "@/components/client";
import { SoccerBadge } from "@/components/soccer/MatchCard";
import { Breadcrumbs, Card, EmptyState, FormChips, Pill, SectionTitle, Skeleton, StatusPill, TriBar } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTimeET, longDate, pct } from "@/lib/format";
import { decidedText, soccerLeague, stageText, type SoccerGameDetail, type SoccerPredictions, type SoccerTeamStats } from "@/lib/soccer";
import { keyed } from "@/lib/i18n";
import { rich } from "@/lib/i18n/rich";
import { getLang, getT } from "@/lib/i18n/server";

type Params = Promise<{ league: string; id: string }>;

export default function MatchPage({ params }: { params: Params }) {
  return (
    <Suspense fallback={<div className="flex flex-col gap-6"><Skeleton className="h-16 w-96 max-w-full" /><Skeleton className="h-80" /></div>}>
      <Match params={params} />
    </Suspense>
  );
}

async function Match({ params }: { params: Params }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const { league, id } = await params;
  const lg = soccerLeague(league);
  if (!lg) notFound();
  if (!/^\d+$/.test(id)) notFound();
  const [dRes, pRes] = await Promise.all([api<SoccerGameDetail>(`${lg.api}/games/${id}`), api<SoccerPredictions>(`${lg.api}/games/${id}/predictions`)]);
  if (!dRes) notFound();
  const g = dRes.data, p = pRes?.data, m = p?.markets;
  const final = g.status === "final";
  const r = m?.result;
  const hasForecast = !!(r?.home && r.draw && r.away);
  const stage = stageText(g, t);
  const extra = final ? decidedText(g, t) : null;
  // Exact-score hits are judged on the 90-minute score.
  const s90 = g.score_90 ?? (final ? { home: g.home.score, away: g.away.score } : null);

  return (
    <div>
      <Breadcrumbs items={[{ href: lg.base, label: lg.name }, { href: `${lg.base}?date=${g.game_date}`, label: longDate(g.game_date, lang) }, { label: `${g.home.abbreviation} v ${g.away.abbreviation}` }]} />
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-3 sm:gap-4">
          <div className="flex shrink-0 gap-1 sm:gap-1.5"><SoccerBadge team={g.home} size={44} /><SoccerBadge team={g.away} size={44} /></div>
          <div>
            <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">
              {g.home.name} {final && g.home.score !== null ? <span className="tabular">{g.home.score}–{g.away.score}</span> : t("vs")} {g.away.name}
            </h1>
            {(stage || extra || g.note) && (
              <div className="mt-1 flex flex-wrap items-center gap-2 text-sm">
                {stage && <Pill tone="accent">{stage}</Pill>}
                {extra && <span className="font-medium">{extra}{g.score_90 ? ` · ${t("{h}–{a} after 90 minutes", { h: g.score_90.home ?? 0, a: g.score_90.away ?? 0 })}` : ""}</span>}
                {g.note && <span className="text-muted">{t(g.note.replace(/^(1st|2nd) Leg( - )?/i, "")) || null}</span>}
                {g.neutral && <span className="text-muted">{t("· Neutral venue")}</span>}
              </div>
            )}
            <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted">
              <StatusPill status={g.status} />
              <span>{dateTimeET(g.tipoff_utc, lang)}</span>
              {g.venue && <span>· {g.venue}</span>}
              {g.half_time && final && <span>· {t("HT")} {g.half_time.home}–{g.half_time.away}</span>}
              {g.verified_by_openfootball && <Pill tone="win" className="!font-medium">{t("Result cross-checked")}</Pill>}
            </div>
          </div>
        </div>
        {p?.published_at && (
          <div className="text-xs text-muted sm:text-right">
            <Pill tone={p.forecast_status === "current" ? "win" : p.forecast_status === "backtest" || p.forecast_status === "stale" ? "warn" : "neutral"}>
              {t({ current: "Current", stale: "Stale: newer data available", backtest: "Backtest (not published live)", pending: "Pending", unavailable: "Unavailable" }[p.forecast_status])}
            </Pill>
            <div className="mt-1">{t("Forecast")}{" "}<RelativeTime iso={p.published_at} prefix={`${t("published")} `} /> · {t("rev")} {p.revision}</div>
          </div>
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex min-w-0 flex-col gap-6">
          {!hasForecast ? (
            <EmptyState title={p?.forecast_status === "pending" ? t("Forecast not published yet") : t("No forecast for this match")}>
              {p?.forecast_status === "pending" ? t("Forecasts are published for matches in the next 10 days.") : t("This match was not forecast.")}
            </EmptyState>
          ) : (
            <>
              <Card className="p-5">
                <div className="grid grid-cols-3 gap-2 sm:gap-3">
                  {([["home", g.home.short_name ?? g.home.name, m!.goals.home?.expected], ["draw", t("Draw"), undefined], ["away", g.away.short_name ?? g.away.name, m!.goals.away?.expected]] as const).map(([k, label, xg]) => {
                    const pv = r![k]!.probability, top = Math.max(r!.home!.probability, r!.draw!.probability, r!.away!.probability) === pv;
                    const res = r![k]!.outcome;
                    return (
                      <div key={k} className={`rounded-xl px-2 py-3 text-center sm:px-4 ${top ? "bg-accent-soft" : "bg-surface-2"}`}>
                        <div className="truncate text-xs text-muted sm:text-sm">{label}</div>
                        <div className={`tabular text-3xl font-bold sm:text-4xl ${top ? "text-accent" : ""}`}>{Math.round(pv * 100)}%</div>
                        {xg !== undefined && <div className="tabular text-[11px] text-muted sm:text-xs">{t("{n} expected goals", { n: xg.toFixed(2) })}</div>}
                        {res?.state === "final" && res.result === "hit" && <div className="mt-1 text-xs font-semibold text-win">{t("✓ Result")}</div>}
                      </div>
                    );
                  })}
                </div>
                <div className="mt-4"><TriBar home={r!.home!.probability} draw={r!.draw!.probability} away={r!.away!.probability} labels={[g.home.abbreviation, g.away.abbreviation]} height={10} /></div>
                <p className="mt-2 text-xs text-muted">{t("Chance of each result after 90 minutes (plus stoppage time). The three add up to 100%.")}</p>
              </Card>

              <div className="grid gap-4 md:grid-cols-2">
                {m!.top_scores && m!.top_scores.length > 0 && (
                  <Card className="p-5">
                    <h3 className="mb-3 font-semibold">{t("Most likely scores")}</h3>
                    <ul className="flex flex-col gap-1.5">
                      {m!.top_scores.map((s) => {
                        const actual = final && s90?.home === s.home && s90?.away === s.away;
                        return (
                          <li key={`${s.home}-${s.away}`} className="flex items-center gap-3 text-sm">
                            <span className={`tabular w-12 font-semibold ${actual ? "text-win" : ""}`}>{s.home}–{s.away}</span>
                            <div className="h-2 flex-1 rounded-full bg-surface-2"><div className="h-2 rounded-full bg-accent" style={{ width: `${Math.min(100, s.p * 500)}%` }} /></div>
                            <span className="tabular w-12 text-right text-muted">{pct(s.p, 1)}</span>
                          </li>
                        );
                      })}
                    </ul>
                    <p className="mt-2 text-[11px] text-muted">{t("{team} score first. Exact scores are rarely above 15% likely.", { team: g.home.abbreviation })}</p>
                  </Card>
                )}
                <Card className="p-5">
                  <h3 className="mb-1 font-semibold">{t("Goals")}</h3>
                  <div className="tabular mb-3 text-sm text-muted">{t("Expected total:")}{" "}<b className="text-ink">{m!.totals.expected?.toFixed(2) ?? "—"}</b>
                    {m!.totals.outcome?.state === "final" && <> · {t("actual")} {m!.totals.outcome.observed}</>}</div>
                  {m!.totals.probabilities_available ? (
                    <ul className="flex flex-col gap-1.5">
                      {m!.totals.lines.map((l) => (
                        <li key={l.line} className="flex items-center gap-3 rounded-xl bg-surface-2 px-3 py-2 text-sm">
                          <span className="flex-1">{t(l.label)}</span>
                          {l.outcome?.state === "final" && <Pill tone={l.outcome.result === "hit" ? "win" : "loss"}>{keyed(t, `result:${l.outcome.result}`, l.outcome.result ?? "")}</Pill>}
                          <span className="tabular w-12 text-right font-bold">{pct(l.probability)}</span>
                        </li>
                      ))}
                      {m!.btts && (
                        <li className="flex items-center gap-3 rounded-xl bg-accent-soft px-3 py-2 text-sm">
                          <span className="flex-1">{t("Both teams score")}</span>
                          {m!.btts.outcome?.state === "final" && <Pill tone={m!.btts.outcome.result === "hit" ? "win" : "loss"}>{keyed(t, `result:${m!.btts.outcome.result}`, m!.btts.outcome.result ?? "")}</Pill>}
                          <span className="tabular w-12 text-right font-bold">{pct(m!.btts.probability)}</span>
                        </li>
                      )}
                    </ul>
                  ) : <p className="text-xs text-muted">{t("Goal-line probabilities aren’t published: they didn’t pass calibration checks.")}</p>}
                </Card>
              </div>

              {m!.factors && m!.factors.length > 0 && (
                <Card className="p-5">
                  <SectionTitle>{t("Why the model leans this way")}</SectionTitle>
                  <Factors factors={m!.factors} home={g.home.abbreviation} away={g.away.abbreviation} />
                </Card>
              )}
            </>
          )}

          {final && g.team_stats.length === 2 && <Stats g={g} />}
          <Card className="p-5">
            <SectionTitle>{t("Recent league form")}</SectionTitle>
            <div className="grid gap-4 sm:grid-cols-2">
              {([[g.home, g.context.home_form], [g.away, g.context.away_form]] as const).map(([tm, f]) => (
                <div key={tm.team_id} className="flex items-center justify-between gap-3 rounded-xl bg-surface-2 px-3 py-2">
                  <Link href={`${lg.base}/teams/${tm.team_id}`} className="flex items-center gap-2 font-medium hover:underline"><SoccerBadge team={tm} size={22} />{tm.short_name ?? tm.name}</Link>
                  {f.length ? <FormChips results={f} /> : <span className="text-xs text-muted">{t("No matches yet")}</span>}
                </div>
              ))}
            </div>
          </Card>
          {g.context.head_to_head.length > 0 && (
            <Card className="p-5">
              <SectionTitle>{t("Head to head · recent league meetings")}</SectionTitle>
              <ul className="divide-y divide-line text-sm">
                {g.context.head_to_head.map((h) => {
                  const ab = (tid: number) => (tid === g.home.team_id ? g.home.abbreviation : g.away.abbreviation);
                  return (
                    <li key={h.game_id} className="flex items-center justify-between py-2">
                      <Link href={`${lg.base}/games/${h.game_id}`} className="hover:underline">{longDate(h.date, lang)}</Link>
                      <span className="tabular">{ab(h.home_team_id)} {h.home_score}–{h.away_score} {ab(h.away_team_id)}</span>
                    </li>
                  );
                })}
              </ul>
            </Card>
          )}
          <Card className="p-5">
            <details>
              <summary className="cursor-pointer text-lg font-semibold tracking-tight">{t("How these forecasts resolve")}</summary>
              <ul className="mt-3 list-disc space-y-1.5 pl-5 text-sm text-muted">
                {["<b>Result</b>: home win, draw or away win after 90 minutes plus stoppage time. League matches have no extra time or penalties.",
                  "<b>Goals</b>: over 2.5 means 3 or more goals in total. Own goals count.",
                  "<b>Both teams score</b>: each side scores at least once.",
                  "<b>Postponed or abandoned</b> matches stay pending until replayed, or become void. Published forecasts are never edited afterwards.",
                ].map((line) => <li key={line}>{rich(t(line), { b: (c) => <b className="text-ink">{c}</b> })}</li>)}
              </ul>
            </details>
          </Card>
        </div>

        <aside className="flex flex-col gap-4 lg:sticky lg:top-32 lg:self-start">
          {hasForecast && final && (
            <Card className="p-4">
              <h2 className="mb-3 text-sm font-semibold">{t("How the forecast did")}</h2>
              {(() => {
                const pick = (["home", "draw", "away"] as const).reduce((a, b) => (r![b]!.probability > r![a]!.probability ? b : a));
                const ok = ({ home: "H", draw: "D", away: "A" } as const)[pick] === g.result;
                return (
                  <div className={`rounded-xl p-4 ${ok ? "bg-win-soft" : "bg-loss-soft"}`}>
                    <div className={`text-sm font-semibold ${ok ? "text-win" : "text-loss"}`}>{ok ? `✓ ${t("Most likely result happened")}` : `✗ ${t("A less likely result happened")}`}</div>
                    <div className="mt-1 text-xs text-muted">{t("Model’s pick: {pick} ({p}%). Actual result had {q}%.", {
                      pick: pick === "draw" ? t("draw") : t("{team} win", { team: pick === "home" ? g.home.abbreviation : g.away.abbreviation }),
                      p: Math.round(r![pick]!.probability * 100),
                      q: Math.round(r![g.result === "H" ? "home" : g.result === "A" ? "away" : "draw"]!.probability * 100) })}</div>
                  </div>
                );
              })()}
            </Card>
          )}
          {p?.published_at && (
            <Card className="p-4 text-xs">
              <h2 className="mb-2 text-sm font-semibold">{t("About this forecast")}</h2>
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-muted">
                <dt>{t("Type")}</dt><dd className="text-ink">{p.kind === "live" ? t("Published before kickoff") : t("Backtest (regenerated after the fact)")}</dd>
                <dt>{t("Revision")}</dt><dd className="text-ink">{p.revision}</dd>
                <dt>{t("Data cutoff")}</dt><dd className="text-ink">{p.data_cutoff_utc ? dateTimeET(p.data_cutoff_utc, lang) : "—"}</dd>
                <dt>{t("Model")}</dt><dd className="truncate text-ink">{p.model_version ?? "—"}</dd>
              </dl>
              {p.notes?.kind && <p className="mt-2 text-muted">{t(p.notes.kind)}</p>}
              {p.notes?.model && <p className="mt-2 text-muted">{t(p.notes.model)}</p>}
              <p className="mt-2 text-muted">{t("Estimates, not guarantees. PolyPicker does not offer wagering.")}</p>
            </Card>
          )}
        </aside>
      </div>
    </div>
  );
}

async function Factors({ factors, home, away }: { factors: NonNullable<NonNullable<SoccerPredictions["markets"]>["factors"]>; home: string; away: string }) {
  const t = await getT();
  const max = Math.max(...factors.map((f) => Math.abs(f.home_goal_edge)), 0.1);
  return (
    <div>
      <div className="mb-2 flex justify-between text-[11px] font-semibold uppercase tracking-wide text-muted"><span>{t("Favors {team}", { team: away })}</span><span>{t("Favors {team}", { team: home })}</span></div>
      <ul className="flex flex-col gap-2.5">
        {factors.map((f) => {
          const w = (Math.abs(f.home_goal_edge) / max) * 50;
          return (
            <li key={f.factor}>
              <div className="mb-1 flex justify-between gap-3 text-sm"><span>{t(f.factor)}</span>
                <span className="tabular shrink-0 text-muted">{f.favors === "home" ? home : away} +{Math.abs(f.home_goal_edge).toFixed(2)} {t("goals")}</span></div>
              <div className="relative h-2 rounded-full bg-surface-2">
                <div className="absolute inset-y-0 left-1/2 w-px bg-line" />
                <div className="absolute inset-y-0 rounded-full bg-accent" style={f.favors === "home" ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%`, opacity: 0.55 }} />
              </div>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-xs text-muted">{t("Approximate effect of each input on the expected goal difference. Statistical associations, not causes.")}</p>
    </div>
  );
}

async function Stats({ g }: { g: SoccerGameDetail }) {
  const t = await getT();
  const h = g.team_stats.find((s) => s.is_home)!, a = g.team_stats.find((s) => !s.is_home)!;
  const rows: [string, (s: SoccerTeamStats) => number | null, string?][] = [
    ["Possession", (s) => s.possession, "%"], ["Shots", (s) => s.shots], ["Shots on target", (s) => s.shots_on_target],
    ["Corners", (s) => s.corners], ["Fouls", (s) => s.fouls], ["Yellow cards", (s) => s.yellow_cards], ["Red cards", (s) => s.red_cards],
    ["Saves", (s) => s.saves], ["Offsides", (s) => s.offsides],
  ];
  return (
    <Card className="p-5">
      <SectionTitle>{t("Match stats")}</SectionTitle>
      <ul className="flex flex-col gap-3">
        {rows.map(([label, f, unit]) => {
          const hv = f(h) ?? 0, av = f(a) ?? 0, tot = hv + av || 1;
          return (
            <li key={label}>
              <div className="tabular mb-1 flex justify-between text-sm"><b>{hv}{unit}</b><span className="text-xs text-muted">{t(label)}</span><b>{av}{unit}</b></div>
              <div className="flex h-1.5 gap-1"><div className="flex flex-1 justify-end rounded-full bg-surface-2"><div className="rounded-full bg-accent" style={{ width: `${(hv / tot) * 100}%` }} /></div>
                <div className="flex-1 rounded-full bg-surface-2"><div className="h-full rounded-full bg-faint" style={{ width: `${(av / tot) * 100}%` }} /></div></div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

import Link from "next/link";
import { Suspense } from "react";
import { GameCardSkeleton } from "@/components/GameCard";
import { MatchCard, SoccerBadge } from "@/components/soccer/MatchCard";
import { Card, EmptyState, Pill, TriBar } from "@/components/ui";
import { api } from "@/lib/api";
import { addDays, longDate, shortDate, weekday } from "@/lib/format";
import { notFound } from "next/navigation";

import { kickoffTime, soccerLeague, type SoccerGame, type SoccerLeague } from "@/lib/soccer";
import { getLang, getT } from "@/lib/i18n/server";

type SP = Promise<{ [k: string]: string | string[] | undefined }>;
type DateRow = { date: string; games: number; final: number; scheduled: number };

type Params = Promise<{ league: string }>;

export async function generateMetadata({ params }: { params: Params }) {
  const lg = soccerLeague((await params).league);
  const t = await getT();
  return lg ? { title: lg.name, description: t("{league} match forecasts: home, draw and away probabilities, expected goals and likely scores.", { league: lg.name }) } : {};
}

export default function LeagueHome({ params, searchParams }: { params: Params; searchParams: SP }) {
  return (
    <Suspense fallback={<Skeleton />}>
      <Matches params={params} searchParams={searchParams} />
    </Suspense>
  );
}

async function Matches({ params, searchParams }: { params: Params; searchParams: SP }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const lg = soccerLeague((await params).league);
  if (!lg) notFound();
  const sp = await searchParams;
  const view = sp.view === "results" ? "results" : sp.view === "upcoming" ? "upcoming" : "day";
  const datesRes = await api<DateRow[]>(`${lg.api}/games/dates`);
  if (!datesRes) throw new Error(t("{league} schedule unavailable", { league: lg.name }));
  const today = String(datesRes.meta.extra?.today);
  const season = String(datesRes.meta.extra?.season_label ?? "");
  const dates = datesRes.data;
  const nextDate = dates.find((d) => d.date >= today && d.scheduled > 0)?.date ?? dates[dates.length - 1]?.date ?? today;
  const date = typeof sp.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(sp.date) ? sp.date : nextDate;

  let games: SoccerGame[] = [];
  let heading = "";
  if (view === "upcoming") {
    games = (await api<SoccerGame[]>(`${lg.api}/games`, { date_from: today, date_to: addDays(today, 10), status: "scheduled", limit: 100 }))?.data ?? [];
    heading = t("Next 10 days");
  } else if (view === "results") {
    games = ((await api<SoccerGame[]>(`${lg.api}/games`, { date_from: addDays(today, -10), date_to: today, status: "final", limit: 100 }))?.data ?? []).reverse();
    heading = t("Last 10 days");
  } else {
    games = (await api<SoccerGame[]>(`${lg.api}/games`, { date, limit: 50 }))?.data ?? [];
    heading = date === today ? `${t("Today")} · ${longDate(date, lang)}` : longDate(date, lang);
  }
  const upcoming = (view === "day" ? games : []).filter((g) => g.prediction && g.status === "scheduled");
  const featured = [...upcoming].sort((a, b) => a.prediction!.pick_probability - b.prediction!.pick_probability)[0];
  const confident = [...upcoming].sort((a, b) => b.prediction!.pick_probability - a.prediction!.pick_probability).slice(0, 5);
  const groups = new Map<string, SoccerGame[]>();
  for (const g of games) groups.set(g.game_date, [...(groups.get(g.game_date) ?? []), g]);

  const tab = (v: string, label: string, href: string) => (
    <Link href={href} aria-current={view === v ? "page" : undefined}
      className={`rounded-lg px-3 py-1.5 text-sm font-medium ${view === v ? "bg-surface text-ink shadow-card" : "text-muted"}`}>{label}</Link>
  );

  return (
    <div className="flex flex-col gap-8">
      {featured && upcoming.length > 1 && (
        <section className="grid gap-4 lg:grid-cols-3">
          <Featured game={featured} />
          <Confident games={confident} />
        </section>
      )}
      <section>
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wider text-accent">{lg.name} · {season}</p>
            <h1 className="text-2xl font-bold tracking-tight">{heading}</h1>
          </div>
          <div className="flex gap-1 self-start rounded-xl bg-surface-2 p-1 sm:self-auto">
            {tab("day", t("Match day"), lg.base)}{tab("upcoming", t("Upcoming"), `${lg.base}?view=upcoming`)}{tab("results", t("Results"), `${lg.base}?view=results`)}
          </div>
        </div>
        {view === "day" && <DateStrip lg={lg} dates={dates} selected={date} today={today} />}
        {games.length === 0 ? (
          <EmptyState title={t("No matches")}>{view === "results" ? t("No completed matches in the last 10 days.") : t("No {league} matches on this date. Pick another.", { league: lg.name })}</EmptyState>
        ) : (
          <div className="mt-4 flex flex-col gap-8">
            {[...groups.entries()].map(([d, gs]) => (
              <div key={d}>
                {view !== "day" && <h3 className="mb-3 text-sm font-semibold text-muted">{longDate(d, lang)} · {t(gs.length === 1 ? "{n} match" : "{n} matches", { n: gs.length })}</h3>}
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {gs.map((g) => <MatchCard key={g.game_id} game={g} />)}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

async function DateStrip({ lg, dates, selected, today }: { lg: SoccerLeague; dates: DateRow[]; selected: string; today: string }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const idx = Math.max(0, dates.findIndex((d) => d.date >= selected));
  const win = dates.slice(Math.max(0, idx - 4), idx + 5);
  return (
    <nav aria-label={t("Choose a match date")} className="no-scrollbar -mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1">
      {win.map((d) => {
        const active = d.date === selected;
        return (
          <Link key={d.date} href={`${lg.base}?date=${d.date}`} aria-current={active ? "date" : undefined}
            className={`flex min-w-[68px] shrink-0 flex-col items-center rounded-xl border px-3 py-1.5 text-xs transition ${
              active ? "border-accent bg-accent-soft text-accent" : "border-line bg-surface text-muted hover:text-ink"}`}>
            <span className="font-semibold">{d.date === today ? t("Today") : weekday(d.date, lang)}</span>
            <span className="tabular">{shortDate(d.date, lang)}</span>
            <span className="text-[10px] opacity-80">{t(d.games === 1 ? "{n} match" : "{n} matches", { n: d.games })}</span>
          </Link>
        );
      })}
    </nav>
  );
}

async function Featured({ game }: { game: SoccerGame }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const p = game.prediction!;
  return (
    <Link href={`/soccer/${game.league}/games/${game.game_id}`} className="group lg:col-span-2">
      <Card className="flex h-full flex-col gap-5 p-6 transition group-hover:border-accent/40">
        <div className="flex items-center gap-2 text-xs text-muted"><Pill tone="accent">{t("Closest match")}</Pill><span>{kickoffTime(game.tipoff_utc, lang)}</span></div>
        <div className="flex items-center gap-4">
          <div className="flex gap-1.5"><SoccerBadge team={game.home} size={44} /><SoccerBadge team={game.away} size={44} /></div>
          <div>
            <h2 className="text-xl font-bold tracking-tight sm:text-2xl">{game.home.name} vs {game.away.name}</h2>
            <p className="text-sm text-muted">{game.home.record} · {game.away.record} (W-D-L)</p>
          </div>
        </div>
        <div className="grid grid-cols-3 gap-2 sm:gap-3">
          {([["home", game.home.short_name ?? game.home.name, p.p_home, p.xg_home], ["draw", t("Draw"), p.p_draw, undefined],
            ["away", game.away.short_name ?? game.away.name, p.p_away, p.xg_away]] as const).map(([k, label, v, xg]) => (
            <div key={k} className={`rounded-xl px-3 py-3 text-center ${p.pick === k ? "bg-accent-soft" : "bg-surface-2"}`}>
              <div className="truncate text-xs text-muted">{label}</div>
              <div className={`tabular text-2xl font-bold ${p.pick === k ? "text-accent" : ""}`}>{Math.round(v * 100)}%</div>
              {xg !== undefined && <div className="tabular text-[11px] text-muted">{t("{n} exp. goals", { n: xg.toFixed(1) })}</div>}
            </div>
          ))}
        </div>
        <div className="mt-auto flex flex-col gap-2">
          <TriBar home={p.p_home} draw={p.p_draw} away={p.p_away} labels={[game.home.abbreviation, game.away.abbreviation]} height={8} />
          <span className="self-end text-xs font-semibold text-accent group-hover:underline">{t("View full forecast →")}</span>
        </div>
      </Card>
    </Link>
  );
}

async function Confident({ games }: { games: SoccerGame[] }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  return (
    <Card className="p-5">
      <h2 className="mb-3 text-sm font-semibold">{t("Most confident forecasts")}</h2>
      <ul className="flex flex-col divide-y divide-line">
        {games.map((g) => {
          const p = g.prediction!;
          const team = p.pick === "home" ? g.home : p.pick === "away" ? g.away : null;
          return (
            <li key={g.game_id}>
              <Link href={`/soccer/${g.league}/games/${g.game_id}`} className="flex items-center gap-3 py-2.5 hover:opacity-80">
                {team ? <SoccerBadge team={team} size={30} /> : <span className="inline-flex h-[30px] w-[30px] items-center justify-center rounded-lg bg-surface-2 text-[10px] font-bold">X</span>}
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{team ? t("{team} to win", { team: team.short_name ?? team.name }) : t("Draw")}</div>
                  <div className="truncate text-[11px] text-muted">{g.home.abbreviation} vs {g.away.abbreviation} · {kickoffTime(g.tipoff_utc, lang)}</div>
                </div>
                <span className="tabular text-sm font-bold">{Math.round(p.pick_probability * 100)}%</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function Skeleton() {
  return (
    <div className="flex flex-col gap-6">
      <div className="h-8 w-64 animate-pulse rounded-lg bg-surface-2" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">{Array.from({ length: 8 }).map((_, i) => <GameCardSkeleton key={i} />)}</div>
    </div>
  );
}
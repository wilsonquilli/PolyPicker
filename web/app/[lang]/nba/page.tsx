import Link from "next/link";
import { Suspense } from "react";
import { GameCard, GameCardSkeleton } from "@/components/GameCard";
import { Card, EmptyState, Gauge, Pill, ProbBar, TeamBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { addDays, longDate, shortDate, tipoffTime, weekday } from "@/lib/format";
import type { GameSummary } from "@/lib/types";
import { getLang, getT } from "@/lib/i18n/server";

type View = "today" | "tomorrow" | "upcoming" | "results";
type SP = Promise<{ [k: string]: string | string[] | undefined }>;

export default function Home({ searchParams }: { searchParams: SP }) {
  return (
    <Suspense fallback={<SlateSkeleton />}>
      <Slate searchParams={searchParams} />
    </Suspense>
  );
}

async function Slate({ searchParams }: { searchParams: SP }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const sp = await searchParams;
  const view = (["today", "tomorrow", "upcoming", "results"].includes(String(sp.view)) ? sp.view : "today") as View;
  const datesRes = await api<{ date: string; games: number; final: number; scheduled: number }[]>("/games/dates");
  if (!datesRes) throw new Error(t("Schedule unavailable"));
  const today = String(datesRes.meta.extra?.today);
  const dates = datesRes.data;
  const date = typeof sp.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(sp.date) ? sp.date
    : view === "tomorrow" ? addDays(today, 1) : today;

  let games: GameSummary[] = [];
  let heading = "";
  if (view === "upcoming" && !sp.date) {
    games = (await api<GameSummary[]>("/games", { date_from: addDays(today, 1), date_to: addDays(today, 7), status: "scheduled", limit: 200 }))?.data ?? [];
    heading = t("Next 7 days");
  } else if (view === "results" && !sp.date) {
    games = ((await api<GameSummary[]>("/games", { date_from: addDays(today, -7), date_to: addDays(today, -1), status: "final", limit: 200 }))?.data ?? []).reverse();
    heading = t("Last 7 days");
  } else {
    games = (await api<GameSummary[]>("/games", { date, limit: 100 }))?.data ?? [];
    heading = date === today ? `${t("Today")} · ${longDate(date, lang)}` : date === addDays(today, 1) ? `${t("Tomorrow")} · ${longDate(date, lang)}` : longDate(date, lang);
  }

  const showHero = (view === "today" || view === "tomorrow") && !sp.date;
  const forecasted = games.filter((g) => g.prediction && g.status === "scheduled");
  const featured = [...forecasted].sort((a, b) => Math.abs(a.prediction!.p_home_win - 0.5) - Math.abs(b.prediction!.p_home_win - 0.5))[0];
  const confident = [...forecasted].sort((a, b) => Math.abs(b.prediction!.p_home_win - 0.5) - Math.abs(a.prediction!.p_home_win - 0.5)).slice(0, 5);

  return (
    <div className="flex flex-col gap-8">
      {showHero && featured && (
        <section className="grid gap-4 lg:grid-cols-3">
          <FeaturedCard game={featured} />
          <ConfidentList games={confident} />
        </section>
      )}

      <section>
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wider text-accent">NBA</p>
            <h1 className="text-2xl font-bold tracking-tight">{heading}</h1>
          </div>
          {(view === "today" || view === "tomorrow" || sp.date) && (
            <DateStrip dates={dates} selected={date} today={today} />
          )}
        </div>
        {games.length === 0 ? (
          <EmptyState title={t("No games on this slate")}>
            {view === "results" ? t("No completed games in the last week.") : t("There are no NBA games scheduled for this day. Try another date.")}
          </EmptyState>
        ) : view === "upcoming" || view === "results" ? (
          <GroupedGrid games={games} />
        ) : (
          <Grid games={games} />
        )}
      </section>
    </div>
  );
}

function Grid({ games }: { games: GameSummary[] }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {games.map((g) => <GameCard key={g.game_id} game={g} />)}
    </div>
  );
}

async function GroupedGrid({ games }: { games: GameSummary[] }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const groups = new Map<string, GameSummary[]>();
  for (const g of games) groups.set(g.game_date, [...(groups.get(g.game_date) ?? []), g]);
  return (
    <div className="flex flex-col gap-8">
      {[...groups.entries()].map(([d, gs]) => (
        <div key={d}>
          <h3 className="mb-3 text-sm font-semibold text-muted">{longDate(d, lang)} · {t(gs.length === 1 ? "{n} game" : "{n} games", { n: gs.length })}</h3>
          <Grid games={gs} />
        </div>
      ))}
    </div>
  );
}

async function DateStrip({ dates, selected, today }: { dates: { date: string; games: number }[]; selected: string; today: string }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const idx = Math.max(0, dates.findIndex((d) => d.date >= selected));
  const window = dates.slice(Math.max(0, idx - 3), idx + 4);
  return (
    <nav aria-label={t("Choose a date")} className="no-scrollbar flex gap-1.5 overflow-x-auto">
      {window.map((d) => {
        const active = d.date === selected;
        return (
          <Link key={d.date} href={d.date === today ? "/nba" : `/nba?date=${d.date}`} aria-current={active ? "date" : undefined}
            className={`flex min-w-[64px] flex-col items-center rounded-xl border px-3 py-1.5 text-xs transition ${
              active ? "border-accent bg-accent-soft text-accent" : "border-line bg-surface text-muted hover:text-ink"}`}>
            <span className="font-semibold">{d.date === today ? t("Today") : weekday(d.date, lang)}</span>
            <span className="tabular">{shortDate(d.date, lang)}</span>
            <span className="text-[10px] opacity-80">{t(d.games === 1 ? "{n} game" : "{n} games", { n: d.games })}</span>
          </Link>
        );
      })}
    </nav>
  );
}

async function FeaturedCard({ game }: { game: GameSummary }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const p = game.prediction!;
  const fav = p.favorite === "home" ? game.home : game.away;
  const pFav = Math.max(p.p_home_win, p.p_away_win);
  return (
    <Link href={`/nba/games/${game.game_id}`} className="group lg:col-span-2">
      <Card className="flex h-full flex-col gap-5 p-6 transition group-hover:border-accent/40">
        <div className="flex items-center gap-2 text-xs text-muted">
          <Pill tone="accent">{t("Closest matchup")}</Pill>
          <span>{tipoffTime(game.tipoff_utc, lang)}</span>
          {game.is_cup && <span>· {t("NBA Cup")} {game.sub_label ? `· ${t(game.sub_label)}` : ""}</span>}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-6">
          <div className="flex items-center gap-4">
            <div className="flex gap-1.5">
              <TeamBadge abbr={game.away.abbreviation} size={44} />
              <TeamBadge abbr={game.home.abbreviation} size={44} />
            </div>
            <div>
              <h2 className="text-xl font-bold tracking-tight sm:text-2xl">{game.away.name} vs {game.home.name}</h2>
              <p className="text-sm text-muted">{game.away.record} · {game.home.record} · {t("Who wins?")}</p>
            </div>
          </div>
          <Gauge p={pFav} label={t("{team} chance", { team: fav.abbreviation })} size={110} />
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 sm:gap-3">
          {[game.away, game.home].map((s) => {
            const pv = s.team_id === game.home.team_id ? p.p_home_win : p.p_away_win;
            const proj = s.team_id === game.home.team_id ? p.projected_home_points : p.projected_away_points;
            return (
              <div key={s.team_id} className="flex items-center justify-between rounded-xl bg-surface-2 px-4 py-3">
                <div className="flex items-center gap-2">
                  <TeamBadge abbr={s.abbreviation} size={24} />
                  <span className="truncate font-medium">{s.name}</span>
                </div>
                <div className="text-right">
                  <div className="tabular text-lg font-bold">{Math.round(pv * 100)}%</div>
                  {proj !== null && <div className="tabular whitespace-nowrap text-[11px] text-muted">{t("proj. {n} pts", { n: proj.toFixed(0) })}</div>}
                </div>
              </div>
            );
          })}
        </div>
        <div className="mt-auto flex flex-col gap-2">
          <ProbBar home={p.p_home_win} homeAbbr={game.home.abbreviation} awayAbbr={game.away.abbreviation} height={8} />
          <div className="flex items-center justify-between text-xs text-muted">
            <span className="hidden sm:inline">{game.away.abbreviation} {Math.round(p.p_away_win * 100)}% · {game.home.abbreviation} {Math.round(p.p_home_win * 100)}% · {t(p.favorite_label)}</span>
            <span className="font-semibold text-accent group-hover:underline">{t("View full forecast →")}</span>
          </div>
        </div>
      </Card>
    </Link>
  );
}

async function ConfidentList({ games }: { games: GameSummary[] }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  return (
    <Card className="p-5">
      <h2 className="mb-3 text-sm font-semibold">{t("Most confident forecasts")}</h2>
      <ul className="flex flex-col divide-y divide-line">
        {games.map((g) => {
          const p = g.prediction!;
          const fav = p.favorite === "home" ? g.home : g.away;
          const opp = p.favorite === "home" ? g.away : g.home;
          return (
            <li key={g.game_id}>
              <Link href={`/nba/games/${g.game_id}`} className="flex items-center gap-3 py-2.5 hover:opacity-80">
                <TeamBadge abbr={fav.abbreviation} size={30} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{fav.name} <span className="text-muted">{p.favorite === "home" ? "vs" : "@"} {opp.abbreviation}</span></div>
                  <div className="text-[11px] text-muted">{tipoffTime(g.tipoff_utc, lang)}</div>
                </div>
                <span className="tabular text-sm font-bold">{Math.round(Math.max(p.p_home_win, p.p_away_win) * 100)}%</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function SlateSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <div className="h-8 w-64 animate-pulse rounded-lg bg-surface-2" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => <GameCardSkeleton key={i} />)}
      </div>
    </div>
  );
}

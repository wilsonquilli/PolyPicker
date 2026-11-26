import Link from "next/link";
import { Suspense } from "react";
import { GameCard, GameCardSkeleton } from "@/components/GameCard";
import { Card, EmptyState, Gauge, Pill, ProbBar, TeamBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { longDate } from "@/lib/format";
import { kickoff, weekRange, type NflGame, type NflWeek } from "@/lib/nfl";
import { getLang, getT } from "@/lib/i18n/server";

export async function generateMetadata() {
  const t = await getT();
  return { title: t("NFL"), description: t("NFL win probabilities, projected scores, spreads, totals and player prop forecasts.") };
}

type SP = Promise<{ [k: string]: string | string[] | undefined }>;

export default function NflHome({ searchParams }: { searchParams: SP }) {
  return (
    <Suspense fallback={<WeekSkeleton />}>
      <Week searchParams={searchParams} />
    </Suspense>
  );
}

async function Week({ searchParams }: { searchParams: SP }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const sp = await searchParams;
  const weeksRes = await api<NflWeek[]>("/nfl/games/weeks", { season: typeof sp.season === "string" ? sp.season : undefined });
  if (!weeksRes) throw new Error(t("NFL schedule unavailable"));
  const weeks = weeksRes.data;
  const current = weeksRes.meta.extra?.current as { season: number; week: number } | undefined;
  const season = Number(weeksRes.meta.extra?.season);
  const weekNum = typeof sp.week === "string" && /^\d+$/.test(sp.week) ? Number(sp.week) : current?.week ?? weeks[0]?.week;
  const wk = weeks.find((w) => w.week === weekNum);
  const games = (await api<NflGame[]>("/nfl/games", { season, week: weekNum, limit: 50 }))?.data ?? [];

  const upcoming = games.filter((g) => g.prediction && g.status === "scheduled");
  const featured = [...upcoming].sort((a, b) => Math.abs(a.prediction!.p_home_win - 0.5) - Math.abs(b.prediction!.p_home_win - 0.5))[0];
  const confident = [...upcoming].sort((a, b) => Math.abs(b.prediction!.p_home_win - 0.5) - Math.abs(a.prediction!.p_home_win - 0.5)).slice(0, 5);
  const byDay = new Map<string, NflGame[]>();
  for (const g of games) byDay.set(g.game_date, [...(byDay.get(g.game_date) ?? []), g]);

  return (
    <div className="flex flex-col gap-8">
      {featured && (
        <section className="grid gap-4 lg:grid-cols-3">
          <FeaturedCard game={featured} />
          <ConfidentList games={confident} />
        </section>
      )}
      <section>
        <div className="mb-4 flex flex-col gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wider text-accent">NFL · {t("{s} season", { s: season })}</p>
            <h1 className="text-2xl font-bold tracking-tight">{wk ? `${t(wk.label)} · ${weekRange(wk, lang)}` : t("Schedule")}</h1>
          </div>
          <WeekStrip weeks={weeks} selected={weekNum} current={current?.week} season={season} />
        </div>
        {games.length === 0 ? (
          <EmptyState title={t("No games this week")}>{t("Pick another week above.")}</EmptyState>
        ) : (
          <div className="flex flex-col gap-8">
            {[...byDay.entries()].map(([d, gs]) => (
              <div key={d}>
                <h3 className="mb-3 text-sm font-semibold text-muted">{longDate(d, lang)} · {t(gs.length === 1 ? "{n} game" : "{n} games", { n: gs.length })}</h3>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {gs.map((g) => <GameCard key={g.game_id} game={g} league="nfl" />)}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

async function WeekStrip({ weeks, selected, current, season }: { weeks: NflWeek[]; selected?: number; current?: number; season: number }) {
  const t = await getT();
  return (
    <nav aria-label={t("Choose a week")} className="no-scrollbar -mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1">
      {weeks.map((w) => {
        const active = w.week === selected;
        const done = w.final === w.games;
        return (
          <Link key={w.week} href={`/nfl?season=${season}&week=${w.week}`} aria-current={active ? "page" : undefined}
            className={`flex min-w-[72px] shrink-0 flex-col items-center rounded-xl border px-3 py-1.5 text-xs transition ${
              active ? "border-accent bg-accent-soft text-accent" : "border-line bg-surface text-muted hover:text-ink"}`}>
            <span className="whitespace-nowrap font-semibold">{w.game_type === "REG" ? t("Wk {n}", { n: w.week }) : t(`short:${w.label}`) === `short:${w.label}` ? w.label.replace(" Round", "").replace("Conference Championships", "Conf.") : t(`short:${w.label}`)}</span>
            <span className="text-[10px] opacity-80">{w.week === current ? t("This week") : done ? t("Final") : t("{n} games", { n: w.games })}</span>
          </Link>
        );
      })}
    </nav>
  );
}

async function FeaturedCard({ game }: { game: NflGame }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const p = game.prediction!;
  const fav = p.favorite === "home" ? game.home : game.away;
  const pFav = Math.max(p.p_home_win, p.p_away_win);
  return (
    <Link href={`/nfl/games/${game.game_id}`} className="group lg:col-span-2">
      <Card className="flex h-full flex-col gap-5 p-6 transition group-hover:border-accent/40">
        <div className="flex items-center gap-2 text-xs text-muted">
          <Pill tone="accent">{t("Closest matchup")}</Pill>
          <span>{t(game.label ?? "")} · {kickoff(game.tipoff_utc, lang)}</span>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-6">
          <div className="flex items-center gap-4">
            <div className="flex gap-1.5">
              <TeamBadge abbr={game.away.abbreviation} size={44} league="nfl" />
              <TeamBadge abbr={game.home.abbreviation} size={44} league="nfl" />
            </div>
            <div>
              <h2 className="text-xl font-bold tracking-tight sm:text-2xl">{t("{away} at {home}", { away: game.away.name, home: game.home.name })}</h2>
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
                  <TeamBadge abbr={s.abbreviation} size={24} league="nfl" />
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

async function ConfidentList({ games }: { games: NflGame[] }) {
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
              <Link href={`/nfl/games/${g.game_id}`} className="flex items-center gap-3 py-2.5 hover:opacity-80">
                <TeamBadge abbr={fav.abbreviation} size={30} league="nfl" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{fav.name} <span className="text-muted">{p.favorite === "home" ? "vs" : "@"} {opp.abbreviation}</span></div>
                  <div className="text-[11px] text-muted">{kickoff(g.tipoff_utc, lang)}</div>
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

function WeekSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <div className="h-8 w-64 animate-pulse rounded-lg bg-surface-2" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => <GameCardSkeleton key={i} />)}
      </div>
    </div>
  );
}
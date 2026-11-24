import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { LeagueFlag } from "@/components/LeagueFlag";
import { SoccerBadge } from "@/components/soccer/MatchCard";
import { Card, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { addDays, shortDate } from "@/lib/format";
import { todayET } from "@/lib/home";
import { kickoffTime, LEAGUE_SLUGS, REGIONS, SOCCER_LEAGUES, type LeagueSlug, type SoccerGame } from "@/lib/soccer";
import { getLang, getT } from "@/lib/i18n/server";

export async function generateMetadata() {
  const t = await getT();
  return { title: t("Soccer"), description: t("Match forecasts for the Premier League, LaLiga, Bundesliga, Serie A, Ligue 1, Champions League, Europa League, Copa Libertadores, Copa Sudamericana and LigaPro.") };
}

/** Soccer hub: every competition, grouped by region, with its next few matches. */
export default async function SoccerHub() {
  const t = await getT();
  return (
    <div className="flex flex-col gap-8">
      <div>
        <p className="text-xs font-semibold uppercase tracking-wider text-accent">{t("Soccer")}</p>
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">{t("Competitions")}</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted">{t("Home, draw and away chances, expected goals and likely scores for every match, from our own model.")}</p>
      </div>
      <Suspense fallback={<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{LEAGUE_SLUGS.map((s) => <Skeleton key={s} className="h-44" />)}</div>}>
        <Hub />
      </Suspense>
    </div>
  );
}

async function Hub() {
  const t = await getT();
  await connection(); // "today" is per-request
  const today = todayET();
  const next = await Promise.all(LEAGUE_SLUGS.map(async (slug) => {
    try {
      const res = await api<SoccerGame[]>(`/soccer/${slug}/games`, { date_from: today, date_to: addDays(today, 14), status: "scheduled", limit: 3 });
      return [slug, res?.data ?? [], res?.meta.total ?? 0] as const;
    } catch {
      return [slug, null, 0] as const;
    }
  }));
  const by = new Map(next.map(([s, g, n]) => [s, { games: g, total: n }]));
  return (
    <>
      {REGIONS.map((region) => (
        <section key={region}>
          <h2 className="mb-3 text-lg font-semibold tracking-tight">{t(region)}</h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {LEAGUE_SLUGS.filter((s) => SOCCER_LEAGUES[s].region === region).map((s) => <LeagueCard key={s} slug={s} {...by.get(s)!} />)}
          </div>
        </section>
      ))}
    </>
  );
}

async function LeagueCard({ slug, games, total }: { slug: LeagueSlug; games: readonly SoccerGame[] | null; total: number }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const lg = SOCCER_LEAGUES[slug];
  return (
    <Card className="flex flex-col">
      <Link href={`/soccer/${slug}`} className="flex items-center gap-3 border-b border-line px-4 py-3 hover:opacity-80">
        <LeagueFlag slug={slug} size="lg" />
        <div className="min-w-0 flex-1">
          <div className="font-semibold">{lg.name}</div>
          <div className="text-xs text-muted">{t(lg.country)}{lg.kind === "cup" ? ` · ${t("Cup")}` : ""}</div>
        </div>
        <span className="text-xs text-muted">{games === null ? t("Unavailable") : total ? t("{n} in 14 days", { n: total }) : t("No matches soon")}</span>
      </Link>
      <ul className="flex flex-1 flex-col divide-y divide-line">
        {(games ?? []).map((g) => {
          const p = g.prediction;
          return (
            <li key={g.game_id}>
              <Link href={`/soccer/${slug}/games/${g.game_id}`} className="flex items-center gap-2 px-4 py-2.5 text-sm hover:bg-surface-2">
                <SoccerBadge team={g.home} size={20} />
                <span className="min-w-0 flex-1 truncate">{g.home.short_name ?? g.home.name} <span className="text-muted">{t("v")}</span> {g.away.short_name ?? g.away.name}</span>
                <SoccerBadge team={g.away} size={20} />
                <span className="tabular w-24 shrink-0 text-right text-xs text-muted">
                  {p ? `${{ home: g.home.abbreviation, draw: t("Draw"), away: g.away.abbreviation }[p.pick]} ${Math.round(p.pick_probability * 100)}%`
                    : `${shortDate(g.game_date, lang)} ${kickoffTime(g.tipoff_utc, lang).replace(" ET", "")}`}
                </span>
              </Link>
            </li>
          );
        })}
        {games !== null && games.length === 0 && <li className="px-4 py-6 text-center text-xs text-muted">{t("Between rounds. Check back closer to the next match day.")}</li>}
      </ul>
    </Card>
  );
}

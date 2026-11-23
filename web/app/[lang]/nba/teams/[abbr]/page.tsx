import { notFound } from "next/navigation";
import { Suspense } from "react";

import { MarginBars } from "@/components/charts";
import { GameCard } from "@/components/GameCard";
import { Breadcrumbs, Card, EmptyState, FormChips, SectionTitle, Skeleton, StatTile, TeamBadge } from "@/components/ui";
import { api } from "@/lib/api";
import type { GameSummary, TeamForm, TeamRef } from "@/lib/types";

interface TeamDetail {
  team: TeamRef;
  season_label: string;
  season_stats: Record<string, number | null>;
  home_away_record: { home: string; away: string };
  last5: TeamForm;
  last10: TeamForm;
  recent_games: GameSummary[];
  upcoming_games: GameSummary[];
}

type Params = Promise<{ abbr: string }>;

export default function TeamPage({ params }: { params: Params }) {
  return (
    <Suspense fallback={<div className="flex flex-col gap-6"><Skeleton className="h-20 w-80" /><Skeleton className="h-28" /><Skeleton className="h-64" /></div>}>
      <Team params={params} />
    </Suspense>
  );
}

async function Team({ params }: { params: Params }) {
  const { abbr } = await params;
  const res = await api<TeamDetail>(`/teams/${encodeURIComponent(abbr)}`);
  if (!res) notFound();
  const d = res.data;
  const s = d.season_stats;
  const margins = [...d.last10.results].reverse().map((r) => r.points - r.opponent_points);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Breadcrumbs items={[{ href: "/teams", label: "Teams" }, { label: d.team.full_name }]} />
        <div className="flex flex-wrap items-center gap-4">
          <TeamBadge abbr={d.team.abbreviation} size={64} />
          <div>
            <h1 className="text-3xl font-bold tracking-tight">{d.team.full_name}</h1>
            <p className="text-sm text-muted">{d.team.conference}ern Conference · {d.team.division} · {d.season_label}</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-8">
        <StatTile label="Record" value={`${s.wins}-${s.losses}`} sub="regular season" />
        <StatTile label="Home" value={d.home_away_record.home} />
        <StatTile label="Road" value={d.home_away_record.away} />
        <StatTile label="Points" value={s.pts ?? "—"} sub={`allowed ${s.opp_pts ?? "—"}`} />
        <StatTile label="Off. rating" value={s.offensive_rating ?? "—"} sub="pts / 100 poss." />
        <StatTile label="Def. rating" value={s.defensive_rating ?? "—"} sub="lower is better" />
        <StatTile label="Pace" value={s.possessions ?? "—"} sub="poss. / game" />
        <StatTile label="3P%" value={s.fg3_pct ? `${(Number(s.fg3_pct) * 100).toFixed(1)}%` : "—"} />
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        <Card className="p-5">
          <SectionTitle action={<FormChips results={d.last5.results} />}>Last 10 games · point margin</SectionTitle>
          {margins.length ? <MarginBars values={margins} labels={[...d.last10.results].reverse().map((r) => `${r.home ? "vs" : "@"} ${r.opponent}`)} />
            : <p className="text-sm text-muted">No games yet this season.</p>}
          <p className="mt-2 text-sm text-muted">{d.last10.wins}-{d.last10.losses} in the last {d.last10.games} · average margin {d.last10.avg_margin ?? "—"}</p>
        </Card>
        <Card className="p-5">
          <SectionTitle>Recent results</SectionTitle>
          <ul className="divide-y divide-line text-sm">
            {d.last10.results.map((r, i) => (
              <li key={i} className="flex items-center justify-between py-1.5">
                <span className="text-muted">{r.date}</span>
                <span>{r.home ? "vs" : "@"} {r.opponent}</span>
                <span className={`tabular font-semibold ${r.result === "W" ? "text-win" : "text-loss"}`}>{r.result} {r.points}-{r.opponent_points}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <section>
        <SectionTitle>Upcoming games</SectionTitle>
        {d.upcoming_games.length ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            {d.upcoming_games.map((g) => <GameCard key={g.game_id} game={g} />)}
          </div>
        ) : <EmptyState title="No upcoming games scheduled" />}
      </section>

      <section>
        <SectionTitle>Completed games</SectionTitle>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {d.recent_games.map((g) => <GameCard key={g.game_id} game={g} />)}
        </div>
      </section>
    </div>
  );
}

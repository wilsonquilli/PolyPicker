import { notFound } from "next/navigation";
import { Suspense } from "react";
import { MarginBars } from "@/components/charts";
import { GameCard } from "@/components/GameCard";
import { Breadcrumbs, Card, EmptyState, FormChips, SectionTitle, Skeleton, StatTile, TeamBadge } from "@/components/ui";
import { api } from "@/lib/api";
import type { GameSummary, TeamForm, TeamRef } from "@/lib/types";
import { keyed } from "@/lib/i18n";
import { getT } from "@/lib/i18n/server";

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
  const t = await getT();
  const { abbr } = await params;
  const res = await api<TeamDetail>(`/teams/${encodeURIComponent(abbr)}`);
  if (!res) notFound();
  const d = res.data;
  const s = d.season_stats;
  const margins = [...d.last10.results].reverse().map((r) => r.points - r.opponent_points);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Breadcrumbs items={[{ href: "/nba/teams", label: "Teams" }, { label: d.team.full_name }]} />
        <div className="flex flex-wrap items-center gap-4">
          <TeamBadge abbr={d.team.abbreviation} size={64} />
          <div>
            <h1 className="text-3xl font-bold tracking-tight">{d.team.full_name}</h1>
            <p className="text-sm text-muted">{t(d.team.conference === "East" ? "Eastern Conference" : "Western Conference")} · {t(d.team.division ?? "")} · {d.season_label}</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-8">
        <StatTile label={t("Record")} value={`${s.wins}-${s.losses}`} sub={t("regular season")} />
        <StatTile label={t("Home")} value={d.home_away_record.home} />
        <StatTile label={t("Road")} value={d.home_away_record.away} />
        <StatTile label={t("Points")} value={s.pts ?? "—"} sub={t("allowed {n}", { n: s.opp_pts ?? "—" })} />
        <StatTile label={t("Off. rating")} value={s.offensive_rating ?? "—"} sub={t("pts / 100 poss.")} />
        <StatTile label={t("Def. rating")} value={s.defensive_rating ?? "—"} sub={t("lower is better")} />
        <StatTile label={t("Pace")} value={s.possessions ?? "—"} sub={t("poss. / game")} />
        <StatTile label="3P%" value={s.fg3_pct ? `${(Number(s.fg3_pct) * 100).toFixed(1)}%` : "—"} />
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        <Card className="p-5">
          <SectionTitle action={<FormChips results={d.last5.results} />}>{t("Last 10 games · point margin")}</SectionTitle>
          {margins.length ? <MarginBars values={margins} labels={[...d.last10.results].reverse().map((r) => `${r.home ? t("vs") : "@"} ${r.opponent}`)} />
            : <p className="text-sm text-muted">{t("No games yet this season.")}</p>}
          <p className="mt-2 text-sm text-muted">{t("{w}-{l} in the last {n} · average margin {m}", { w: d.last10.wins, l: d.last10.losses, n: d.last10.games, m: d.last10.avg_margin ?? "—" })}</p>
        </Card>
        <Card className="p-5">
          <SectionTitle>{t("Recent results")}</SectionTitle>
          <ul className="divide-y divide-line text-sm">
            {d.last10.results.map((r, i) => (
              <li key={i} className="flex items-center justify-between py-1.5">
                <span className="text-muted">{r.date}</span>
                <span>{r.home ? t("vs") : "@"} {r.opponent}</span>
                <span className={`tabular font-semibold ${r.result === "W" ? "text-win" : "text-loss"}`}>{keyed(t, `form:${r.result}`, r.result)} {r.points}-{r.opponent_points}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <section>
        <SectionTitle>{t("Upcoming games")}</SectionTitle>
        {d.upcoming_games.length ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            {d.upcoming_games.map((g) => <GameCard key={g.game_id} game={g} />)}
          </div>
        ) : <EmptyState title={t("No upcoming games scheduled")} />}
      </section>

      <section>
        <SectionTitle>{t("Completed games")}</SectionTitle>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {d.recent_games.map((g) => <GameCard key={g.game_id} game={g} />)}
        </div>
      </section>
    </div>
  );
}
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { MatchCard, SoccerBadge } from "@/components/soccer/MatchCard";
import { Breadcrumbs, Card, SectionTitle, Skeleton, StatTile } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { soccerLeague, type SoccerGame, type SoccerTeam, type TableRow } from "@/lib/soccer";
import { keyed } from "@/lib/i18n";
import { getLang, getT } from "@/lib/i18n/server";

type Params = Promise<{ league: string; id: string }>;
interface Detail {
  team: SoccerTeam; season: number; season_label: string; standing: TableRow | null;
  averages: { n: number; shots: number | null; shots_on_target: number | null; possession: number | null; shots_against: number | null; shots_on_target_against: number | null } | null;
  games: SoccerGame[];
}

export default function TeamPage({ params }: { params: Params }) {
  return <Suspense fallback={<Skeleton className="h-[600px]" />}><Team params={params} /></Suspense>;
}

async function Team({ params }: { params: Params }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const { league, id } = await params;
  const lg = soccerLeague(league);
  if (!lg) notFound();
  if (!/^\d+$/.test(id)) notFound();
  const res = await api<Detail>(`${lg.api}/teams/${id}`);
  if (!res) notFound();
  const d = res.data, s = d.standing, a = d.averages, tid = d.team.team_id;
  const upcoming = d.games.filter((g) => g.status === "scheduled").slice(0, 4);
  const results = d.games.filter((g) => g.status === "final").reverse();
  return (
    <div className="flex flex-col gap-6">
      <div>
        <Breadcrumbs items={[{ href: `${lg.base}/teams`, label: t("{league} clubs", { league: lg.name }) }, { label: d.team.name }]} />
        <div className="flex items-center gap-4">
          <SoccerBadge team={d.team} size={64} />
          <div><h1 className="text-3xl font-bold tracking-tight">{d.team.name}</h1><p className="text-sm text-muted">{t("{s} season", { s: d.season_label })}</p></div>
        </div>
      </div>
      {s && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          <StatTile label={t("Position")} value={`#${s.position}`} sub={t("{n} points", { n: s.points })} />
          <StatTile label={t("Record")} value={`${s.won}-${s.drawn}-${s.lost}`} sub={t("W-D-L")} />
          <StatTile label={t("Goals")} value={`${s.gf}–${s.ga}`} sub={t("{gd} difference", { gd: `${s.gd > 0 ? "+" : ""}${s.gd}` })} />
          <StatTile label={t("Shots / match")} value={a?.shots ?? "—"} sub={a?.shots_against !== null && a?.shots_against !== undefined ? t("{n} against", { n: a.shots_against }) : undefined} />
          <StatTile label={t("On target / match")} value={a?.shots_on_target ?? "—"} sub={a?.possession ? t("{n}% possession", { n: a.possession }) : undefined} />
        </div>
      )}
      {upcoming.length > 0 && (
        <section>
          <SectionTitle>{t("Next matches")}</SectionTitle>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">{upcoming.map((g) => <MatchCard key={g.game_id} game={g} />)}</div>
        </section>
      )}
      <Card className="overflow-x-auto p-5">
        <SectionTitle>{t("Results")}</SectionTitle>
        {results.length === 0 ? <p className="text-sm text-muted">{t("No completed matches yet.")}</p> : (
          <table className="tabular w-full text-sm">
            <tbody>
              {results.map((g) => {
                const home = g.home.team_id === tid, opp = home ? g.away : g.home;
                const gf = home ? g.home.score : g.away.score, ga = home ? g.away.score : g.home.score;
                const r = gf === ga ? "D" : (gf ?? 0) > (ga ?? 0) ? "W" : "L";
                return (
                  <tr key={g.game_id} className="border-t border-line first:border-0">
                    <td className="py-2 text-muted">{shortDate(g.game_date, lang)}</td>
                    <td className="py-2"><Link href={`${lg.base}/games/${g.game_id}`} className="flex items-center gap-2 hover:underline">
                      {home ? t("vs") : t("at")} <SoccerBadge team={opp} size={18} /> {opp.short_name ?? opp.name}</Link></td>
                    <td className={`py-2 text-right font-semibold ${r === "W" ? "text-win" : r === "L" ? "text-loss" : ""}`}>{keyed(t, `form:${r}`, r)} {gf}–{ga}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

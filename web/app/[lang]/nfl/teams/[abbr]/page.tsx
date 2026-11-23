import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { GameCard } from "@/components/GameCard";
import { Breadcrumbs, Card, SectionTitle, Skeleton, StatTile, TeamBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import type { NflGame, NflStandingRow } from "@/lib/nfl";
import { keyed } from "@/lib/i18n";
import { getLang, getT } from "@/lib/i18n/server";

type Params = Promise<{ abbr: string }>;

interface TeamDetail {
  team: { team_id: number; abbreviation: string; name: string; full_name: string; conference: string; division: string };
  season: number;
  standing: NflStandingRow | null;
  games: NflGame[];
  leaders: { player_id: string; full_name: string; position: string; games: number; passing_yards: number | null; passing_tds: number | null;
    rushing_yards: number | null; receptions: number | null; receiving_yards: number | null; scrimmage_tds: number | null }[];
}

export default function NflTeamPage({ params }: { params: Params }) {
  return (
    <Suspense fallback={<Skeleton className="h-[600px]" />}>
      <Team params={params} />
    </Suspense>
  );
}

async function Team({ params }: { params: Params }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const { abbr } = await params;
  const res = await api<TeamDetail>(`/nfl/teams/${encodeURIComponent(abbr)}`);
  if (!res) notFound();
  const d = res.data;
  const s = d.standing;
  const upcoming = d.games.filter((g) => g.status !== "final").slice(0, 4);
  const results = d.games.filter((g) => g.status === "final").reverse();
  const tid = d.team.team_id;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Breadcrumbs items={[{ href: "/nfl/teams", label: "NFL teams" }, { label: d.team.full_name }]} />
        <div className="flex items-center gap-4">
          <TeamBadge abbr={d.team.abbreviation} size={64} league="nfl" />
          <div>
            <h1 className="text-3xl font-bold tracking-tight">{d.team.full_name}</h1>
            <p className="text-sm text-muted">{d.team.division} · {t("{s} season", { s: d.season })}</p>
          </div>
        </div>
      </div>
      {s && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile label={t("Record")} value={s.record} sub={s.win_pct !== null ? `${s.win_pct.toFixed(3).replace(/^0/, "")} ${t("pct")}` : undefined} />
          <StatTile label={t("Points / game")} value={s.games ? (s.points_for / s.games).toFixed(1) : "—"} />
          <StatTile label={t("Allowed / game")} value={s.games ? (s.points_against / s.games).toFixed(1) : "—"} />
          <StatTile label={t("Point diff")} value={`${s.point_diff > 0 ? "+" : ""}${s.point_diff}`} sub={t("Division {w}-{l}", { w: s.div_wins, l: s.div_losses })} />
        </div>
      )}
      {upcoming.length > 0 && (
        <section>
          <SectionTitle>{t("Upcoming")}</SectionTitle>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {upcoming.map((g) => <GameCard key={g.game_id} game={g} league="nfl" />)}
          </div>
        </section>
      )}
      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="overflow-x-auto p-5">
          <SectionTitle>{t("Results")}</SectionTitle>
          {results.length === 0 ? <p className="text-sm text-muted">{t("No completed games yet.")}</p> : (
            <table className="tabular w-full text-sm">
              <tbody>
                {results.map((g) => {
                  const home = g.home.team_id === tid;
                  const opp = home ? g.away : g.home;
                  const us = home ? g.home.score : g.away.score, them = home ? g.away.score : g.home.score;
                  const res_ = g.winner_team_id === null ? "T" : g.winner_team_id === tid ? "W" : "L";
                  return (
                    <tr key={g.game_id} className="border-t border-line first:border-0">
                      <td className="py-2 text-muted">{g.label?.startsWith("Week ") ? t("Wk {n}", { n: g.label.slice(5) }) : t(g.label ?? "")}</td>
                      <td className="py-2 text-muted">{shortDate(g.game_date, lang)}</td>
                      <td className="py-2"><Link href={`/nfl/games/${g.game_id}`} className="flex items-center gap-2 hover:underline">
                        {home ? t("vs") : "@"} <TeamBadge abbr={opp.abbreviation} size={18} league="nfl" /> {opp.abbreviation}</Link></td>
                      <td className={`py-2 text-right font-semibold ${res_ === "W" ? "text-win" : res_ === "L" ? "text-loss" : ""}`}>{keyed(t, `form:${res_}`, res_)} {us}-{them}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </Card>
        <Card className="overflow-x-auto p-5">
          <SectionTitle>{t("Season leaders")}</SectionTitle>
          <table className="tabular w-full text-sm">
            <thead><tr className="text-[11px] text-muted"><th className="text-left font-medium">{t("Player")}</th><th className="text-right font-medium">{t("GP")}</th>
              <th className="text-right font-medium">{t("Pass")}</th><th className="text-right font-medium">{t("Rush")}</th><th className="text-right font-medium">{t("Rec")}</th><th className="text-right font-medium">{t("TD")}</th></tr></thead>
            <tbody>
              {d.leaders.map((l) => (
                <tr key={l.player_id} className="border-t border-line">
                  <td className="py-1.5"><Link href={`/nfl/players/${l.player_id}`} className="hover:underline">{l.full_name}</Link> <span className="text-[11px] text-muted">{l.position}</span></td>
                  <td className="text-right">{l.games}</td>
                  <td className="text-right">{l.passing_yards || "—"}</td>
                  <td className="text-right">{l.rushing_yards || "—"}</td>
                  <td className="text-right">{l.receiving_yards || "—"}</td>
                  <td className="text-right">{(l.passing_tds ?? 0) + (l.scrimmage_tds ?? 0) || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-[11px] text-muted">{t("Yards this season. TD = passing + rushing + receiving touchdowns.")}</p>
        </Card>
      </div>
    </div>
  );
}

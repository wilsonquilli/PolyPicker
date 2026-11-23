import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { StatBars } from "@/components/charts";
import { Breadcrumbs, Card, SectionTitle, Skeleton, StatTile, TeamBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { getLang, getT } from "@/lib/i18n/server";

type Params = Promise<{ id: string }>;
type Row = { game_id: number; date: string; week: number; game_type: string; team_id: number; is_home: boolean; opponent: string;
  participation: string; status_comment: string | null; offense_snaps: number | null; offense_pct: number | null; completions: number | null;
  pass_attempts: number | null; passing_yards: number | null; passing_tds: number | null; interceptions: number | null; carries: number | null;
  rushing_yards: number | null; rushing_tds: number | null; targets: number | null; receptions: number | null; receiving_yards: number | null;
  receiving_tds: number | null; scrimmage_tds: number | null };
interface PlayerDetail {
  player: { player_id: string; full_name: string; position: string | null; jersey: string | null; college: string | null;
    height_inches: number | null; weight_lbs: number | null };
  current_team: { team_id: number; abbreviation: string; full_name: string } | null;
  season: number;
  games_played: number;
  season_averages: Record<string, number | null>;
  game_log: Row[];
}

export default function NflPlayerPage({ params }: { params: Params }) {
  return (
    <Suspense fallback={<Skeleton className="h-[600px]" />}>
      <Player params={params} />
    </Suspense>
  );
}

async function Player({ params }: { params: Params }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const { id } = await params;
  if (!/^[\w-]{4,16}$/.test(id)) notFound();
  const res = await api<PlayerDetail>(`/nfl/players/${id}`);
  if (!res) notFound();
  const d = res.data;
  const pos = d.player.position ?? "";
  const a = d.season_averages;
  const primary = pos === "QB" ? "passing_yards" : pos === "RB" ? "rushing_yards" : "receiving_yards";
  const primaryLabel = { passing_yards: "Passing yards", rushing_yards: "Rushing yards", receiving_yards: "Receiving yards" }[primary];
  const chrono = [...d.game_log].reverse();
  const tiles: [string, string | number | null | undefined][] = pos === "QB"
    ? [["Pass yds / g", a.passing_yards], ["Pass TD / g", a.passing_tds], ["Attempts / g", a.pass_attempts], ["Rush yds / g", a.rushing_yards]]
    : pos === "RB" ? [["Rush yds / g", a.rushing_yards], ["Carries / g", a.carries], ["Rec yds / g", a.receiving_yards], ["TD / g", a.scrimmage_tds]]
      : [["Rec yds / g", a.receiving_yards], ["Receptions / g", a.receptions], ["Targets / g", a.targets], ["TD / g", a.scrimmage_tds]];
  const ht = d.player.height_inches ? `${Math.floor(d.player.height_inches / 12)}'${d.player.height_inches % 12}"` : null;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Breadcrumbs items={[{ href: "/nfl/teams", label: "NFL teams" },
          ...(d.current_team ? [{ href: `/nfl/teams/${d.current_team.abbreviation}`, label: d.current_team.full_name }] : []),
          { label: d.player.full_name }]} />
        <div className="flex items-center gap-4">
          {d.current_team && <TeamBadge abbr={d.current_team.abbreviation} size={56} league="nfl" />}
          <div>
            <h1 className="text-3xl font-bold tracking-tight">{d.player.full_name}</h1>
            <p className="text-sm text-muted">{[pos, d.player.jersey && `#${d.player.jersey}`, ht, d.player.weight_lbs && `${d.player.weight_lbs} lb`, d.player.college].filter(Boolean).join(" · ")}</p>
          </div>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <StatTile label={t("Games played")} value={d.games_played} sub={t("{s} season", { s: d.season })} />
        {tiles.map(([l, v]) => <StatTile key={l} label={t(l)} value={v ?? "—"} />)}
      </div>
      {chrono.some((r) => r.participation === "played") && (
        <Card className="p-5">
          <SectionTitle>{t("{stat} by game", { stat: t(primaryLabel) })}</SectionTitle>
          <StatBars values={chrono.map((r) => (r.participation === "played" ? (r[primary as keyof Row] as number | null) : null))}
            labels={chrono.map((r) => t("Wk {n}", { n: r.week }))} average={a[primary] ?? null} />
        </Card>
      )}
      <Card className="overflow-x-auto">
        <table className="tabular w-full text-sm">
          <thead>
            <tr className="border-b border-line text-[11px] uppercase tracking-wide text-muted">
              <th className="py-2 pl-5 pr-3 text-left font-semibold">{t("Game")}</th><th className="px-2 text-right font-semibold">{t("Snap%")}</th>
              <th className="px-2 text-right font-semibold">{t("Pass")}</th><th className="px-2 text-right font-semibold">{t("Rush")}</th>
              <th className="px-2 text-right font-semibold">{t("Rec")}</th><th className="py-2 pl-2 pr-5 text-right font-semibold">{t("TD")}</th>
            </tr>
          </thead>
          <tbody>
            {d.game_log.map((r) => (
              <tr key={r.game_id} className="border-t border-line">
                <td className="py-2 pl-5 pr-3"><Link href={`/nfl/games/${r.game_id}`} className="hover:underline">{t("Wk {n}", { n: r.week })} · {shortDate(r.date, lang)}</Link>
                  <span className="ml-1 text-muted">{r.is_home ? t("vs") : "@"} {r.opponent}</span></td>
                {r.participation !== "played" ? (
                  <td colSpan={5} className="py-2 pl-2 pr-5 text-right text-muted">{t(r.status_comment ?? "Did not play")}</td>
                ) : (
                  <>
                    <td className="px-2 text-right">{r.offense_pct !== null ? `${Math.round(r.offense_pct * 100)}%` : "—"}</td>
                    <td className="px-2 text-right">{r.pass_attempts ? `${r.completions}/${r.pass_attempts}, ${r.passing_yards}` : "—"}</td>
                    <td className="px-2 text-right">{r.carries ? `${r.carries}-${r.rushing_yards}` : "—"}</td>
                    <td className="px-2 text-right">{r.targets ? `${r.receptions}/${r.targets}, ${r.receiving_yards}` : "—"}</td>
                    <td className="py-2 pl-2 pr-5 text-right">{(r.passing_tds ?? 0) + (r.scrimmage_tds ?? 0) || "—"}</td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        <p className="px-5 py-3 text-[11px] text-muted">{t("Pass: completions/attempts, yards. Rush: carries-yards. Rec: receptions/targets, yards. Games not played show their status, never zeros.")}</p>
      </Card>
    </div>
  );
}

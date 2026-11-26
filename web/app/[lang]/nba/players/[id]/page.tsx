import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { StatBars } from "@/components/charts";
import { Breadcrumbs, Card, Pill, SectionTitle, Skeleton, StatTile } from "@/components/ui";
import { api } from "@/lib/api";
import { num, shortDate } from "@/lib/format";
import { teamColor } from "@/lib/teams";
import type { GameLogRow, PlayerAgg } from "@/lib/types";
import { keyed } from "@/lib/i18n";
import { getLang, getT } from "@/lib/i18n/server";

interface PlayerDetail {
  player: { player_id: number; full_name: string; position: string | null; height_inches: number | null; weight_lbs: number | null;
    birth_date: string | null; country: string | null; school: string | null; draft_year: number | null; draft_round: number | null;
    draft_number: number | null; jersey: string | null };
  current_team: { team_id: number; abbreviation: string; full_name: string } | null;
  season_label: string;
  summary: { last5: PlayerAgg; last10: PlayerAgg; season: PlayerAgg };
  seasons: (PlayerAgg & { season: number; season_label: string })[];
}

type Params = Promise<{ id: string }>;

export default function PlayerPage({ params }: { params: Params }) {
  return (
    <Suspense fallback={<div className="flex flex-col gap-6"><Skeleton className="h-20 w-80" /><Skeleton className="h-28" /><Skeleton className="h-96" /></div>}>
      <Player params={params} />
    </Suspense>
  );
}

async function Player({ params }: { params: Params }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const { id } = await params;
  if (!/^\d+$/.test(id)) notFound();
  const [res, logRes] = await Promise.all([
    api<PlayerDetail>(`/players/${id}`),
    api<{ games: GameLogRow[] }>(`/players/${id}/gamelog`, { limit: 82 }),
  ]);
  if (!res) notFound();
  const d = res.data;
  const games = logRes?.data.games ?? [];
  const chrono = [...games].reverse().slice(-25);
  const ht = d.player.height_inches ? `${Math.floor(d.player.height_inches / 12)}'${d.player.height_inches % 12}"` : null;
  const abbr = d.current_team?.abbreviation ?? "";

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Breadcrumbs items={[{ href: "/nba/teams", label: "Teams" }, ...(d.current_team ? [{ href: `/nba/teams/${abbr}`, label: d.current_team.full_name }] : []), { label: d.player.full_name }]} />
        <div className="flex flex-wrap items-center gap-4">
          <span className="inline-flex h-16 w-16 items-center justify-center rounded-full text-xl font-bold text-white" style={{ background: teamColor(abbr) }}>
            {d.player.full_name.split(" ").map((s) => s[0]).slice(0, 2).join("")}
          </span>
          <div>
            <h1 className="text-3xl font-bold tracking-tight">{d.player.full_name}</h1>
            <p className="text-sm text-muted">
              {[d.current_team?.full_name, d.player.position, d.player.jersey && `#${d.player.jersey}`, ht, d.player.weight_lbs && `${d.player.weight_lbs} lb`]
                .filter(Boolean).join(" · ")}
            </p>
          </div>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        {(["last5", "last10", "season"] as const).map((k) => {
          const a = d.summary[k];
          return (
            <Card key={k} className="p-4">
              <div className="mb-2 flex items-center justify-between text-xs font-semibold uppercase tracking-wide text-muted">
                <span>{k === "season" ? d.season_label : k === "last5" ? t("Last 5") : t("Last 10")}</span>
                <span className="normal-case tracking-normal">{a.games_played} GP</span>
              </div>
              <div className="grid grid-cols-4 gap-2">
                <StatTile label={t("PTS")} value={num(a.points)} />
                <StatTile label={t("AST")} value={num(a.assists)} />
                <StatTile label={t("REB")} value={num(a.rebounds)} />
                <StatTile label={t("MIN")} value={num(a.minutes)} />
              </div>
            </Card>
          );
        })}
      </div>

      {chrono.length > 0 && (
        <Card className="p-5">
          <SectionTitle>Points by game · last {chrono.length}</SectionTitle>
          <StatBars values={chrono.map((g) => (g.participation === "played" ? g.points : null))}
            labels={chrono.map((g) => `${shortDate(g.date, lang)} ${g.is_home ? t("vs") : "@"} ${g.opponent}`)} average={d.summary.season.points} />
        </Card>
      )}

      <Card className="overflow-x-auto">
        <div className="px-5 pt-4"><SectionTitle>Game log · {d.season_label}</SectionTitle></div>
        <table className="tabular w-full whitespace-nowrap text-sm">
          <thead>
            <tr className="text-[11px] uppercase tracking-wide text-muted">
              {["Date", "Opp", "Result", "MIN", "PTS", "AST", "REB", "FG", "3P", "FT", "+/-"].map((h) => (
                <th key={h} className={`px-3 py-2 font-semibold ${h === "Date" || h === "Opp" ? "text-left" : "text-right"} first:pl-5`}>{keyed(t, `col:${h}`, h)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {games.map((g) => (
              <tr key={g.game_id} className="border-t border-line">
                <td className="py-2 pl-5 pr-3"><Link href={`/nba/games/${g.game_id}`} className="hover:underline">{shortDate(g.date, lang)}</Link></td>
                <td className="px-3">{g.is_home ? "vs" : "@"} {g.opponent}</td>
                <td className={`px-3 text-right font-semibold ${g.result === "W" ? "text-win" : "text-loss"}`}>{g.result}</td>
                {g.participation === "played" ? (
                  <>
                    <td className="px-3 text-right">{num(g.minutes, 0)}</td>
                    <td className="px-3 text-right font-semibold">{g.points}</td>
                    <td className="px-3 text-right">{g.assists}</td>
                    <td className="px-3 text-right">{g.rebounds}</td>
                    <td className="px-3 text-right text-muted">{g.fgm}-{g.fga}</td>
                    <td className="px-3 text-right text-muted">{g.fg3m}-{g.fg3a}</td>
                    <td className="px-3 text-right text-muted">{g.ftm}-{g.fta}</td>
                    <td className="px-3 pr-5 text-right">{g.plus_minus !== null && g.plus_minus > 0 ? `+${g.plus_minus}` : g.plus_minus}</td>
                  </>
                ) : (
                  <td colSpan={8} className="px-3 pr-5 text-right">
                    <Pill tone={g.participation === "inactive" ? "loss" : "neutral"}>{g.status_comment ? t(g.status_comment) : g.participation === "unknown" ? t("No minutes recorded") : keyed(t, `part:${g.participation}`, g.participation.toUpperCase())}</Pill>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        {games.length === 0 && <p className="px-5 pb-5 text-sm text-muted">{t("No games this season.")}</p>}
      </Card>

      {d.seasons.length > 0 && (
        <Card className="overflow-x-auto">
          <div className="px-5 pt-4"><SectionTitle>{t("By season")}</SectionTitle></div>
          <table className="tabular w-full text-sm">
            <thead><tr className="text-[11px] uppercase tracking-wide text-muted">{["Season", "GP", "MIN", "PTS", "AST", "REB", "FG%", "3P%"].map((h) => <th key={h} className="px-3 py-2 text-right font-semibold first:pl-5 first:text-left">{keyed(t, `col:${h}`, h)}</th>)}</tr></thead>
            <tbody>
              {d.seasons.map((s) => (
                <tr key={s.season} className="border-t border-line">
                  <td className="py-2 pl-5">{s.season_label}</td>
                  <td className="px-3 text-right">{s.games_played}</td><td className="px-3 text-right">{num(s.minutes)}</td>
                  <td className="px-3 text-right font-semibold">{num(s.points)}</td><td className="px-3 text-right">{num(s.assists)}</td>
                  <td className="px-3 text-right">{num(s.rebounds)}</td>
                  <td className="px-3 text-right">{s.fg_pct !== null ? (Number(s.fg_pct) * 100).toFixed(1) : "—"}</td>
                  <td className="px-3 pr-5 text-right">{s.fg3_pct !== null ? (Number(s.fg3_pct) * 100).toFixed(1) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
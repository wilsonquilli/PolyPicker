import Link from "next/link";
import { notFound } from "next/navigation";
import { Fragment, Suspense } from "react";

import { SoccerBadge } from "@/components/soccer/MatchCard";
import { Card, FormChips, SectionTitle, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { soccerLeague, type KnockoutTie, type SoccerLeague, type SoccerTeam, type Standings, type StandingsTable } from "@/lib/soccer";
import { keyed } from "@/lib/i18n";
import { getLang, getT } from "@/lib/i18n/server";

type LParams = Promise<{ league: string }>;
type SP = Promise<{ [k: string]: string | string[] | undefined }>;

export async function generateMetadata({ params }: { params: LParams }) {
  const lg = soccerLeague((await params).league);
  const t = await getT();
  return lg ? { title: t(lg.kind === "cup" ? "{league} standings" : "{league} table", { league: lg.name }) } : {};
}

export default function TablePage({ params, searchParams }: { params: LParams; searchParams: SP }) {
  return (
    <div className="flex flex-col gap-6">
      <Suspense fallback={<Skeleton className="h-[700px]" />}><Tables params={params} searchParams={searchParams} /></Suspense>
    </div>
  );
}

// Colored edge per table position: qualification / relegation places differ by competition and stage.
type Zone = { from: number; to: number; cls: string; label: string };
function zonesFor(lg: SoccerLeague, t: StandingsTable, n: number): Zone[] {
  const win = "border-l-win", mid = "border-l-accent", loss = "border-l-loss";
  if (t.name === "League Phase" || t.stage === "league-phase") {
    return [{ from: 1, to: 8, cls: win, label: "Round of 16" }, { from: 9, to: 24, cls: mid, label: "Knockout playoffs" }];
  }
  if (t.name.startsWith("Group ") && lg.kind === "cup") {
    return lg.slug === "sudamericana" || lg.slug === "uel"
      ? [{ from: 1, to: 1, cls: win, label: "Next round" }, { from: 2, to: 2, cls: mid, label: "Playoff round" }]
      : [{ from: 1, to: 2, cls: win, label: "Next round" }];
  }
  const top: Record<string, [number, number]> = { epl: [4, 3], laliga: [4, 3], seriea: [4, 3], bundesliga: [4, 2], ligue1: [3, 2] };
  const z = top[lg.slug];
  if (!z || t.name === "Overall") return [];
  return [{ from: 1, to: z[0], cls: win, label: "Champions League" }, { from: n - z[1] + 1, to: n, cls: loss, label: "Relegation" }];
}

async function Tables({ params, searchParams }: { params: LParams; searchParams: SP }) {
  const t = await getT();
  const lg = soccerLeague((await params).league);
  if (!lg) notFound();
  const sp = await searchParams;
  const season = typeof sp.season === "string" && /^\d{4}$/.test(sp.season) ? sp.season : undefined;
  const res = await api<Standings>(`${lg.api}/standings`, season ? { season } : {});
  const d = res?.data ?? { tables: [], knockout: [] };
  const label = String(res?.meta.extra?.season_label ?? "");
  const seasons = (res?.meta.extra?.seasons ?? []) as { season: number; label: string }[];
  const current = Number(res?.meta.extra?.season);
  const teams = new Map<number, SoccerTeam>();
  for (const k of d.knockout) for (const tie of k.ties) { teams.set(tie.team_a.team_id, tie.team_a); teams.set(tie.team_b.team_id, tie.team_b); }
  const cup = lg.kind === "cup";
  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{t(cup ? "{league} standings" : "{league} table", { league: lg.name })}</h1>
          <p className="text-sm text-muted">{label} · {t("3 points for a win, 1 for a draw. Ties broken by goal difference, then goals scored.")}</p>
        </div>
        {seasons.length > 1 && (
          <nav aria-label={t("Season")} className="no-scrollbar flex max-w-full gap-1 overflow-x-auto text-sm">
            {seasons.slice(0, 6).map((s) => (
              <Link key={s.season} href={`${lg.base}/table?season=${s.season}`} aria-current={s.season === current ? "page" : undefined}
                className={`whitespace-nowrap rounded-lg px-2.5 py-1 ${s.season === current ? "bg-surface-2 font-semibold text-ink" : "text-muted hover:text-ink"}`}>{s.label}</Link>
            ))}
          </nav>
        )}
      </div>

      {d.tables.length === 0 && d.knockout.length === 0 && <Card className="p-6 text-sm text-muted">{t("No matches stored for this season yet.")}</Card>}

      {/* Knockout rounds first once they've started: they're what's live in a cup. */}
      {cup && d.knockout.length > 0 && <Knockout lg={lg} rounds={[...d.knockout].reverse()} teams={teams} />}

      <div className={d.tables.length > 2 ? "grid gap-6 lg:grid-cols-2" : "flex flex-col gap-6"}>
        {d.tables.map((tm) => <TableCard key={`${tm.stage}-${tm.name}`} lg={lg} tb={tm} compact={d.tables.length > 2} title={d.tables.length > 1 ? tm.name : null} />)}
      </div>

      {!cup && d.knockout.length > 0 && <Knockout lg={lg} rounds={[...d.knockout].reverse()} teams={teams} />}
    </>
  );
}

async function TableCard({ lg, tb, compact, title }: { lg: SoccerLeague; tb: StandingsTable; compact: boolean; title: string | null }) {
  const t = await getT();
  const grouped = tb.rows.some((r) => r.group);
  const zones = grouped ? [] : zonesFor(lg, tb, tb.rows.length);
  const cols = compact ? 8 : 11;
  const zoneOf = (p: number) => zones.find((z) => p >= z.from && p <= z.to)?.cls ?? "border-l-transparent";
  return (
    <section>
      {title && <SectionTitle>{t(title)}{tb.name !== tb.stage_label && tb.stage_label !== "Table" && <span className="ml-2 text-sm font-normal text-muted">{t(tb.stage_label)}</span>}</SectionTitle>}
      {!title && tb.stage_label !== "Table" && <p className="mb-2 text-xs text-muted">{t(tb.stage_label)}</p>}
      <Card className="overflow-x-auto">
        <table className="tabular w-full text-sm">
          <thead>
            <tr className="border-b border-line text-[11px] uppercase tracking-wide text-muted">
              <th className="w-10 py-2 pl-4 text-left font-semibold">#</th><th className="py-2 text-left font-semibold">{t("Club")}</th>
              <th className="px-2 text-right font-semibold">{keyed(t, "col:P", "P")}</th><th className="px-2 text-right font-semibold">{t("W")}</th>
              <th className="px-2 text-right font-semibold">{keyed(t, "col:D", "D")}</th><th className="px-2 text-right font-semibold">{t("L")}</th>
              {!compact && <><th className="hidden px-2 text-right font-semibold sm:table-cell">{t("GF")}</th><th className="hidden px-2 text-right font-semibold sm:table-cell">{t("GA")}</th></>}
              <th className="px-2 text-right font-semibold">{t("GD")}</th><th className="px-2 text-right font-semibold">{t("Pts")}</th>
              {!compact && <th className="hidden py-2 pl-3 pr-4 text-left font-semibold md:table-cell">{t("Form")}</th>}
            </tr>
          </thead>
          <tbody>
            {tb.rows.map((r, i) => (
              <Fragment key={r.team_id}>
              {grouped && r.group !== tb.rows[i - 1]?.group && (
                <tr className="border-t border-line bg-surface-2/60">
                  <td colSpan={cols} className="px-4 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">{t(r.group ?? "")}</td>
                </tr>
              )}
              <tr className={`border-l-4 border-t border-line ${zoneOf(r.position)}`}>
                <td className="py-2 pl-3 text-muted">{r.position}</td>
                <td className="py-2"><Link href={`${lg.base}/teams/${r.team_id}`} className="flex items-center gap-2.5 hover:underline">
                  <SoccerBadge team={r} size={24} /><span className="truncate font-medium">{r.short_name ?? r.name}</span></Link></td>
                <td className="px-2 text-right">{r.played}</td><td className="px-2 text-right">{r.won}</td>
                <td className="px-2 text-right">{r.drawn}</td><td className="px-2 text-right">{r.lost}</td>
                {!compact && <><td className="hidden px-2 text-right sm:table-cell">{r.gf}</td><td className="hidden px-2 text-right sm:table-cell">{r.ga}</td></>}
                <td className={`px-2 text-right ${r.gd > 0 ? "text-win" : r.gd < 0 ? "text-loss" : ""}`}>{r.gd > 0 ? "+" : ""}{r.gd}</td>
                <td className="px-2 pr-3 text-right font-bold">{r.points}</td>
                {!compact && <td className="hidden py-2 pl-3 pr-4 md:table-cell"><FormChips results={[...r.form].reverse().map((x) => ({ result: x, opponent: "", home: true }))} /></td>}
              </tr>
              </Fragment>
            ))}
          </tbody>
        </table>
        {zones.length > 0 && (
          <p className="px-4 py-3 text-[11px] text-muted">
            {zones.map((z, i) => <span key={z.label}>{i > 0 && " · "}<span className={z.cls.replace("border-l-", "text-")}>■</span> {t(z.label)}</span>)}
            {!compact && ` · ${t("Form: most recent on the right.")}`}
          </p>
        )}
      </Card>
    </section>
  );
}

async function Knockout({ lg, rounds, teams }: { lg: SoccerLeague; rounds: Standings["knockout"]; teams: Map<number, SoccerTeam> }) {
  const t = await getT();
  return (
    <section className="flex flex-col gap-5">
      {rounds.map((r) => (
        <div key={r.stage}>
          <SectionTitle>{t(r.label)}</SectionTitle>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {r.ties.map((tm) => <Tie key={`${tm.team_a.team_id}-${tm.team_b.team_id}`} lg={lg} tie={tm} teams={teams} />)}
          </div>
        </div>
      ))}
    </section>
  );
}

async function Tie({ lg, tie, teams }: { lg: SoccerLeague; tie: KnockoutTie; teams: Map<number, SoccerTeam> }) {
  const t = await getT();
  const lang = await getLang();
  const twoLegs = tie.legs.length > 1;
  const side = (team: SoccerTeam, agg: number) => {
    const won = tie.winner_id === team.team_id, lost = tie.winner_id !== null && !won;
    return (
      <div className="flex items-center gap-2.5">
        <SoccerBadge team={team} size={24} />
        <Link href={`${lg.base}/teams/${team.team_id}`} className={`min-w-0 flex-1 truncate text-sm hover:underline ${won ? "font-semibold" : lost ? "text-muted" : "font-medium"}`}>
          {team.short_name ?? team.name}
        </Link>
        {tie.legs.some((l) => l.status === "final") && <span className={`tabular text-base ${won ? "font-bold" : "text-muted"}`}>{agg}</span>}
      </div>
    );
  };
  return (
    <Card className="flex flex-col gap-2 p-4">
      {side(tie.team_a, tie.aggregate.a)}
      {side(tie.team_b, tie.aggregate.b)}
      <ul className="mt-1 flex flex-col gap-1 border-t border-line pt-2 text-xs text-muted">
        {tie.legs.map((l) => {
          const h = teams.get(l.home_team_id), a = teams.get(l.away_team_id);
          const extra = l.decided === "pen" && l.shootout ? ` (${t("{h}-{a} pens", { h: l.shootout.home, a: l.shootout.away })})` : l.decided ? ` (${t("AET")})` : "";
          return (
            <li key={l.game_id}>
              <Link href={`${lg.base}/games/${l.game_id}`} className="flex justify-between gap-2 hover:text-ink">
                <span>{twoLegs && l.leg ? `${t("Leg {n}", { n: l.leg })} · ` : ""}{shortDate(l.date, lang)}</span>
                <span className="tabular truncate">
                  {h?.abbreviation} {l.status === "final" ? `${l.home_score}–${l.away_score}` : "v"} {a?.abbreviation}{extra}
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
      {tie.note && tie.complete && <p className="text-[11px] text-faint">{t(tie.note.replace(/^2nd Leg - /, ""))}</p>}
    </Card>
  );
}

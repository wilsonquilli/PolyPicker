import Link from "next/link";

import { LeagueFlag } from "@/components/LeagueFlag";
import { TeamBadge } from "@/components/ui";
import { api } from "@/lib/api";
import type { T } from "@/lib/i18n";
import { getLang, getT } from "@/lib/i18n/server";
import { LEAGUES, type LeagueKey } from "@/lib/home";
import { SOCCER_LEAGUES, teamCss, type LeagueSlug } from "@/lib/soccer";

/** Championship forecasts from `GET /futures` (built by `polypicker futures`). */
interface FutureTeam {
  team_id: number; name: string; short: string; abbr: string; color?: string | null;
  p_title: number;
}
interface Future {
  competition: LeagueKey; sport: "nba" | "nfl" | "soccer"; season: number; simulations: number; generated_at: string;
  title: string; event: string; season_label: string; status: "simulated" | "clinched" | "decided" | "final_scheduled";
  champion_id?: number | null; remaining_games: number; teams: FutureTeam[]; reasons: string[]; method: string;
  reasons_es?: string[]; method_es?: string; title_es?: string; event_es?: string;
}

const pct = (p: number) => (p > 0 && p < 0.005 ? "<1%" : p > 0.995 && p < 1 ? ">99%" : `${Math.round(p * 100)}%`);

function Badge({ f, t, size }: { f: Future; t: FutureTeam; size: number }) {
  return f.sport === "soccer"
    ? <TeamBadge abbr={t.abbr} size={size} league="soccer" color={teamCss({ color: t.color ?? null })} logoId={t.team_id} />
    : <TeamBadge abbr={t.abbr} size={size} league={f.sport} />;
}

function teamHref(f: Future, t: FutureTeam): string {
  if (f.sport === "nba") return `/nba/teams/${t.abbr}`;
  if (f.sport === "nfl") return `/nfl/teams/${t.abbr}`;
  return `/soccer/${f.competition}/teams/${t.team_id}`;
}

const settled = (f: Future) => f.status === "decided" || f.status === "clinched";

/** Server component: renders nothing if the forecasts aren't available (the rest of the home page still loads). */
export async function Futures() {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  let data: Future[] = [];
  try {
    data = (await api<Future[]>("/futures"))?.data ?? [];
  } catch {
    return null;
  }
  if (!data.length) return null;
  if (lang === "es") {   // the backend writes each pick's reasons in both languages
    data = data.map((f) => ({ ...f, reasons: f.reasons_es ?? f.reasons, method: f.method_es ?? f.method,
      title: f.title_es ?? f.title, event: f.event_es ?? f.event }));
  }
  const us = data.filter((f) => f.sport !== "soccer");
  const soccer = data.filter((f) => f.sport === "soccer");
  return (
    <section aria-labelledby="futures-h" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-accent">{t("Season forecasts")}</p>
          <h2 id="futures-h" className="text-xl font-bold tracking-tight sm:text-2xl">{t("Who wins it all?")}</h2>
          <p className="mt-0.5 max-w-2xl text-sm text-muted">
            {t("Our game models play out the rest of every season thousands of times. A team’s chance is the share of those seasons it wins.")}
          </p>
        </div>
      </div>

      {us.length > 0 && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {us.map((f) => <MajorCard key={f.competition} f={f} t={t} />)}
        </div>
      )}

      <SoccerRow t={t} title={t("Domestic leagues")} items={soccer.filter((f) => SOCCER_LEAGUES[f.competition as LeagueSlug]?.kind === "league")}
        cols="lg:grid-cols-3" />
      <SoccerRow t={t} title={t("Continental cups")} items={soccer.filter((f) => SOCCER_LEAGUES[f.competition as LeagueSlug]?.kind === "cup")}
        cols="lg:grid-cols-2 xl:grid-cols-4" />
    </section>
  );
}

function SoccerRow({ title, items, cols, t }: { title: string; items: Future[]; cols: string; t: T }) {
  if (!items.length) return null;
  return (
    <div className="flex flex-col gap-3">
      <h3 className="mt-2 flex items-center gap-2 text-sm font-semibold"><span aria-hidden>⚽</span> {title}</h3>
      {/* Phones: swipe through competitions; wider screens: a grid. */}
      <ul className={`no-scrollbar -mx-4 flex snap-x snap-mandatory gap-3 overflow-x-auto px-4 pb-1 sm:mx-0 sm:grid sm:grid-cols-2 sm:gap-4 sm:overflow-visible sm:px-0 ${cols}`}>
        {items.map((f) => (
          <li key={f.competition} className="w-[85%] shrink-0 snap-start sm:w-auto sm:min-w-0">
            <SoccerCard f={f} t={t} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function StatusPill({ f, t }: { f: Future; t: T }) {
  if (f.status === "decided") return <span className="rounded-full bg-win/15 px-2 py-0.5 text-[11px] font-bold text-win">🏆 {t("Champions")}</span>;
  if (f.status === "clinched") return <span className="rounded-full bg-win/15 px-2 py-0.5 text-[11px] font-bold text-win">🏆 {t("Title clinched")}</span>;
  return null;
}

/** Bars share one scale: the favourite's chance is a full bar, so a 10% side shows as a short one. */
function Contenders({ f, teams, max }: { f: Future; teams: FutureTeam[]; max: number }) {
  return (
    <ul className="flex flex-col gap-1.5">
      {teams.map((tm) => (
        <li key={tm.team_id}>
          <Link href={teamHref(f, tm)} className="flex items-center gap-2 text-sm hover:opacity-80">
            <Badge f={f} t={tm} size={20} />
            <span className="w-28 shrink-0 truncate sm:w-32">{tm.short}</span>
            <span className="relative h-2 flex-1 overflow-hidden rounded-full bg-surface-2">
              <span className="absolute inset-y-0 left-0 rounded-full bg-ink/25" style={{ width: `${(tm.p_title / max) * 100}%` }} />
            </span>
            <span className="tabular w-10 shrink-0 text-right text-xs font-semibold text-muted">{pct(tm.p_title)}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function Reasons({ items }: { items: string[] }) {
  return (
    <ul className="flex flex-col gap-1.5 text-sm">
      {items.map((r) => (
        <li key={r} className="flex gap-2">
          <span aria-hidden className="mt-[3px] inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full bg-accent/15 text-[9px] font-bold text-accent">✓</span>
          <span className="text-ink/90">{r}</span>
        </li>
      ))}
    </ul>
  );
}

/** NBA / NFL: the pick, every reason, and the next four contenders. */
function MajorCard({ f, t }: { f: Future; t: T }) {
  const [fav, ...rest] = f.teams;
  const league = LEAGUES[f.competition];
  return (
    <article className="flex min-w-0 flex-col gap-4 rounded-2xl border border-line bg-surface p-5 shadow-card sm:p-6">
      <header className="flex items-center justify-between gap-2 text-xs text-muted">
        <Link href={league.href} className="flex items-center gap-1.5 hover:text-ink">
          <span aria-hidden>{league.icon}</span><span className="font-semibold text-ink">{t(league.label)}</span><span>· {f.event}</span>
        </Link>
        <span>{f.season_label}</span>
      </header>
      <Link href={teamHref(f, fav)} className="group flex items-center gap-4 rounded-xl bg-accent-soft p-4">
        <Badge f={f} t={fav} size={64} />
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-accent">{t("Our pick")} · {f.title}</p>
          <p className="text-xl font-bold leading-tight tracking-tight group-hover:underline sm:text-2xl">{fav.name}</p>
        </div>
        <div className="shrink-0 text-right">
          <p className="tabular text-3xl font-bold text-accent sm:text-4xl">{pct(fav.p_title)}</p>
          <p className="text-[11px] text-muted">{t("to win it all")}</p>
        </div>
      </Link>
      <div>
        <h3 className="mb-2 text-sm font-semibold">{t("Why {team}?", { team: fav.short })}</h3>
        <Reasons items={f.reasons} />
      </div>
      <div className="mt-auto border-t border-line pt-4">
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">{t("Next contenders")}</h3>
        <Contenders f={f} teams={rest.slice(0, 4)} max={Math.max(fav.p_title, 0.01)} />
      </div>
    </article>
  );
}

/** Soccer competitions: compact; reasons beyond the first three and the contenders sit behind a disclosure. */
function SoccerCard({ f, t }: { f: Future; t: T }) {
  const lg = SOCCER_LEAGUES[f.competition as LeagueSlug];
  const [fav, ...rest] = f.teams;
  const done = settled(f);
  const contenders = rest.filter((t) => t.p_title > 0).slice(0, 3);
  const more = f.reasons.slice(3);
  return (
    <article className="flex h-full min-w-0 flex-col gap-3 rounded-2xl border border-line bg-surface p-4 shadow-card">
      <header className="flex items-center justify-between gap-2 text-xs text-muted">
        <Link href={`/soccer/${f.competition}`} className="flex min-w-0 items-center gap-1.5 hover:text-ink">
          <LeagueFlag slug={f.competition as LeagueSlug} /><span className="truncate font-semibold text-ink">{lg?.name ?? f.event}</span>
        </Link>
        <span className="shrink-0">{f.season_label}</span>
      </header>
      <Link href={teamHref(f, fav)} className={`group flex items-center gap-3 rounded-xl p-3 ${done ? "bg-win/10" : "bg-accent-soft"}`}>
        <Badge f={f} t={fav} size={44} />
        <div className="min-w-0 flex-1">
          <p className={`text-[10px] font-semibold uppercase tracking-wider ${done ? "text-win" : "text-accent"}`}>
            {t(f.status === "decided" ? "Winner" : f.status === "clinched" ? "Champion" : f.status === "final_scheduled" ? "Our pick for the final" : "Our pick")}
          </p>
          <p className="font-bold leading-tight group-hover:underline">{fav.name}</p>
        </div>
        {done ? <StatusPill f={f} t={t} /> : <span className="tabular shrink-0 text-2xl font-bold text-accent">{pct(fav.p_title)}</span>}
      </Link>
      <Reasons items={f.reasons.slice(0, 3)} />
      {!done && contenders.length > 0 && (
        <div className="border-t border-line pt-3">
          <Contenders f={f} teams={contenders} max={Math.max(fav.p_title, 0.01)} />
        </div>
      )}
      {more.length > 0 && (
        <details className="group mt-auto text-sm">
          <summary className="cursor-pointer list-none text-xs font-semibold text-accent hover:underline [&::-webkit-details-marker]:hidden">
            <span className="group-open:hidden">{t("More on why →")}</span><span className="hidden group-open:inline">{t("Show less")}</span>
          </summary>
          <div className="mt-2"><Reasons items={more} /></div>
        </details>
      )}
    </article>
  );
}

import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";
import { AutoRefresh, Carousel, UpdatedAt } from "@/components/client";
import { ComboGrader, PickButton } from "@/components/ComboGrader";
import { Combos } from "@/components/Combos";
import { Futures } from "@/components/Futures";
import { TeamBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { addDays } from "@/lib/format";
import { fromNba, fromNfl, fromSoccer, isSoccer, LEAGUES, legKey, nowMs, todayET, whenLabel, type LeagueKey, type Market, type MarketSide } from "@/lib/home";
import type { NflGame } from "@/lib/nfl";
import { LEAGUE_SLUGS, SOCCER_LEAGUES, type SoccerGame } from "@/lib/soccer";
import type { Lang, T } from "@/lib/i18n";
import { ReasonLine } from "@/components/Reasons";
import { getLang, getT } from "@/lib/i18n/server";
import type { GameSummary } from "@/lib/types";

export async function generateMetadata() {
  const t = await getT();
  return { description: t("Live games and model forecasts across the NBA, NFL and 10 soccer competitions.") };
}

type SP = Promise<{ [k: string]: string | string[] | undefined }>;
type SportFilter = "all" | LeagueKey;
type Sort = "soon" | "close" | "favorites";

// Soccer: 2.25 h, a little more in cups (extra time and penalties).
const GAME_HOURS = (k: LeagueKey) => k === "nba" ? 3 : k === "nfl" ? 4 : SOCCER_LEAGUES[k].kind === "cup" ? 2.75 : 2.25;

const SORTS: { key: Sort; label: string }[] = [
  { key: "soon", label: "Starting soon" },
  { key: "close", label: "Toss-ups" },
  { key: "favorites", label: "Biggest favorites" },
];

export default function Home({ searchParams }: { searchParams: SP }) {
  return (
    <Suspense fallback={<HomeSkeleton />}>
      <Feed searchParams={searchParams} />
    </Suspense>
  );
}

/** One league failing (backend down for it, empty season) must not take the whole home page down. */
async function safe<T>(p: Promise<{ data: T } | null>): Promise<T | null> {
  try {
    return (await p)?.data ?? null;
  } catch {
    return null;
  }
}

async function Feed({ searchParams }: { searchParams: SP }) {
  const sp = await searchParams;
  const [t, lang] = await Promise.all([getT(), getLang()]);
  await connection(); // "today" and live scores are per-request
  const sport: SportFilter = typeof sp.sport === "string" && sp.sport in LEAGUES ? (sp.sport as LeagueKey) : "all";
  const sort: Sort = sp.sort === "close" || sp.sort === "favorites" ? sp.sort : "soon";
  const today = todayET();
  const tomorrow = addDays(today, 1);
  const from = addDays(today, -1);

  const [nba, nfl, soccer, pm] = await Promise.all([
    safe(api<GameSummary[]>("/games", { date_from: from, date_to: addDays(today, 4), limit: 300 })),
    safe(api<NflGame[]>("/nfl/games", { date_from: from, date_to: addDays(today, 8), limit: 300 })),
    Promise.all(LEAGUE_SLUGS.map(async (slug) => [slug, await safe(api<SoccerGame[]>(`/soccer/${slug}/games`, { date_from: from, date_to: addDays(today, 8), limit: 300 }))] as const)),
    safe(api<{ game_id: number; p_home: number; url: string; volume_usd: number | null }[]>("/markets/polymarket", { date_from: from, date_to: addDays(today, 4) })),
  ]);
  const pmBy = new Map((pm ?? []).map((r) => [r.game_id, r]));
  const all: Market[] = [...(nba ?? []).map((g) => ({ ...fromNba(g, t), polymarket: pmBy.get(g.game_id) ?? null })), ...(nfl ?? []).map((g) => fromNfl(g, t)), ...soccer.flatMap(([, g]) => (g ?? []).map((x) => fromSoccer(x, t)))]
    .sort((a, b) => a.tipoff.localeCompare(b.tipoff));
  const unavailable = ([["nba", nba], ["nfl", nfl], ...soccer] as const).filter(([, d]) => d === null).map(([k]) => LEAGUES[k as LeagueKey].label);

  // A game past its start time that the feed still calls "scheduled" has almost certainly started and
  // the status just hasn't synced yet: show it with the live games (for a typical game length), never as upcoming.
  const now = nowMs();
  const started = (m: Market) => m.status === "scheduled" && Date.parse(m.tipoff) <= now;
  const live = all.filter((m) => m.status === "live" || (started(m) && now - Date.parse(m.tipoff) < GAME_HOURS(m.league) * 3_600_000));
  const upcomingAll = all.filter((m) => m.status === "scheduled" && !started(m) && m.pickP !== null);
  const recent = all.filter((m) => m.status === "final" && m.pick && m.result && m.date >= from).reverse().slice(0, 12);

  const counts = Object.fromEntries((Object.keys(LEAGUES) as LeagueKey[]).map((k) => [k, upcomingAll.filter((m) => m.league === k).length]));
  const upcoming = upcomingAll.filter((m) => sport === "all" || m.league === sport);
  const sorted = sort === "soon" ? upcoming
    : sort === "close" ? [...upcoming].sort((a, b) => a.pickP! - b.pickP!)
    : [...upcoming].sort((a, b) => b.pickP! - a.pickP!);

  // Polymarket-style hero: the closest contest starting in the next ~day, plus the strongest picks.
  const soon = upcomingAll.filter((m) => m.date <= tomorrow);
  const pool = soon.length ? soon : upcomingAll;
  const featured = [...pool].sort((a, b) => closeness(a) - closeness(b))[0];
  const strongest = [...pool].filter((m) => m !== featured).sort((a, b) => b.pickP! - a.pickP!).slice(0, 5);
  const when = (m: Market) => whenLabel(m.tipoff, m.date, today, tomorrow, lang);

  const href = (s: SportFilter, o: Sort) => {
    const q = new URLSearchParams();
    if (s !== "all") q.set("sport", s);
    if (o !== "soon") q.set("sort", o);
    const str = q.toString();
    return str ? `/?${str}` : "/";
  };

  const todayCount = all.filter((m) => m.date === today).length;
  const tomorrowCount = all.filter((m) => m.date === tomorrow).length;
  return (
    <div className="flex flex-col gap-8">
      <AutoRefresh seconds={live.length ? 30 : 120} />

      {/* At a glance: what's on, with a jump to each section of the page. */}
      <nav aria-label={t("On this page")} className="no-scrollbar -mx-4 -mb-3 flex gap-2 overflow-x-auto px-4 sm:mx-0 sm:px-0">
        {live.length > 0 && <Glance href="#live-h" tone="live" icon={<span className="h-2 w-2 animate-pulse rounded-full bg-loss" />}>{t(live.length === 1 ? "{n} live game" : "{n} live games", { n: live.length })}</Glance>}
        <Glance href="#markets-h" icon="📅">{t(todayCount === 1 ? "{n} game today" : "{n} games today", { n: todayCount })}</Glance>
        {tomorrowCount > 0 && <Glance href="#markets-h" icon="🗓️">{t("{n} tomorrow", { n: tomorrowCount })}</Glance>}
        <Glance href="#grader-h" icon="🧮">{t("Combo grader")}</Glance>
        <Glance href="#combos-h" icon="🎯">{t("Combos")}</Glance>
        <Glance href="#futures-h" icon="🏆">{t("Who wins it all?")}</Glance>
        {recent.length > 0 && <Glance href="#recent-h" icon="✅">{t("Just finished")}</Glance>}
      </nav>

      {live.length > 0 ? (
        <section aria-labelledby="live-h">
          <div className="mb-4 flex items-center gap-2.5">
            <span className="relative flex h-2.5 w-2.5" aria-hidden>
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-loss opacity-60" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-loss" />
            </span>
            <h1 id="live-h" className="text-2xl font-bold tracking-tight">{t("Live now")}</h1>
            <span className="tabular text-sm text-muted">{t(live.length === 1 ? "{n} game" : "{n} games", { n: live.length })}</span>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {live.map((m) => <LiveCard key={m.key} m={m} t={t} />)}
          </div>
        </section>
      ) : featured ? (
        <section className="grid gap-4 lg:grid-cols-3" aria-label="Featured">
          <h1 className="sr-only">{t("PolyPicker sports forecasts")}</h1>
          <Featured m={featured} when={when(featured)} t={t} />
          <Strongest markets={strongest} when={when} t={t} />
        </section>
      ) : null}

      {recent.length > 0 && (
        <section aria-labelledby="recent-h">
          <h2 id="recent-h" className="mb-3 text-sm font-semibold">{t("Just finished")}</h2>
          <Carousel label={t("Just finished")} className="-mx-1 px-1 pb-1">
            {recent.map((m) => <li key={m.key} className="shrink-0 snap-start"><ResultChip m={m} t={t} /></li>)}
          </Carousel>
        </section>
      )}

      <ComboGrader />

      <Suspense fallback={<FuturesSkeleton />}>
        <Combos />
      </Suspense>

      <Suspense fallback={<FuturesSkeleton />}>
        <Futures />
      </Suspense>

      <section aria-labelledby="markets-h">
        <div className="mb-4 flex flex-col gap-3">
          <h2 id="markets-h" className="text-xl font-bold tracking-tight">{live.length ? t("Coming up") : t("All forecasts")}</h2>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <nav aria-label={t("Filter by sport")} className="no-scrollbar -mx-1 flex gap-1.5 overflow-x-auto px-1">
              <Chip href={href("all", sort)} active={sport === "all"}>{t("All")} <Count n={upcomingAll.length} /></Chip>
              {(Object.keys(LEAGUES) as LeagueKey[]).filter((k) => k === "nba" || k === "nfl" || counts[k] > 0 || sport === k).map((k) => (
                <Chip key={k} href={href(k, sort)} active={sport === k}>
                  <span aria-hidden>{LEAGUES[k].icon}</span>{LEAGUES[k].label} <Count n={counts[k]} />
                </Chip>
              ))}
            </nav>
            <nav aria-label={t("Sort")} className="flex gap-1 self-start rounded-xl bg-surface-2 p-1 text-sm sm:self-auto">
              {SORTS.map((s) => (
                <Link key={s.key} href={href(sport, s.key)} scroll={false} aria-current={sort === s.key ? "page" : undefined}
                  className={`whitespace-nowrap rounded-lg px-3 py-1.5 font-medium ${sort === s.key ? "bg-surface text-ink shadow-card" : "text-muted"}`}>{t(s.label)}</Link>
              ))}
            </nav>
          </div>
        </div>
        {sorted.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-line px-6 py-12 text-center">
            <p className="font-semibold">{sport !== "all" ? t("No upcoming forecasts for {league}", { league: LEAGUES[sport].label }) : t("No upcoming forecasts")}</p>
            <p className="mt-1 text-sm text-muted">{t("Forecasts appear here once a game is on the schedule and the model has published.")}</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {sorted.map((m) => <MarketCard key={m.key} m={m} when={when(m)} t={t} lang={lang} />)}
          </div>
        )}
        {unavailable.length > 0 && <p className="mt-4 text-xs text-warn">{t("{leagues} data is unavailable right now.", { leagues: unavailable.join(", ") })}</p>}
      </section>
    </div>
  );
}

/** Lower = closer contest. Three-way soccer markets are compared on the same footing (gap between top two outcomes). */
function closeness(m: Market): number {
  const ps = [m.home.p ?? 0, m.away.p ?? 0, m.pDraw ?? 0].sort((a, b) => b - a);
  return ps[0] - ps[1];
}

const pct = (p: number) => `${Math.round(p * 100)}%`;

function Badge({ m, side, size }: { m: Market; side: MarketSide; size: number }) {
  return isSoccer(m)
    ? <TeamBadge abbr={side.abbr} size={size} league="soccer" color={side.color} logoId={side.team_id} />
    : <TeamBadge abbr={side.abbr} size={size} league={m.league as "nba" | "nfl"} />;
}

function LeagueTag({ m }: { m: Market }) {
  return (
    <span className="flex min-w-0 items-center gap-1.5 truncate">
      <span aria-hidden>{LEAGUES[m.league].icon}</span>
      <span className="font-semibold text-ink">{LEAGUES[m.league].label}</span>
      {m.context && <span className="truncate">· {m.context}</span>}
    </span>
  );
}

/** Outcome rows in display order: away @ home for US leagues, home v away (+ draw) for soccer. */
function outcomes(m: Market, t: T): { key: "home" | "away" | "draw"; label: string; p: number | null; side?: MarketSide }[] {
  if (isSoccer(m)) {
    return [
      { key: "home", label: m.home.short, p: m.home.p, side: m.home },
      { key: "draw", label: t("Draw"), p: m.pDraw },
      { key: "away", label: m.away.short, p: m.away.p, side: m.away },
    ];
  }
  return [
    { key: "away", label: m.away.short, p: m.away.p, side: m.away },
    { key: "home", label: m.home.short, p: m.home.p, side: m.home },
  ];
}

const title = (m: Market) => isSoccer(m) ? `${m.home.short} vs ${m.away.short}` : `${m.away.short} @ ${m.home.short}`;

function MarketCard({ m, when, t, lang }: { m: Market; when: string; t: T; lang: Lang }) {
  // The whole card opens the game (stretched link); outcome rows sit above it and add that leg to the combo grader.
  return (
    <article className="group relative flex flex-col gap-3 rounded-2xl border border-line bg-surface p-4 shadow-card transition hover:-translate-y-0.5 hover:border-accent/40 hover:shadow-md">
      <div className="flex items-center gap-2.5">
        <div className="flex -space-x-1.5">
          <Badge m={m} side={isSoccer(m) ? m.home : m.away} size={30} />
          <Badge m={m} side={isSoccer(m) ? m.away : m.home} size={30} />
        </div>
        <h3 className="min-w-0 flex-1 truncate text-sm font-semibold leading-snug">
          <Link href={m.href} className="after:absolute after:inset-0 after:rounded-2xl after:content-['']">{title(m)}</Link>
        </h3>
      </div>
      <ul className="flex flex-col gap-1.5">
        {outcomes(m, t).map((o) => (
          <li key={o.key}>
            <PickButton legKey={legKey(m.league, gameId(m), o.key)} label={o.label} p={o.p} modelPick={m.pick === o.key} />
          </li>
        ))}
      </ul>
      <ReasonLine reasons={m.reasons} pick={m.pick} teams={{ home: m.home.short, away: m.away.short }} t={t} lang={lang} />
      {m.polymarket && <PolymarketLine m={m} t={t} />}
      <div className="mt-auto flex flex-col gap-0.5 text-[11px] text-muted">
        <div className="flex items-center justify-between gap-2">
          <LeagueTag m={m} />
          <span className="tabular shrink-0 font-medium">{when}</span>
        </div>
        {m.fresh && <UpdatedAt iso={m.fresh.at} checked={m.fresh.checked} odds={m.fresh.odds} className="relative z-10 self-start text-[10px] text-faint" />}
      </div>
    </article>
  );
}

/** Market keys are `${league}-${game_id}`. */
const gameId = (m: Market) => Number(m.key.slice(m.key.lastIndexOf("-") + 1));

/** Polymarket's price for the same game, as a comparison (not a PolyPicker forecast). */
function PolymarketLine({ m, t }: { m: Market; t: T }) {
  const p = m.polymarket!.p_home;
  const fav = p >= 0.5 ? m.home : m.away;
  const agree = m.pick === (p >= 0.5 ? "home" : "away");
  return (
    <div className="flex items-center justify-between gap-2 rounded-lg border border-dashed border-line px-2.5 py-1.5 text-[11px] text-muted"
      title={t("Polymarket price for this game's winner market, for comparison")}>
      <span>Polymarket</span>
      <span className="tabular">
        <b className="text-ink">{fav.short} {pct(Math.max(p, 1 - p))}</b>
        <span className={`ml-1.5 ${agree ? "text-win" : "text-warn"}`}>{agree ? t("agrees") : t("disagrees")}</span>
      </span>
    </div>
  );
}

function Featured({ m, when, t }: { m: Market; when: string; t: T }) {
  const os = outcomes(m, t);
  return (
    <Link href={m.href} className="group lg:col-span-2">
      <div className="flex h-full flex-col gap-5 rounded-2xl border border-line bg-surface p-5 shadow-card transition group-hover:border-accent/40 sm:p-6">
        <div className="flex items-center justify-between gap-2 text-xs text-muted">
          <LeagueTag m={m} />
          <span className="tabular shrink-0">{when}</span>
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-accent">{t("Closest game coming up")}</p>
          <h2 className="mt-1 text-2xl font-bold tracking-tight sm:text-3xl">{isSoccer(m) ? `${m.home.name} vs ${m.away.name}` : t("{away} at {home}", { away: m.away.name, home: m.home.name })}</h2>
          {m.projection && <p className="mt-1 text-sm text-muted">{m.projection}</p>}
        </div>
        <div className={`grid gap-2 sm:gap-3 ${os.length === 3 ? "grid-cols-3" : "grid-cols-2"}`}>
          {os.map((o) => {
            const pick = m.pick === o.key;
            return (
              <div key={o.key} className={`flex flex-col items-center gap-2 rounded-xl px-3 py-4 text-center ${pick ? "bg-accent-soft" : "bg-surface-2"}`}>
                {o.side ? <Badge m={m} side={o.side} size={44} />
                  : <span className="inline-flex h-11 w-11 items-center justify-center rounded-lg bg-surface text-sm font-bold text-muted">X</span>}
                <span className="w-full truncate text-xs text-muted">{o.label}{o.side?.record ? ` · ${o.side.record}` : ""}</span>
                <span className={`tabular text-3xl font-bold ${pick ? "text-accent" : ""}`}>{o.p !== null ? pct(o.p) : "—"}</span>
              </div>
            );
          })}
        </div>
        <span className="mt-auto self-end text-xs font-semibold text-accent group-hover:underline">{t("View full forecast →")}</span>
      </div>
    </Link>
  );
}

function Strongest({ markets, when, t }: { markets: Market[]; when: (m: Market) => string; t: T }) {
  return (
    <div className="rounded-2xl border border-line bg-surface p-5 shadow-card">
      <h2 className="mb-3 text-sm font-semibold">{t("Strongest picks")}</h2>
      {markets.length === 0 ? <p className="text-sm text-muted">{t("No other games scheduled yet.")}</p> : (
        <ul className="flex flex-col divide-y divide-line">
          {markets.map((m) => {
            const side = m.pick === "home" ? m.home : m.pick === "away" ? m.away : null;
            return (
              <li key={m.key}>
                <Link href={m.href} className="flex items-center gap-3 py-2.5 hover:opacity-80">
                  {side ? <Badge m={m} side={side} size={30} />
                    : <span className="inline-flex h-[30px] w-[30px] items-center justify-center rounded-lg bg-surface-2 text-[10px] font-bold">X</span>}
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{side ? t("{team} to win", { team: side.short }) : t("Draw")}</div>
                    <div className="truncate text-[11px] text-muted">{LEAGUES[m.league].icon} {title(m)} · {when(m)}</div>
                  </div>
                  <span className="tabular text-sm font-bold">{pct(m.pickP!)}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function LiveCard({ m, t }: { m: Market; t: T }) {
  const rows = isSoccer(m) ? [m.home, m.away] : [m.away, m.home];
  const pickSide = m.pick === "home" ? m.home : m.pick === "away" ? m.away : null;
  const pending = m.status !== "live"; // started per the clock, live feed not synced yet
  return (
    <Link href={m.href}
      className="group flex flex-col gap-3 rounded-2xl border border-loss/30 bg-surface p-4 shadow-card transition hover:-translate-y-0.5 hover:border-loss/60 hover:shadow-md">
      <div className="flex items-center justify-between gap-2 text-[11px] text-muted">
        <LeagueTag m={m} />
        <span className="flex items-center gap-1.5 rounded-full bg-loss/10 px-2 py-0.5 font-semibold text-loss">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-loss" aria-hidden />{pending ? t("In progress") : t("Live")}
        </span>
      </div>
      <div className="flex flex-col gap-2">
        {rows.map((s) => (
          <div key={s.team_id} className="flex items-center gap-3">
            <Badge m={m} side={s} size={34} />
            <span className="min-w-0 flex-1 truncate font-medium">{s.name}</span>
            <span className="tabular text-2xl font-bold">{s.score ?? "–"}</span>
          </div>
        ))}
      </div>
      <div className="mt-auto flex items-center justify-between gap-2 border-t border-line pt-2.5 text-[11px] text-muted">
        <span>{pending && rows.every((r) => r.score === null) ? t("Score updating · pre-game forecast") : t("Pre-game forecast")}</span>
        <span className="font-semibold text-ink">{m.pickP === null ? "—" : pickSide ? `${pickSide.abbr} ${pct(m.pickP)}` : `${t("Draw")} ${pct(m.pickP)}`}</span>
      </div>
    </Link>
  );
}

function ResultChip({ m, t }: { m: Market; t: T }) {
  const right = m.pick === m.result;
  const [a, b] = isSoccer(m) ? [m.home, m.away] : [m.away, m.home];
  return (
    <Link href={m.href} className="flex w-[210px] flex-col gap-1.5 rounded-xl border border-line bg-surface px-3 py-2.5 text-sm shadow-card hover:border-accent/40">
      <div className="flex items-center justify-between text-[11px] text-muted">
        <span>{LEAGUES[m.league].icon} {LEAGUES[m.league].label}</span>
        <span className={`font-semibold ${right ? "text-win" : "text-loss"}`}>{right ? `✓ ${t("Pick right")}` : `✗ ${t("Pick wrong")}`}</span>
      </div>
      {[a, b].map((s) => {
        const won = (m.result === "home" && s === m.home) || (m.result === "away" && s === m.away);
        return (
          <div key={s.team_id} className="flex items-center gap-2">
            <Badge m={m} side={s} size={20} />
            <span className={`min-w-0 flex-1 truncate ${won ? "font-semibold" : "text-muted"}`}>{s.short}</span>
            <span className={`tabular ${won ? "font-bold" : "text-muted"}`}>{s.score}</span>
          </div>
        );
      })}
    </Link>
  );
}

function Glance({ href, icon, tone, children }: { href: string; icon: React.ReactNode; tone?: "live"; children: React.ReactNode }) {
  return (
    <a href={href} className={`flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1.5 text-xs font-semibold transition hover:-translate-y-px ${
      tone === "live" ? "border-loss/30 bg-loss/10 text-loss" : "border-line bg-surface text-muted hover:text-ink"}`}>
      <span aria-hidden className="flex items-center">{icon}</span>{children}
    </a>
  );
}

function Chip({ href, active, children }: { href: string; active: boolean; children: React.ReactNode }) {
  return (
    <Link href={href} scroll={false} aria-current={active ? "page" : undefined}
      className={`flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-3.5 py-1.5 text-sm font-medium transition ${
        active ? "border-accent bg-accent-soft text-accent" : "border-line bg-surface text-muted hover:text-ink"}`}>
      {children}
    </Link>
  );
}

function Count({ n }: { n: number }) {
  return <span className="tabular text-xs opacity-70">{n}</span>;
}

function FuturesSkeleton() {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="h-96 animate-pulse rounded-2xl bg-surface-2" />
      <div className="h-96 animate-pulse rounded-2xl bg-surface-2" />
    </div>
  );
}

function HomeSkeleton() {
  return (
    <div className="flex flex-col gap-8">
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="h-72 animate-pulse rounded-2xl bg-surface-2 lg:col-span-2" />
        <div className="h-72 animate-pulse rounded-2xl bg-surface-2" />
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => <div key={i} className="h-44 animate-pulse rounded-2xl bg-surface-2" />)}
      </div>
    </div>
  );
}

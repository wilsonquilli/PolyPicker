import Link from "next/link";
import { connection } from "next/server";

import { Carousel, Tabs } from "@/components/client";
import { TeamBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { addDays } from "@/lib/format";
import { LEAGUES, todayET, whenLabel, type LeagueKey } from "@/lib/home";
import type { T } from "@/lib/i18n";
import { getLang, getT } from "@/lib/i18n/server";
import { SOCCER_LEAGUES, teamCss, type LeagueSlug } from "@/lib/soccer";

/**
 * Combos from `GET /combos`: multi-leg picks (Polymarket "Combo" / sportsbook parlay) that win only if every leg wins.
 * Per sport, a safer and a riskier combo of 3, 4 and 5 legs. The backend freezes a combo once its first game starts
 * and grades it leg by leg, so the cards show ✓ / ✗ as games finish and recently decided combos are listed below.
 */
type Sport = "nba" | "nfl" | "soccer";
type Kind = "safe" | "risky";
interface Team { team_id: number; abbr: string; name: string; short: string; color?: string | null }
interface Leg {
  sport: Sport; league: LeagueSlug | null; game_id: number; tipoff_utc: string; pick: "home" | "away" | "draw";
  team: Team | null; home: Team; away: Team; p_model: number; p_market: number | null; market_source: string | null; href: string;
  result?: "won" | "lost" | "void" | null; game_status?: string; home_score?: number | null; away_score?: number | null;
}
type Status = "open" | "live" | "won" | "lost" | "void";
interface Combo {
  id: number; kind: Kind; n_legs: number; status: Status; settled: boolean; legs: Leg[]; p_model: number; p_market: number | null;
  payout: number; payout_basis: "market" | "model"; edge: number | null;
}
type Slot = Combo | { kind: Kind; n_legs: number; status: "none" };
type Data = Record<Sport, { combos: Slot[]; recent: Combo[] }>;

const SPORTS: { key: Sport; label: string; icon: string }[] = [
  { key: "nba", label: "NBA", icon: "🏀" }, { key: "nfl", label: "NFL", icon: "🏈" }, { key: "soccer", label: "Soccer", icon: "⚽" },
];
const pct = (p: number) => (p > 0 && p < 0.01 ? "<1%" : `${Math.round(p * 100)}%`);
const money = (v: number) => `$${v >= 100 ? v.toFixed(0) : v.toFixed(2)}`;
const STAKE = 10;
const isCombo = (s: Slot): s is Combo => s.status !== "none";
type When = (iso: string) => string;

/** Server component; renders nothing if combos are unavailable, so the rest of the home page still loads. */
export async function Combos() {
  await connection();   // which games are "upcoming" depends on the request time
  const [t, lang] = await Promise.all([getT(), getLang()]);
  let data: Data | null = null;
  try {
    data = (await api<Data>("/combos"))?.data ?? null;
  } catch {
    return null;
  }
  if (!data || SPORTS.every((s) => !data![s.key]?.combos.some(isCombo) && !data![s.key]?.recent.length)) return null;
  const today = todayET(), tomorrow = addDays(today, 1);
  const when: When = (iso) => whenLabel(iso, todayET(new Date(iso)), today, tomorrow, lang);
  const initial = Math.max(0, SPORTS.findIndex((s) => data![s.key]?.combos.some(isCombo)));
  return (
    <section aria-labelledby="combos-h" className="flex flex-col gap-4">
      <div>
        <p className="text-xs font-semibold uppercase tracking-wider text-accent">{t("Combos")}</p>
        <h2 id="combos-h" className="text-xl font-bold tracking-tight sm:text-2xl">{t("Best combos right now")}</h2>
        <p className="mt-0.5 max-w-2xl text-sm text-muted">{t("Multi-leg picks or Parlay, only pays if every leg wins. Our picks for each sport.")}</p>
      </div>
      <Tabs label={t("Combos by sport")} initial={initial}
        tabs={SPORTS.map((s) => ({ key: `combos-${s.key}`, label: <><span aria-hidden>{s.icon}</span>{t(s.label)}</> }))}
        panels={SPORTS.map((s) => <SportPanel key={s.key} d={data![s.key] ?? { combos: [], recent: [] }} t={t} when={when} />)} />
    </section>
  );
}

function SportPanel({ d, t, when }: { d: { combos: Slot[]; recent: Combo[] }; t: T; when: When }) {
  // safer row (3, 4, 5 legs) above the riskier row on wide screens; one swipeable row on phones
  const order = [...d.combos].sort((a, b) => (a.kind === b.kind ? a.n_legs - b.n_legs : a.kind === "safe" ? -1 : 1));
  return (
    <div className="flex flex-col gap-5">
      <div className="no-scrollbar -mx-4 flex snap-x snap-mandatory gap-3 overflow-x-auto px-4 pb-1 sm:mx-0 sm:grid sm:grid-cols-2 sm:overflow-visible sm:px-0 lg:grid-cols-3">
        {order.map((c) => (
          <div key={`${c.kind}-${c.n_legs}`} className="w-[88%] shrink-0 snap-start sm:w-auto">
            {isCombo(c) ? <ComboCard combo={c} t={t} when={when} /> : <EmptyCard kind={c.kind} n={c.n_legs} t={t} />}
          </div>
        ))}
      </div>
      {d.recent.length > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-semibold">{t("Recent combo results")}</h3>
          <Carousel label={t("Recent combo results")} className="-mx-1 px-1 pb-1">
            {d.recent.map((c) => <li key={c.id} className="shrink-0 snap-start"><ResultCard combo={c} t={t} /></li>)}
          </Carousel>
        </div>
      )}
    </div>
  );
}

function KindTag({ kind, n, t }: { kind: Kind; n: number; t: T }) {
  const safe = kind === "safe";
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-bold ${safe ? "bg-win/15 text-win" : "bg-warn/15 text-warn"}`}>
      <span aria-hidden>{safe ? "🛡️" : "🔥"}</span>{safe ? t("Safer combo") : t("Riskier combo")} · {t("{n} legs", { n })}
    </span>
  );
}

function StatusTag({ combo, t }: { combo: Combo; t: T }) {
  const done = combo.legs.filter((l) => l.result === "won").length;
  if (combo.status === "won") return <span className="rounded-full bg-win/15 px-2 py-0.5 text-[11px] font-bold text-win">✓ {t("Hit")}</span>;
  if (combo.status === "lost") return <span className="rounded-full bg-loss/15 px-2 py-0.5 text-[11px] font-bold text-loss">✗ {t("Missed")}</span>;
  if (combo.status === "void") return <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[11px] font-bold text-muted">{t("Void")}</span>;
  if (combo.status === "live") {
    return (
      <span className="flex items-center gap-1.5 rounded-full bg-loss/10 px-2 py-0.5 text-[11px] font-semibold text-loss">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-loss" aria-hidden />{t("{a} of {b} legs hit", { a: done, b: combo.legs.length })}
      </span>
    );
  }
  return null;
}

function EmptyCard({ kind, n, t }: { kind: Kind; n: number; t: T }) {
  return (
    <article className="flex h-full flex-col gap-2 rounded-2xl border border-dashed border-line p-4">
      <div><KindTag kind={kind} n={n} t={t} /></div>
      <p className="text-sm text-muted">
        {kind === "safe" ? t("Not enough strong favorites in the next games to build this combo.") : t("No legs where our model beats the market right now. Check back closer to game time.")}
      </p>
    </article>
  );
}

function ComboCard({ combo, t, when }: { combo: Combo; t: T; when: When }) {
  const safe = combo.kind === "safe";
  const border = combo.status === "won" ? "border-win/50" : combo.status === "lost" ? "border-loss/40" : safe ? "border-line" : "border-warn/40";
  return (
    <article className={`flex h-full flex-col gap-3 rounded-2xl border bg-surface p-4 shadow-card ${border}`}>
      <div className="flex items-center justify-between gap-2">
        <KindTag kind={combo.kind} n={combo.n_legs} t={t} />
        <StatusTag combo={combo} t={t} />
      </div>
      <div className="flex items-end justify-between gap-3 rounded-xl bg-surface-2 px-3 py-2.5">
        <div className="min-w-0">
          <p className="tabular whitespace-nowrap text-xl font-bold tracking-tight sm:text-2xl">
            ${STAKE} <span className="text-muted">→</span> <span className={safe ? "text-win" : "text-warn"}>~{money(STAKE * combo.payout)}</span>
          </p>
          <p className="text-[11px] text-muted">{t("estimated · if every leg wins")}</p>
        </div>
        <div className="shrink-0 text-right">
          <p className="tabular text-xl font-bold text-accent">{pct(combo.p_model)}</p>
          <p className="text-[11px] text-muted">{t("our chance")}</p>
        </div>
      </div>
      <ul className="flex flex-col divide-y divide-line">
        {combo.legs.map((l) => <LegRow key={l.game_id} l={l} t={t} when={when} />)}
      </ul>
    </article>
  );
}

/** ✓ won, ✗ lost, – void, pulsing dot while the game is on; nothing before it starts. */
function Mark({ l, t, size = 20 }: { l: Leg; t: T; size?: number }) {
  const box = "inline-flex shrink-0 items-center justify-center rounded-full font-bold leading-none";
  const s = { width: size, height: size, fontSize: size * 0.6 };
  if (l.result === "won") return <span className={`${box} bg-win text-white`} style={s} role="img" aria-label={t("Leg hit")}>✓</span>;
  if (l.result === "lost") return <span className={`${box} bg-loss text-white`} style={s} role="img" aria-label={t("Leg missed")}>✗</span>;
  if (l.result === "void") return <span className={`${box} bg-surface-2 text-muted`} style={s} role="img" aria-label={t("Leg void")}>–</span>;
  if (l.game_status && l.game_status !== "scheduled") return <span className={`${box} border border-loss/40`} style={s} role="img" aria-label={t("In progress")}><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-loss" /></span>;
  return null;
}

function legLabel(l: Leg, t: T) {
  return l.team ? t("{team} to win", { team: l.team.short }) : t("Draw: {home} v {away}", { home: l.home.short, away: l.away.short });
}

function LegRow({ l, t, when }: { l: Leg; t: T; when: When }) {
  const opp = l.pick === "home" ? l.away : l.home;
  const badge = (team: Team, size: number) => l.sport === "soccer"
    ? <TeamBadge abbr={team.abbr} size={size} league="soccer" color={teamCss({ color: team.color ?? null })} logoId={team.team_id} />
    : <TeamBadge abbr={team.abbr} size={size} league={l.sport} />;
  const context = l.sport === "soccer" && l.league ? SOCCER_LEAGUES[l.league]?.short ?? "" : LEAGUES[l.sport as LeagueKey]?.label ?? "";
  const started = l.game_status !== undefined && l.game_status !== "scheduled";
  const score = started && l.home_score != null && l.away_score != null
    ? (l.sport === "soccer" ? `${l.home.abbr} ${l.home_score}–${l.away_score} ${l.away.abbr}` : `${l.away.abbr} ${l.away_score}–${l.home_score} ${l.home.abbr}`)
    : null;
  return (
    <li>
      <Link href={l.href} className="flex items-center gap-2.5 py-2 hover:opacity-80">
        {l.team ? badge(l.team, 28) : <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-[10px] font-bold text-muted">X</span>}
        <div className="min-w-0 flex-1">
          <p className={`truncate text-sm font-semibold ${l.result === "lost" ? "text-muted line-through decoration-loss/60" : ""}`}>{legLabel(l, t)}</p>
          <p className="truncate text-[11px] text-muted">
            {score ?? <>{l.team ? `${l.pick === "home" ? t("vs") : "@"} ${opp.abbr} · ` : ""}{context} · {when(l.tipoff_utc)}</>}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <div className="text-right">
            <p className="tabular text-sm font-bold">{pct(l.p_model)}</p>
            {l.p_market !== null && <p className="tabular text-[10px] text-muted">{t("mkt {p}", { p: pct(l.p_market) })}</p>}
          </div>
          <Mark l={l} t={t} />
        </div>
      </Link>
    </li>
  );
}

/** A decided combo: did it hit, and which legs did or didn't. */
function ResultCard({ combo, t }: { combo: Combo; t: T }) {
  const hits = combo.legs.filter((l) => l.result === "won").length;
  const won = combo.status === "won";
  return (
    <article className={`flex w-[250px] flex-col gap-2 rounded-xl border bg-surface px-3 py-2.5 text-sm shadow-card ${won ? "border-win/50" : "border-line"}`}>
      <div className="flex items-center justify-between gap-2 text-[11px]">
        <span className="font-semibold text-muted">{combo.kind === "safe" ? "🛡️" : "🔥"} {combo.kind === "safe" ? t("Safer") : t("Riskier")} · {t("{n} legs", { n: combo.n_legs })}</span>
        <StatusTag combo={combo} t={t} />
      </div>
      <ul className="flex flex-col gap-1">
        {combo.legs.map((l) => (
          <li key={l.game_id}>
            <Link href={l.href} className="flex items-center gap-2 hover:opacity-80">
              <span className="flex w-5 shrink-0 justify-center"><Mark l={l} t={t} size={18} /></span>
              <span className={`min-w-0 flex-1 truncate ${l.result === "lost" ? "text-muted line-through decoration-loss/60" : ""}`}>{legLabel(l, t)}</span>
              <span className="tabular text-[11px] text-muted">{pct(l.p_model)}</span>
            </Link>
          </li>
        ))}
      </ul>
      <p className="mt-auto border-t border-line pt-1.5 text-[11px] text-muted">
        {won ? <>{t("Paid")} <b className="tabular text-win">${STAKE} → ~{money(STAKE * combo.payout)}</b></>
          : t("{a} of {b} legs hit", { a: hits, b: combo.legs.length })}
      </p>
    </article>
  );
}

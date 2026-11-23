import Link from "next/link";
import { connection } from "next/server";

import { TeamBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { addDays } from "@/lib/format";
import { LEAGUES, todayET, whenLabel, type LeagueKey } from "@/lib/home";
import type { T } from "@/lib/i18n";
import { getLang, getT } from "@/lib/i18n/server";
import { SOCCER_LEAGUES, teamCss, type LeagueSlug } from "@/lib/soccer";

/** Combos from `GET /combos`: multi-leg picks (Polymarket "Combo" / sportsbook parlay) that win only if every leg wins. */
type Sport = "nba" | "nfl" | "soccer";
interface Team { team_id: number; abbr: string; name: string; short: string; color?: string | null }
interface Leg {
  sport: Sport; league: LeagueSlug | null; game_id: number; tipoff_utc: string; pick: "home" | "away" | "draw";
  team: Team | null; home: Team; away: Team; p_model: number; p_market: number | null; market_source: string | null; href: string;
}
interface Combo { kind: "safe" | "risky"; legs: Leg[]; p_model: number; p_market: number | null; payout: number; payout_basis: "market" | "model"; edge: number | null }
type Data = Record<Sport, { safe: Combo | null; risky: Combo | null }>;

const SPORTS: { key: Sport; label: string; icon: string }[] = [
  { key: "nba", label: "NBA", icon: "🏀" }, { key: "nfl", label: "NFL", icon: "🏈" }, { key: "soccer", label: "Soccer", icon: "⚽" },
];
const pct = (p: number) => (p > 0 && p < 0.01 ? "<1%" : `${Math.round(p * 100)}%`);
const money = (v: number) => `$${v >= 100 ? v.toFixed(0) : v.toFixed(2)}`;
const STAKE = 10;

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
  if (!data || SPORTS.every((s) => !data![s.key].safe && !data![s.key].risky)) return null;
  const today = todayET(), tomorrow = addDays(today, 1);
  const when = (iso: string) => whenLabel(iso, todayET(new Date(iso)), today, tomorrow, lang);
  return (
    <section aria-labelledby="combos-h" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-accent">{t("Combos")}</p>
          <h2 id="combos-h" className="text-xl font-bold tracking-tight sm:text-2xl">{t("Best combos right now")}</h2>
          <p className="mt-0.5 max-w-2xl text-sm text-muted">
            {t("Multi-leg picks built from our forecasts, like a Polymarket Combo or a parlay: it pays only if every leg wins. One safer, one riskier but worth a look, for each sport.")}
          </p>
        </div>
        <span className="text-xs text-muted">{t("Model probabilities · not betting advice")}</span>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        {SPORTS.map((s) => (
          <div key={s.key} className="flex min-w-0 flex-col gap-3">
            <h3 className="flex items-center gap-2 text-sm font-semibold"><span aria-hidden>{s.icon}</span>{t(s.label)}</h3>
            {/* Phones: swipe between the safer and riskier combo; wider screens: stacked in the sport's column. */}
            <div className="no-scrollbar -mx-4 flex snap-x snap-mandatory gap-3 overflow-x-auto px-4 pb-1 lg:mx-0 lg:flex-col lg:overflow-visible lg:px-0">
              {(["safe", "risky"] as const).map((k) => (
                <div key={k} className="w-[88%] shrink-0 snap-start sm:w-[calc(50%-6px)] lg:w-auto">
                  <ComboCard kind={k} combo={data![s.key][k]} t={t} when={when} />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
      <p className="text-[11px] text-faint">
        {t("Legs come from different games and are treated as independent. Payouts use market prices with the bookmaker margin removed (real payouts are lower), or our own odds where no market price exists. Informational only, not betting advice. 21+ where legal. Problem gambling help: 1-800-GAMBLER.")}
      </p>
    </section>
  );
}

function ComboCard({ kind, combo, t, when }: { kind: "safe" | "risky"; combo: Combo | null; t: T; when: (iso: string) => string }) {
  const safe = kind === "safe";
  const tag = (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-bold ${safe ? "bg-win/15 text-win" : "bg-warn/15 text-warn"}`}>
      <span aria-hidden>{safe ? "🛡️" : "🔥"}</span>{safe ? t("Safer combo") : t("Riskier combo")}
    </span>
  );
  if (!combo) {
    return (
      <article className="flex h-full flex-col gap-2 rounded-2xl border border-dashed border-line p-4">
        <div>{tag}</div>
        <p className="text-sm text-muted">
          {safe ? t("Not enough strong favorites in the next games to build this combo.") : t("No legs where our model beats the market right now. Check back closer to game time.")}
        </p>
      </article>
    );
  }
  const won = STAKE * combo.payout;
  return (
    <article className={`flex h-full flex-col gap-3 rounded-2xl border bg-surface p-4 shadow-card ${safe ? "border-line" : "border-warn/40"}`}>
      <div className="flex items-center justify-between gap-2">
        {tag}
        <span className="text-[11px] text-muted">{t("{n} legs", { n: combo.legs.length })}</span>
      </div>
      <div className="flex items-end justify-between gap-3 rounded-xl bg-surface-2 px-3 py-2.5">
        <div className="min-w-0">
          <p className="tabular whitespace-nowrap text-xl font-bold tracking-tight sm:text-2xl">${STAKE} <span className="text-muted">→</span> <span className={safe ? "text-win" : "text-warn"}>{money(won)}</span></p>
          <p className="text-[11px] text-muted">{combo.payout_basis === "market" ? t("if every leg wins · market price") : t("if every leg wins · at our odds")}</p>
        </div>
        <div className="shrink-0 text-right">
          <p className="tabular text-xl font-bold text-accent">{pct(combo.p_model)}</p>
          <p className="text-[11px] text-muted">{t("our chance")}</p>
        </div>
      </div>
      <ul className="flex flex-col divide-y divide-line">
        {combo.legs.map((l) => <LegRow key={l.game_id} l={l} t={t} when={when} />)}
      </ul>
      {combo.p_market !== null && (
        <p className={`rounded-lg px-2.5 py-1.5 text-xs ${combo.edge !== null && combo.edge > 0 ? "bg-accent-soft text-ink" : "bg-surface-2 text-muted"}`}>
          {t("Our chance {a} vs market {b}", { a: pct(combo.p_model), b: pct(combo.p_market) })}
          {!safe && combo.edge !== null && combo.edge > 0 ? ` · ${t("every leg rated higher by our model than by the market")}` : ""}
        </p>
      )}
      {combo.p_market === null && !safe && (
        <p className="rounded-lg bg-surface-2 px-2.5 py-1.5 text-xs text-muted">{t("No market prices for these matches: close calls the model leans toward.")}</p>
      )}
    </article>
  );
}

function LegRow({ l, t, when }: { l: Leg; t: T; when: (iso: string) => string }) {
  const opp = l.pick === "home" ? l.away : l.home;
  const badge = (team: Team, size: number) => l.sport === "soccer"
    ? <TeamBadge abbr={team.abbr} size={size} league="soccer" color={teamCss({ color: team.color ?? null })} logoId={team.team_id} />
    : <TeamBadge abbr={team.abbr} size={size} league={l.sport} />;
  const context = l.sport === "soccer" && l.league ? SOCCER_LEAGUES[l.league]?.short ?? "" : LEAGUES[l.sport as LeagueKey]?.label ?? "";
  return (
    <li>
      <Link href={l.href} className="flex items-center gap-2.5 py-2 hover:opacity-80">
        {l.team ? badge(l.team, 28) : <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-[10px] font-bold text-muted">X</span>}
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">
            {l.team ? t("{team} to win", { team: l.team.short }) : t("Draw: {home} v {away}", { home: l.home.short, away: l.away.short })}
          </p>
          <p className="truncate text-[11px] text-muted">
            {l.team ? `${l.pick === "home" ? t("vs") : "@"} ${opp.abbr} · ` : ""}{context} · {when(l.tipoff_utc)}
          </p>
        </div>
        <div className="shrink-0 text-right">
          <p className="tabular text-sm font-bold">{pct(l.p_model)}</p>
          {l.p_market !== null && <p className="tabular text-[10px] text-muted">{t("mkt {p}", { p: pct(l.p_market) })}</p>}
        </div>
      </Link>
    </li>
  );
}


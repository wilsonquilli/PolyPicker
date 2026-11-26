"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { useLang, useT } from "@/components/i18n";
import { TeamBadge } from "@/components/ui";
import { addDays } from "@/lib/format";
import { LEAGUES, todayET, whenLabel, type LeagueKey } from "@/lib/home";
import type { Lang, T } from "@/lib/i18n";
import { MAX_LEGS, slip, useSlip, type Sport } from "@/lib/slip";
import { bets } from "@/lib/bets";
import { UpdatedAt } from "@/components/client";
import { lastUpdated } from "@/lib/types";
import { SOCCER_LEAGUES, teamCss, type LeagueSlug } from "@/lib/soccer";

/**
 * Combo grader (home page). Users tap outcomes on the game cards (PickButton) to build a combo across NBA, NFL and
 * soccer; `GET /combos/grade` returns our chance that every leg wins, fair odds, value against the market,
 * a letter grade, the weakest leg and a stronger swap. The slip lives in localStorage (lib/slip.ts) and
 * can be shared as `/?slip=...#grader-h`.
 */

const API = process.env.NEXT_PUBLIC_API_URL ?? "/api/v1";
const STAKE = 10;
const POLL_MS = 60_000;   // re-grade while a leg is live / awaiting its result, so finished games show without a reload

interface Team { team_id: number; abbr: string; name: string; short: string; color?: string | null }
interface Leg {
  key: string; sport: Sport; league: LeagueSlug | null; game_id: number; pick: "home" | "away" | "draw"; team: Team | null;
  home: Team; away: Team; tipoff_utc: string; game_status: string; home_score: number | null; away_score: number | null;
  result: "won" | "lost" | "void" | null; p_model: number; p_market: number | null; market_source: string | null; href: string;
  published_at: string; checked_at: string | null; odds_at: string | null;
}
interface Combo {
  chance: number; chance_pregame: number; dead: boolean; p_market: number | null; fair_odds: number | null; market_odds: number | null;
  book_odds: number | null; book_implied: number | null; ev: number | null; edge: number | null; value_basis: "odds" | "market" | null;
  verdict: "good" | "fair" | "bad" | null; payout: number | null; payout_basis: "odds" | "market" | "model"; grade: string;
  weakest: { key: string; p_model: number; chance_without: number } | null; status: "open" | "live" | "won" | "lost" | "void";
  settled: boolean; n_legs: number; swap: { leg: Leg; chance_with: number } | null;
}
interface Graded { legs: Leg[]; unavailable: { key: string; reason: string }[]; combo: Combo | null }

const pct = (p: number) => (p <= 0 ? "0%" : p < 0.001 ? "<0.1%" : p < 0.1 ? `${(p * 100).toFixed(1)}%` : `${Math.round(p * 100)}%`);
const american = (a: number | null, lang: string) => (a === null ? "—" : `${a > 0 ? "+" : ""}${a.toLocaleString(lang)}`);
const money = (v: number) => `$${v >= 1000 ? Math.round(v).toLocaleString("en-US") : v >= 100 ? v.toFixed(0) : v.toFixed(2)}`;

// ------------------------------------------------------------------ pick buttons on the game cards

/** One outcome row on a game card; tapping it adds / removes that leg. Sits above the card's stretched link. */
export function PickButton({ legKey, label, p, modelPick }: { legKey: string; label: string; p: number | null; modelPick: boolean }) {
  const t = useT();
  const keys = useSlip();
  const on = keys.includes(legKey);
  const [full, setFull] = useState(false);
  return (
    <button type="button" aria-pressed={on} disabled={p === null}
      onClick={() => setFull(slip.toggle(legKey) === "full")}
      title={full ? t("A combo can have up to {n} legs", { n: MAX_LEGS }) : on ? t("Remove from your combo") : t("Add to your combo")}
      className={`relative z-10 w-full overflow-hidden rounded-lg text-left transition active:scale-[0.98] disabled:cursor-default ${
        on ? "bg-accent-soft ring-2 ring-accent" : "bg-surface-2 hover:ring-1 hover:ring-accent/40"}`}>
      <span aria-hidden className={`absolute inset-y-0 left-0 ${modelPick || on ? "bg-accent/15" : "bg-ink/[0.04]"}`} style={{ width: p !== null ? `${Math.round(p * 100)}%` : 0 }} />
      <span className="relative flex items-center justify-between gap-2 px-2.5 py-1.5 text-sm">
        <span className={`flex min-w-0 items-center gap-1.5 ${modelPick || on ? "font-semibold" : ""}`}>
          <span aria-hidden className={`inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold leading-none ${
            on ? "bg-accent text-accent-fg" : "border border-line text-muted"}`}>{on ? "✓" : "+"}</span>
          <span className="truncate">{label}</span>
        </span>
        <span className={`tabular font-bold ${modelPick || on ? "text-accent" : "text-muted"}`}>{p !== null ? `${Math.round(p * 100)}%` : "—"}</span>
      </span>
    </button>
  );
}

// ------------------------------------------------------------------ the grader section

export function ComboGrader() {
  const t = useT();
  const lang = useLang();
  const keys = useSlip();
  const [data, setData] = useState<Graded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [inView, setInView] = useState(true);
  const [tick, setTick] = useState(0);
  const ref = useRef<HTMLElement>(null);

  const legsParam = keys.join(",");

  // A combo whose games were all over the last time it was shown starts empty on the next visit.
  useEffect(() => {
    slip.clearIfSettled();
  }, []);

  // A shared link (?slip=...) fills the slip once, then the params are dropped from the address bar.
  useEffect(() => {
    const sp = new URLSearchParams(window.location.search);
    const shared = sp.get("slip");
    if (shared === null) return;
    slip.set(shared.split(","));
    sp.delete("slip");
    sp.delete("odds");
    const rest = sp.toString();
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${rest ? `?${rest}` : ""}#grader-h`);
    requestAnimationFrame(() => document.getElementById("grader-h")?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }, []);

  useEffect(() => {
    if (!legsParam) return;
    const ctrl = new AbortController();
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const qs = `legs=${encodeURIComponent(legsParam)}`;
        const r = await fetch(`${API}/combos/grade?${qs}`, { signal: ctrl.signal });
        const body = await r.json();
        if (!r.ok) {
          setError(body?.error?.message ?? t("Couldn't grade this combo right now."));
        } else {
          setData(body.data);
          setError(null);
        }
      } catch (e) {
        if ((e as Error).name !== "AbortError") setError(t("Couldn't grade this combo right now."));
      } finally {
        if (!ctrl.signal.aborted) setLoading(false);
      }
    }, tick ? 0 : 200);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [legsParam, t, tick]);

  // Every game over: remember it so the slip clears itself next time. Otherwise, while any game has kicked off
  // (or is about to), re-grade every minute so live and final results appear on their own.
  const settled = data?.combo?.settled ?? false;
  useEffect(() => {
    if (settled && data && data.legs.length && data.legs.every((l) => keys.includes(l.key)) && data.legs.length === keys.length) {
      slip.markSettled(keys);
    }
  }, [settled, data, keys]);
  useEffect(() => {
    if (settled || !data) return;
    const id = setInterval(() => {
      const due = data.legs.some((l) => l.result === null && Date.parse(l.tipoff_utc) <= Date.now() + POLL_MS);
      if (due && document.visibilityState === "visible") setTick((n) => n + 1);
    }, POLL_MS);
    return () => clearInterval(id);
  }, [settled, data]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => setInView(e.isIntersecting), { rootMargin: "0px 0px -20% 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  const share = async () => {
    const url = `${window.location.origin}/?slip=${keys.join(",")}#grader-h`;
    const c = data?.combo;
    const text = c ? t("PolyPicker grades my {n}-leg combo {grade} ({chance} to hit)", { n: c.n_legs, grade: c.grade, chance: pct(c.chance) }) : "PolyPicker";
    try {
      if (navigator.share && window.matchMedia("(pointer: coarse)").matches) {
        await navigator.share({ title: "PolyPicker", text, url });
        return;
      }
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {}
  };

  // legs in slip order (the API returns them in request order; unavailable ones are listed separately)
  const legs = data?.legs.filter((l) => keys.includes(l.key)) ?? [];
  const unavailable = data?.unavailable.filter((u) => keys.includes(u.key)) ?? [];
  const pending = keys.length > 0 && (!data || legs.length + unavailable.length !== keys.length);
  const combo = pending ? null : data?.combo ?? null;

  return (
    <section ref={ref} aria-labelledby="grader-h" className="flex scroll-mt-20 flex-col gap-4">
      <div>
        <p className="text-xs font-semibold uppercase tracking-wider text-accent">{t("Combo grader")}</p>
        <h2 id="grader-h" className="scroll-mt-20 text-xl font-bold tracking-tight sm:text-2xl">{t("Grade your combo")}</h2>
        <p className="mt-0.5 max-w-2xl text-sm text-muted">{t("Pick game outcomes across the NBA, NFL and soccer. We grade your combo with our models: chance to hit, fair odds and how it compares to the betting market.")}</p>
      </div>

      {keys.length === 0 ? (
        <div className="flex flex-col items-start gap-3 rounded-2xl border border-dashed border-line p-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <span aria-hidden className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-lg">🧮</span>
            <div>
              <p className="font-semibold">{t("Your combo is empty")}</p>
              <p className="text-sm text-muted">{t("Tap any outcome on the games below to add it as a leg.")}</p>
            </div>
          </div>
          <a href="#markets-h" className="shrink-0 rounded-full bg-accent px-4 py-2 text-sm font-semibold text-accent-fg hover:opacity-90">{t("Browse games ↓")}</a>
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-5">
          {/* legs */}
          <div className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-4 shadow-card lg:col-span-3">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-sm font-semibold">{t(keys.length === 1 ? "{n} leg" : "{n} legs", { n: keys.length })}</h3>
              <button type="button" onClick={() => slip.clear()} className="text-xs font-semibold text-muted hover:text-loss">
                {combo?.settled ? t("Start a new combo") : t("Clear all")}
              </button>
            </div>
            <ul className="flex flex-col divide-y divide-line">
              {pending && !legs.length
                ? keys.map((k) => <li key={k} className="py-2"><div className="h-9 animate-pulse rounded-lg bg-surface-2" /></li>)
                : <>
                    {legs.map((l) => <LegRow key={l.key} l={l} t={t} lang={lang} weakest={combo?.weakest?.key === l.key} />)}
                    {unavailable.map((u) => (
                      <li key={u.key} className="flex items-center justify-between gap-2 py-2 text-sm text-muted">
                        <span>{t("This game is no longer available to grade.")}</span>
                        <RemoveButton k={u.key} t={t} />
                      </li>
                    ))}
                    {pending && legs.length > 0 && <li className="py-2"><div className="h-9 animate-pulse rounded-lg bg-surface-2" /></li>}
                  </>}
            </ul>
          </div>

          {/* result */}
          <div className="lg:col-span-2">
            {error ? (
              <div className="rounded-2xl border border-loss/30 bg-surface p-4 text-sm text-loss shadow-card">{error}</div>
            ) : combo ? (
              <Result combo={combo} legs={legs} t={t} lang={lang} loading={loading} onShare={share} copied={copied} />
            ) : (
              <div className="h-full min-h-64 animate-pulse rounded-2xl bg-surface-2" />
            )}
          </div>
        </div>
      )}

      {keys.length > 0 && !inView && (
        <a href="#grader-h"
          className="fixed bottom-[calc(76px+env(safe-area-inset-bottom))] left-1/2 z-30 flex -translate-x-1/2 whitespace-nowrap items-center gap-2 rounded-full bg-accent px-4 py-2.5 text-sm font-semibold text-accent-fg shadow-lg transition hover:opacity-90 md:bottom-6">
          <span aria-hidden>🧮</span>
          {t(keys.length === 1 ? "{n} leg" : "{n} legs", { n: keys.length })}
          {combo && <span className="tabular rounded-full bg-white/20 px-2 py-0.5 text-xs">{combo.grade} · {pct(combo.chance)}</span>}
          <span aria-hidden>↑</span>
        </a>
      )}
    </section>
  );
}

function RemoveButton({ k, t }: { k: string; t: T }) {
  return (
    <button type="button" onClick={() => slip.remove(k)} aria-label={t("Remove leg")}
      className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-muted hover:bg-loss/10 hover:text-loss">✕</button>
  );
}

function badge(l: Leg, team: Team, size: number) {
  return l.sport === "soccer"
    ? <TeamBadge abbr={team.abbr} size={size} league="soccer" color={teamCss({ color: team.color ?? null })} logoId={team.team_id} />
    : <TeamBadge abbr={team.abbr} size={size} league={l.sport} />;
}

function legLabel(l: Leg, t: T) {
  return l.team ? t("{team} to win", { team: l.team.short }) : t("Draw: {home} v {away}", { home: l.home.short, away: l.away.short });
}

function LegRow({ l, t, lang, weakest }: { l: Leg; t: T; lang: Lang; weakest: boolean }) {
  const today = todayET();
  const when = whenLabel(l.tipoff_utc, todayET(new Date(l.tipoff_utc)), today, addDays(today, 1), lang);
  const context = l.sport === "soccer" && l.league ? SOCCER_LEAGUES[l.league]?.short ?? "" : LEAGUES[l.sport as LeagueKey]?.label ?? "";
  const icon = l.sport === "soccer" ? "⚽" : LEAGUES[l.sport as LeagueKey]?.icon;
  const opp = l.pick === "home" ? l.away : l.home;
  const started = l.game_status !== "scheduled";
  const score = started && l.home_score != null && l.away_score != null
    ? (l.sport === "soccer" ? `${l.home.abbr} ${l.home_score}–${l.away_score} ${l.away.abbr}` : `${l.away.abbr} ${l.away_score}–${l.home_score} ${l.home.abbr}`)
    : null;
  return (
    <li className="flex items-center gap-2.5 py-2">
      <Link href={l.href} className="flex min-w-0 flex-1 items-center gap-2.5 hover:opacity-80">
        {l.team ? badge(l, l.team, 28) : <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-[10px] font-bold text-muted">X</span>}
        <div className="min-w-0 flex-1">
          <p className={`flex items-center gap-1.5 text-sm font-semibold ${l.result === "lost" ? "text-muted line-through decoration-loss/60" : ""}`}>
            <span className="truncate">{legLabel(l, t)}</span>
            {weakest && <span className="shrink-0 rounded-full bg-warn/15 px-1.5 py-px text-[10px] font-bold text-warn no-underline">{t("weakest")}</span>}
          </p>
          <p className="truncate text-[11px] text-muted">
            {score ?? <>{icon} {l.team ? `${l.pick === "home" ? t("vs") : "@"} ${opp.abbr} · ` : ""}{context} · {when}</>}
          </p>
        </div>
      </Link>
      <div className="shrink-0 text-right">
        <p className="tabular text-sm font-bold">{pct(l.p_model)}</p>
        {l.p_market !== null && <p className="tabular text-[10px] text-muted">{t("mkt {p}", { p: pct(l.p_market) })}</p>}
      </div>
      {l.result ? <ResultMark r={l.result} t={t} /> : started ? <span className="flex h-7 w-7 items-center justify-center" role="img" aria-label={t("In progress")}><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-loss" /></span> : <RemoveButton k={l.key} t={t} />}
    </li>
  );
}

function ResultMark({ r, t }: { r: "won" | "lost" | "void"; t: T }) {
  const box = "inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold leading-none mx-0.5";
  if (r === "won") return <span className={`${box} bg-win text-white`} role="img" aria-label={t("Leg hit")}>✓</span>;
  if (r === "lost") return <span className={`${box} bg-loss text-white`} role="img" aria-label={t("Leg missed")}>✗</span>;
  return <span className={`${box} bg-surface-2 text-muted`} role="img" aria-label={t("Leg void")}>–</span>;
}

const GRADE_TONE: Record<string, string> = { A: "bg-win/15 text-win ring-win/30", B: "bg-accent-soft text-accent ring-accent/30", C: "bg-warn/15 text-warn ring-warn/30", D: "bg-loss/10 text-loss ring-loss/25", F: "bg-loss/15 text-loss ring-loss/30" };

function Result({ combo, legs, t, lang, loading, onShare, copied }: {
  combo: Combo; legs: Leg[]; t: T; lang: Lang; loading: boolean; onShare: () => void; copied: boolean;
}) {
  const weak = combo.weakest ? legs.find((l) => l.key === combo.weakest!.key) : undefined;
  const oneIn = combo.chance > 0 && combo.chance < 0.5 ? Math.round(1 / combo.chance) : null;
  const headline = combo.status === "won" ? t("Every leg hit!") : combo.dead ? t("A leg missed, so this combo is out.")
    : oneIn ? t("About 1 in {n}", { n: oneIn.toLocaleString(lang) }) : t("Better than a coin flip");
  return (
    <article className={`flex h-full flex-col gap-4 rounded-2xl border border-line bg-surface p-4 shadow-card transition-opacity ${loading ? "opacity-70" : ""}`} aria-live="polite">
      <div className="flex items-center gap-4">
        <div className={`flex h-20 w-20 shrink-0 items-center justify-center rounded-2xl text-4xl font-black ring-1 ${GRADE_TONE[combo.grade[0]]}`}
          role="img" aria-label={t("Grade {g}", { g: combo.grade })}>{combo.grade}</div>
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">
            {combo.settled ? t("Final") : combo.status === "live" ? t("Chance now") : t("Chance to hit")}
          </p>
          <p className="tabular text-4xl font-bold tracking-tight">{pct(combo.chance)}</p>
          <p className="text-sm text-muted">{headline}</p>
        </div>
      </div>

      <dl className="grid grid-cols-3 gap-2 text-center">
        <Stat label={t("Fair odds")} value={american(combo.fair_odds, lang)} />
        <Stat label={t("Market")} value={combo.p_market !== null ? pct(combo.p_market) : "—"} />
        <Stat label={t("Est. payout")}
          value={combo.payout ? `${money(STAKE * combo.payout)}` : "—"} sub={`$${STAKE} ${t("stake")}`} />
      </dl>

      <Value combo={combo} t={t} />

      {weak && combo.weakest && !combo.dead && (
        <div className="rounded-xl bg-surface-2 p-3 text-sm">
          <p>
            <span className="font-semibold">{t("Weakest leg:")}</span> {legLabel(weak, t)} ({pct(weak.p_model)}).{" "}
            {t("Without it your chance is {p}.", { p: pct(combo.weakest.chance_without) })}
          </p>
          {combo.swap && (
            <div className="mt-2 flex items-center justify-between gap-3 border-t border-line pt-2">
              <p className="min-w-0 text-[13px]">
                {t("Swap for {leg} ({p})", { leg: legLabel(combo.swap.leg, t), p: pct(combo.swap.leg.p_model) })}
                <span className="text-muted"> → </span><b className="tabular text-win">{pct(combo.swap.chance_with)}</b>
              </p>
              <button type="button" onClick={() => slip.replace(weak.key, combo.swap!.leg.key)}
                className="shrink-0 rounded-full border border-accent px-3 py-1 text-xs font-semibold text-accent hover:bg-accent-soft">{t("Swap")}</button>
            </div>
          )}
        </div>
      )}

      {!combo.settled && <StalestLeg legs={legs} />}

      {combo.settled && (
        <p className="rounded-xl bg-surface-2 px-3 py-2 text-sm text-muted">{t("Every game in this combo is over. It clears automatically the next time you open PolyPicker.")}</p>
      )}

      {!combo.settled && !combo.dead && <TrackBet combo={combo} legs={legs} t={t} />}

      <div className="mt-auto flex items-center gap-2">
        <button type="button" onClick={onShare} className="flex-1 rounded-full bg-accent px-4 py-2 text-sm font-semibold text-accent-fg hover:opacity-90">
          {copied ? t("Link copied ✓") : t("Share this grade")}
        </button>
      </div>
    </article>
  );
}

const matchup = (l: Leg) => (l.sport === "soccer" ? `${l.home.abbr} v ${l.away.abbr}` : `${l.away.abbr} @ ${l.home.abbr}`);

/** "I placed this bet": stake + the odds the user got, saved to My bets (this browser) and graded there. */
function TrackBet({ combo, legs, t }: { combo: Combo; legs: Leg[]; t: T }) {
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState(false);
  const suggested = combo.market_odds ?? combo.fair_odds;
  const [stake, setStake] = useState("10");
  const [odds, setOdds] = useState(suggested !== null ? `${suggested > 0 ? "+" : ""}${suggested}` : "");
  const [book, setBook] = useState("");
  const started = legs.some((l) => l.game_status !== "scheduled");
  const stakeN = Number(stake);
  const oddsN = Number(odds.replace(/\s|\+/g, ""));
  const ok = stakeN > 0 && Number.isFinite(oddsN) && Math.abs(oddsN) >= 100;

  if (saved) {
    return (
      <p className="rounded-xl bg-win/10 px-3 py-2 text-sm text-win">
        ✓ {t("Saved to My bets.")} <Link href="/bets" className="font-semibold underline">{t("View →")}</Link>
      </p>
    );
  }
  if (started) return null;   // only bets placed before kickoff are tracked
  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)}
        className="rounded-full border border-accent px-4 py-2 text-sm font-semibold text-accent hover:bg-accent-soft">
        {t("I placed this bet: track it")}
      </button>
    );
  }
  const save = (e: React.FormEvent) => {
    e.preventDefault();
    if (!ok) return;
    bets.add({
      legs: legs.map((l) => ({ key: l.key, label: legLabel(l, t), matchup: matchup(l), p_model: l.p_model })),
      stake: stakeN, odds: oddsN, book: book.trim() || undefined, chance: combo.chance_pregame, market: combo.p_market,
    });
    setSaved(true);
  };
  const field = "w-full rounded-lg border border-line bg-surface-2 px-2.5 py-1.5 text-sm tabular focus:border-accent focus:outline-none";
  return (
    <form onSubmit={save} className="flex flex-col gap-2 rounded-xl bg-surface-2/60 p-3">
      <div className="grid grid-cols-3 gap-2">
        <label className="flex flex-col gap-1 text-[11px] text-muted">{t("Stake ($)")}
          <input inputMode="decimal" value={stake} onChange={(e) => setStake(e.target.value)} className={field} aria-invalid={!(stakeN > 0)} />
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-muted">{t("Your odds")}
          <input inputMode="text" value={odds} onChange={(e) => setOdds(e.target.value)} placeholder="+650" className={field}
            aria-invalid={!(Math.abs(oddsN) >= 100)} />
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-muted">{t("Book (optional)")}
          <input value={book} onChange={(e) => setBook(e.target.value)} maxLength={40} className={field} />
        </label>
      </div>
      <p className="text-[11px] text-muted">{t("American odds from your sportsbook, e.g. +650 or -120. Saved only on this device.")}</p>
      <div className="flex gap-2">
        <button type="submit" disabled={!ok} className="flex-1 rounded-full bg-accent px-4 py-1.5 text-sm font-semibold text-accent-fg disabled:opacity-50">{t("Save bet")}</button>
        <button type="button" onClick={() => setOpen(false)} className="rounded-full px-3 py-1.5 text-sm text-muted hover:text-ink">{t("Cancel")}</button>
      </div>
    </form>
  );
}

/** "Updated 4m ago" for the combo: its least recently refreshed open leg, the number that could be out of date. */
function StalestLeg({ legs }: { legs: Leg[] }) {
  const open = legs.filter((l) => l.result === null && l.published_at);
  if (!open.length) return null;
  const oldest = open.reduce((a, b) => (Date.parse(lastUpdated(b)) < Date.parse(lastUpdated(a)) ? b : a));
  return <UpdatedAt iso={lastUpdated(oldest)} checked={oldest.checked_at} odds={oldest.odds_at} className="-mt-2 text-[11px] text-muted" />;
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl bg-surface-2 px-2 py-2">
      <dt className="truncate text-[11px] text-muted">{label}</dt>
      <dd className="tabular text-base font-bold">{value}</dd>
      {sub && <dd className="text-[10px] text-muted">{sub}</dd>}
    </div>
  );
}

function Value({ combo, t }: { combo: Combo; t: T }) {
  if (combo.dead || combo.settled) return null;
  const tone = combo.verdict === "good" ? "text-win bg-win/10" : combo.verdict === "bad" ? "text-loss bg-loss/10" : "text-warn bg-warn/10";
  if (combo.value_basis === "market" && combo.p_market !== null) {
    const word = combo.verdict === "good" ? t("Beats the market") : combo.verdict === "bad" ? t("We rate it lower than the market") : t("In line with the market");
    return (
      <p className={`rounded-xl px-3 py-2 text-sm ${tone}`}>
        <b>{word}</b> · {t("Betting markets give this {p}; we give it {ours}.", { p: pct(combo.p_market), ours: pct(combo.chance_pregame) })}
      </p>
    );
  }
  return <p className="rounded-xl bg-surface-2 px-3 py-2 text-sm text-muted">{t("No market price for every leg, so this grade is on chance alone.")}</p>;
}

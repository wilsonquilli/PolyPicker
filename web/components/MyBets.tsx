"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { useLang, useT } from "@/components/i18n";
import { useMounted } from "@/components/pwa";
import { bets, decimalOdds, edgeAtPlacement, profit, returned, useBets, type Bet, type BetStatus } from "@/lib/bets";
import type { T } from "@/lib/i18n";

/**
 * My bets: the bets saved from the combo grader on this device. Open bets are re-graded through
 * `GET /combos/grade` (every minute while any is open); settled results are stored and never re-fetched.
 */

const API = process.env.NEXT_PUBLIC_API_URL ?? "/api/v1";
const POLL_MS = 60_000;

interface GradedLeg { key: string; result: "won" | "lost" | "void" | null; game_status: string }
interface Graded { legs: GradedLeg[]; combo: { status: BetStatus; settled: boolean } | null }

const money = (v: number, sign = false) => `${sign && v > 0 ? "+" : v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const american = (a: number) => `${a > 0 ? "+" : ""}${a}`;
const pct = (p: number, d = 0) => `${(p * 100).toFixed(d)}%`;

async function regrade(b: Bet): Promise<Partial<Bet> | null> {
  const r = await fetch(`${API}/combos/grade?legs=${encodeURIComponent(b.legs.map((l) => l.key).join(","))}`);
  if (!r.ok) return null;
  const data: Graded = (await r.json()).data;
  if (!data.combo) return null;
  const by = new Map(data.legs.map((l) => [l.key, l]));
  const legs = b.legs.map((l) => ({ ...l, result: by.get(l.key)?.result ?? l.result ?? null }));
  const status = data.combo.status;
  return { legs, status, ...(data.combo.settled ? { settled_at: new Date().toISOString() } : {}) };
}

export function MyBets() {
  const t = useT();
  const lang = useLang();
  const all = useBets();
  const mounted = useMounted();
  const [tick, setTick] = useState(0);
  const [msg, setMsg] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const openBets = all.filter((b) => b.status === "open" || b.status === "live");
  const settled = all.filter((b) => b.status !== "open" && b.status !== "live");
  const openIds = openBets.map((b) => b.id).join(",");

  // grade open bets now and every minute while any remain open
  useEffect(() => {
    if (!openIds) return;
    let cancelled = false;
    (async () => {
      for (const b of openBets) {
        try {
          const patch = await regrade(b);
          if (!cancelled && patch) bets.update(b.id, patch);
        } catch {}
      }
    })();
    const id = setInterval(() => document.visibilityState === "visible" && setTick((n) => n + 1), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-run on the set of open bets and on each tick
  }, [openIds, tick]);

  const staked = settled.reduce((s, b) => s + b.stake, 0);
  const pnl = settled.reduce((s, b) => s + (profit(b) ?? 0), 0);
  const won = settled.filter((b) => b.status === "won").length;
  const lost = settled.filter((b) => b.status === "lost").length;
  const voids = settled.filter((b) => b.status === "void").length;
  const avgEdge = all.length ? all.reduce((s, b) => s + edgeAtPlacement(b), 0) / all.length : null;
  const atRisk = openBets.reduce((s, b) => s + b.stake, 0);

  const download = () => {
    const url = URL.createObjectURL(new Blob([bets.export()], { type: "application/json" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: `polypicker-bets-${new Date().toISOString().slice(0, 10)}.json` });
    a.click();
    URL.revokeObjectURL(url);
  };
  const upload = async (f: File | undefined) => {
    if (!f) return;
    try {
      const n = bets.import(await f.text());
      setMsg(t(n === 1 ? "Imported {n} bet." : "Imported {n} bets.", { n }));
    } catch {
      setMsg(t("That file isn't a PolyPicker bets export."));
    }
    if (fileRef.current) fileRef.current.value = "";
  };

  if (!mounted) return <div className="h-64 animate-pulse rounded-2xl bg-surface-2" />;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-accent">{t("My bets")}</p>
          <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">{t("Your betting record")}</h1>
          <p className="mt-0.5 max-w-2xl text-sm text-muted">{t("Bets you tracked from the combo grader, graded automatically as games finish. Saved only in this browser: export them to keep a backup or move them to another device.")}</p>
        </div>
        <div className="flex gap-2 text-sm">
          <button type="button" onClick={download} disabled={!all.length} className="rounded-full border border-line px-3 py-1.5 font-medium hover:bg-surface-2 disabled:opacity-40">{t("Export")}</button>
          <button type="button" onClick={() => fileRef.current?.click()} className="rounded-full border border-line px-3 py-1.5 font-medium hover:bg-surface-2">{t("Import")}</button>
          <input ref={fileRef} type="file" accept="application/json,.json" className="hidden" onChange={(e) => upload(e.target.files?.[0])} />
        </div>
      </div>
      {msg && <p role="status" className="rounded-xl bg-surface-2 px-3 py-2 text-sm">{msg}</p>}

      {all.length === 0 ? (
        <div className="flex flex-col items-start gap-3 rounded-2xl border border-dashed border-line p-6">
          <p className="font-semibold">{t("No bets tracked yet")}</p>
          <p className="text-sm text-muted">{t("Build a combo (or a single pick) in the combo grader, then tap “I placed this bet” and enter your stake and odds.")}</p>
          <Link href="/#grader-h" className="rounded-full bg-accent px-4 py-2 text-sm font-semibold text-accent-fg hover:opacity-90">{t("Go to the combo grader")}</Link>
        </div>
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <Tile label={t("Profit")} value={money(pnl, true)} tone={pnl > 0 ? "win" : pnl < 0 ? "loss" : undefined} />
            <Tile label={t("ROI")} value={staked ? pct(pnl / staked, 1) : "—"} tone={pnl > 0 ? "win" : pnl < 0 ? "loss" : undefined} />
            <Tile label={t("Record")} value={`${won}–${lost}${voids ? `–${voids}` : ""}`} sub={voids ? t("won–lost–void") : t("won–lost")} />
            <Tile label={t("Staked")} value={money(staked)} sub={t("settled bets")} />
            <Tile label={t("At risk")} value={money(atRisk)} sub={t(openBets.length === 1 ? "{n} open bet" : "{n} open bets", { n: openBets.length })} />
            <Tile label={t("Model edge")} value={avgEdge === null ? "—" : `${avgEdge > 0 ? "+" : ""}${pct(avgEdge, 1)}`}
              sub={t("avg, when placed")} tone={avgEdge !== null && avgEdge > 0 ? "win" : avgEdge !== null && avgEdge < -0.1 ? "loss" : undefined} />
          </dl>
          <p className="-mt-3 text-xs text-muted">{t("Model edge = our chance that the bet wins × your decimal odds − 1, at the time you placed it. Positive means your price was better than our forecast says it should be.")}</p>

          {openBets.length > 0 && (
            <section aria-labelledby="open-h" className="flex flex-col gap-3">
              <h2 id="open-h" className="text-lg font-semibold">{t("Open")}</h2>
              <ul className="grid gap-3 md:grid-cols-2">{openBets.map((b) => <BetCard key={b.id} b={b} t={t} lang={lang} />)}</ul>
            </section>
          )}
          {settled.length > 0 && (
            <section aria-labelledby="settled-h" className="flex flex-col gap-3">
              <h2 id="settled-h" className="text-lg font-semibold">{t("Settled")}</h2>
              <ul className="grid gap-3 md:grid-cols-2">{settled.map((b) => <BetCard key={b.id} b={b} t={t} lang={lang} />)}</ul>
            </section>
          )}
        </>
      )}
      <p className="text-xs text-muted">{t("Bet responsibly. If gambling stops being fun, call 1-800-GAMBLER.")}</p>
    </div>
  );
}

function Tile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "win" | "loss" }) {
  return (
    <div className="rounded-2xl border border-line bg-surface px-3 py-3 shadow-card">
      <dt className="text-[11px] font-medium text-muted">{label}</dt>
      <dd className={`tabular text-xl font-bold ${tone === "win" ? "text-win" : tone === "loss" ? "text-loss" : ""}`}>{value}</dd>
      {sub && <dd className="text-[11px] text-muted">{sub}</dd>}
    </div>
  );
}

const STATUS: Record<BetStatus, [string, string]> = {
  open: ["Open", "bg-surface-2 text-muted"], live: ["Live", "bg-loss/10 text-loss"], won: ["Won", "bg-win/15 text-win"],
  lost: ["Lost", "bg-loss/15 text-loss"], void: ["Void", "bg-surface-2 text-muted"],
};

function BetCard({ b, t, lang }: { b: Bet; t: T; lang: string }) {
  const [editing, setEditing] = useState(false);
  const [payout, setPayout] = useState(b.payout != null ? String(b.payout) : "");
  const p = profit(b);
  const back = returned(b);
  const toWin = b.stake * (decimalOdds(b.odds) - 1);
  const voidLegs = b.legs.some((l) => l.result === "void");
  const date = new Intl.DateTimeFormat(lang === "es" ? "es-ES" : "en-US", { month: "short", day: "numeric" }).format(new Date(b.created));
  return (
    <li className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-4 shadow-card">
      <div className="flex items-center justify-between gap-2 text-[11px] text-muted">
        <span>{t(b.legs.length === 1 ? "Single" : "{n}-leg combo", { n: b.legs.length })} · {date}{b.book ? ` · ${b.book}` : ""}</span>
        <span className={`rounded-full px-2 py-0.5 font-semibold ${STATUS[b.status][1]}`}>{t(STATUS[b.status][0])}</span>
      </div>
      <ul className="flex flex-col gap-1.5">
        {b.legs.map((l) => (
          <li key={l.key} className="flex items-center gap-2 text-sm">
            <Mark r={l.result ?? null} t={t} />
            <span className={`min-w-0 flex-1 truncate ${l.result === "lost" ? "text-muted line-through" : "font-medium"}`}>{l.label}</span>
            <span className="shrink-0 text-[11px] text-muted">{l.matchup}</span>
            <span className="tabular w-9 shrink-0 text-right text-[11px] text-muted">{pct(l.p_model)}</span>
          </li>
        ))}
      </ul>
      <div className="tabular grid grid-cols-4 gap-2 border-t border-line pt-3 text-center text-sm">
        <Fig label={t("Stake")} value={money(b.stake)} />
        <Fig label={t("Odds")} value={american(b.odds)} />
        <Fig label={t("Our chance")} value={pct(b.chance, b.chance < 0.1 ? 1 : 0)} />
        {p === null ? <Fig label={t("To win")} value={money(toWin)} />
          : <Fig label={t("Profit")} value={money(p, true)} tone={p > 0 ? "win" : p < 0 ? "loss" : undefined} />}
      </div>
      {b.status === "won" && voidLegs && b.payout == null && (
        <p className="text-[11px] text-warn">{t("A leg was void, so your book probably paid less. Enter what you actually got back.")}</p>
      )}
      <div className="flex items-center justify-between gap-2 text-xs">
        {back !== null && !editing ? (
          <button type="button" onClick={() => setEditing(true)} className="text-muted hover:text-ink">
            {t("Returned {amount}", { amount: money(back) })} · <span className="underline">{t("edit")}</span>
          </button>
        ) : editing ? (
          <form className="flex items-center gap-1.5" onSubmit={(e) => {
            e.preventDefault();
            const v = payout.trim() === "" ? null : Number(payout);
            if (v === null || (Number.isFinite(v) && v >= 0)) {
              bets.update(b.id, { payout: v });
              setEditing(false);
            }
          }}>
            <label className="text-muted" htmlFor={`pay-${b.id}`}>{t("Returned $")}</label>
            <input id={`pay-${b.id}`} inputMode="decimal" value={payout} onChange={(e) => setPayout(e.target.value)} placeholder={back?.toFixed(2)}
              className="tabular w-20 rounded-md border border-line bg-surface-2 px-1.5 py-0.5" />
            <button type="submit" className="font-semibold text-accent">{t("Save")}</button>
          </form>
        ) : <span />}
        <button type="button" onClick={() => { if (window.confirm(t("Delete this bet from your record?"))) bets.remove(b.id); }}
          className="text-muted hover:text-loss">{t("Delete")}</button>
      </div>
    </li>
  );
}

function Fig({ label, value, tone }: { label: string; value: string; tone?: "win" | "loss" }) {
  return (
    <div>
      <div className="text-[10px] text-muted">{label}</div>
      <div className={`font-semibold ${tone === "win" ? "text-win" : tone === "loss" ? "text-loss" : ""}`}>{value}</div>
    </div>
  );
}

function Mark({ r, t }: { r: "won" | "lost" | "void" | null; t: T }) {
  const box = "inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold";
  if (r === "won") return <span className={`${box} bg-win text-white`} role="img" aria-label={t("Leg hit")}>✓</span>;
  if (r === "lost") return <span className={`${box} bg-loss text-white`} role="img" aria-label={t("Leg missed")}>✗</span>;
  if (r === "void") return <span className={`${box} bg-surface-2 text-muted`} role="img" aria-label={t("Leg void")}>–</span>;
  return <span className={`${box} border border-line text-muted`} role="img" aria-label={t("Pending")}>·</span>;
}

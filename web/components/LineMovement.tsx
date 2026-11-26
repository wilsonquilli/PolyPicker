// Accuracy pages: how the betting line moved between open and close, against our picks and on its own.
// Data: GET {base}/line-movement?kind= (backend api/linemove.py). Server component; fetches its own data so a slow
// or failing request can't hold up the rest of the page (wrap it in <Suspense>).

import { api } from "@/lib/api";
import { getT } from "@/lib/i18n/server";

interface Group { n: number; hit_rate: number | null; expected: number | null }
interface Follow { n: number; hit_rate: number | null; expected: number | null; threshold_pts: number }
interface Favs { [k: string]: { n: number; accuracy: number | null } }
interface LineMove {
  n: number; moved_our_way: number | null; moved_n: number; avg_move_pts: number | null; dropped_in_play: number;
  groups: { toward: Group; flat: Group; against: Group }; favorites: Favs; follow_big_moves: Follow; source: string;
  market_history: { n: number; seasons: number[]; favorites: Favs; follow_big_moves: Follow; dropped_in_play: number };
}

const pct = (v: number | null | undefined, d = 1) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
const seasonSpan = (s: number[]) => (s.length ? (s[0] === s[s.length - 1] ? `${s[0]}` : `${s[0]}–${s[s.length - 1]}`) : "");

export async function LineMovement({ base, kind, unit = "game" }: { base: string; kind: "live" | "backtest"; unit?: "game" | "match" }) {
  const t = await getT();
  const d = (await api<LineMove>(`${base}/line-movement`, { kind }).catch(() => null))?.data;
  const h = d?.market_history;
  if (!d || (!d.n && !h?.n)) {
    return (
      <section className="rounded-2xl border border-dashed border-line p-5 text-sm text-muted">
        <h2 className="mb-1 font-semibold text-ink">{t("Line movement")}</h2>
        {t("We don't have opening prices for this competition yet, so line movement can't be measured here.")}
      </section>
    );
  }
  const f = h?.follow_big_moves;
  const gap = f?.hit_rate != null && f.expected != null ? f.hit_rate - f.expected : null;
  // Only call it either way when the gap is clear of sampling noise (about 2 standard errors).
  const se = f?.expected != null && f.n > 0 ? Math.sqrt((f.expected * (1 - f.expected)) / f.n) : null;
  const verdict = gap === null || se === null ? null : Math.abs(gap) < 2 * se ? t("Late moves are already priced in: big movers win about as often as their closing price says.")
    : gap > 0 ? t("Late money knew a bit more than the close: big movers won more often than their closing price implied.")
    : t("Late moves overshot: big movers won less often than their closing price implied.");
  const rows: [keyof LineMove["groups"], string][] = [["toward", t("Moved toward our pick")], ["flat", t("Barely moved")], ["against", t("Moved against our pick")]];
  return (
    <section aria-labelledby="lm-h" className="flex flex-col gap-4 rounded-2xl border border-line bg-surface p-5 shadow-card">
      <div>
        <h2 id="lm-h" className="text-lg font-semibold">{t("Line movement")}</h2>
        <p className="text-sm text-muted">{t("How the betting price moved from the opening line to the close, with the bookmaker margin removed. Source: {s}.", { s: t(d.source) })}</p>
      </div>

      {d.n > 0 && (
        <div className="flex flex-col gap-3">
          <p className="text-sm">
            {d.moved_our_way != null
              ? t("When the line moved, it moved toward our pick in {p} of {n} {units}: betting our pick at the opening price would have beaten the closing line that often.",
                  { p: pct(d.moved_our_way, 0), n: d.moved_n, units: t(unit === "match" ? "matches" : "games") })
              : t("The line barely moved on these {units}.", { units: t(unit === "match" ? "matches" : "games") })}
          </p>
          <div className="overflow-x-auto">
            <table className="tabular w-full text-sm">
              <thead><tr className="text-[11px] uppercase tracking-wide text-muted">
                <th className="py-1.5 text-left font-semibold">{t("Line")}</th><th className="text-right font-semibold">{t(unit === "match" ? "Matches" : "Games")}</th>
                <th className="text-right font-semibold">{t("Our pick won")}</th><th className="text-right font-semibold">{t("Closing price said")}</th></tr></thead>
              <tbody>
                {rows.map(([k, label]) => (
                  <tr key={k} className="border-t border-line">
                    <td className="py-1.5">{label}</td><td className="text-right">{d.groups[k].n}</td>
                    <td className="text-right font-semibold">{pct(d.groups[k].hit_rate)}</td><td className="text-right text-muted">{pct(d.groups[k].expected)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {d.n < 100 && <p className="text-xs text-warn">{t("Small sample: only {n} graded {units} with opening and closing prices so far.", { n: d.n, units: t(unit === "match" ? "matches" : "games") })}</p>}
        </div>
      )}

      {h && h.n > 0 && (
        <div className="flex flex-col gap-2 rounded-xl bg-surface-2 p-4">
          <h3 className="text-sm font-semibold">{t("The market on its own, {s}", { s: seasonSpan(h.seasons) })} <span className="font-normal text-muted">· {t("{n} {units}", { n: h.n.toLocaleString(), units: t(unit === "match" ? "matches" : "games") })}</span></h3>
          <dl className="tabular grid grid-cols-2 gap-2 text-center sm:grid-cols-3">
            <Stat label={t("Opening favorite won")} value={pct(h.favorites.open?.accuracy)} />
            <Stat label={t("Closing favorite won")} value={pct(h.favorites.close?.accuracy)} />
            {f && f.n > 0 && <Stat label={t("Big movers (+{x} pts) won", { x: f.threshold_pts })} value={pct(f.hit_rate)} sub={t("closing price said {p} · {n} {units}", { p: pct(f.expected), n: f.n, units: t(unit === "match" ? "matches" : "games") })} />}
          </dl>
          {verdict && <p className="text-sm text-muted">{verdict}</p>}
        </div>
      )}
      {(d.dropped_in_play > 0 || (h?.dropped_in_play ?? 0) > 0) && (
        <p className="text-[11px] text-muted">{t("Moves of more than 25 points are left out: those are almost always a price recorded after kickoff.")}</p>
      )}
    </section>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg bg-surface px-2 py-2">
      <dt className="text-[11px] text-muted">{label}</dt>
      <dd className="text-lg font-bold">{value}</dd>
      {sub && <dd className="text-[10px] text-muted">{sub}</dd>}
    </div>
  );
}

"use client";

import { useState } from "react";

import type { Dist } from "@/lib/types";

type Mode = "margin" | "total";

function normPdf(x: number, m: number, s: number) {
  return Math.exp(-0.5 * ((x - m) / s) ** 2) / (s * Math.sqrt(2 * Math.PI));
}

/**
 * The chart area of the event page. Instead of a price history, it shows the model's predictive
 * distribution: the shaded area right of 0 *is* the home win probability (or the over probability).
 */
export function DistributionChart({ margin, total, homeAbbr, awayAbbr, actualMargin, actualTotal }: {
  margin?: Dist; total?: Dist; homeAbbr: string; awayAbbr: string; actualMargin?: number | null; actualTotal?: number | null;
}) {
  const [mode, setMode] = useState<Mode>("margin");
  const d = mode === "margin" ? margin : total;
  if (!d) return null;

  const W = 640, H = 220, padL = 8, padR = 8, padT = 16, padB = 30;
  const lo = d.mean - 3.2 * d.sd, hi = d.mean + 3.2 * d.sd;
  const xs = Array.from({ length: 161 }, (_, i) => lo + ((hi - lo) * i) / 160);
  const ymax = normPdf(d.mean, d.mean, d.sd);
  const X = (v: number) => padL + ((v - lo) / (hi - lo)) * (W - padL - padR);
  const Y = (v: number) => padT + (1 - v / ymax) * (H - padT - padB);
  const split = mode === "margin" ? 0 : Math.round(d.mean) + 0.5;
  const path = (pts: number[]) => pts.map((x, i) => `${i ? "L" : "M"}${X(x).toFixed(1)},${Y(normPdf(x, d.mean, d.sd)).toFixed(1)}`).join(" ");
  const left = xs.filter((x) => x <= split), right = xs.filter((x) => x >= split);
  const area = (pts: number[]) => pts.length ? `${path(pts)} L${X(pts[pts.length - 1])},${Y(0)} L${X(pts[0])},${Y(0)} Z` : "";
  const ticks = niceTicks(lo, hi, 7);
  const actual = mode === "margin" ? actualMargin : actualTotal;
  const pRight = 1 - cdf((split - d.mean) / d.sd);

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex gap-1 rounded-lg bg-surface-2 p-0.5 text-xs font-medium" role="tablist">
          {(["margin", "total"] as Mode[]).map((m) => (
            <button key={m} role="tab" aria-selected={mode === m} onClick={() => setMode(m)}
              className={`rounded-md px-3 py-1 ${mode === m ? "bg-surface text-ink shadow-card" : "text-muted"}`}>
              {m === "margin" ? "Final margin" : "Total points"}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3 text-xs text-muted">
          {mode === "margin" ? (
            <>
              <Legend color="var(--text-muted)" label={`${awayAbbr} wins ${(100 - pRight * 100).toFixed(0)}%`} faded />
              <Legend color="var(--accent)" label={`${homeAbbr} wins ${(pRight * 100).toFixed(0)}%`} />
            </>
          ) : (
            <>
              <Legend color="var(--text-muted)" label={`Under ${split} · ${(100 - pRight * 100).toFixed(0)}%`} faded />
              <Legend color="var(--accent)" label={`Over ${split} · ${(pRight * 100).toFixed(0)}%`} />
            </>
          )}
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img"
        aria-label={mode === "margin"
          ? `Projected ${homeAbbr} margin ${d.mean.toFixed(1)} points; ${homeAbbr} win probability ${(pRight * 100).toFixed(0)}%`
          : `Projected total ${d.mean.toFixed(1)} points`}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={X(t)} x2={X(t)} y1={padT} y2={H - padB} stroke="var(--border)" strokeDasharray="2 4" />
            <text x={X(t)} y={H - 10} textAnchor="middle" fontSize="11" fill="var(--text-faint)">
              {mode === "margin" ? (t > 0 ? `${homeAbbr} +${t}` : t < 0 ? `${awayAbbr} +${-t}` : "0") : t}
            </text>
          </g>
        ))}
        <path d={area(left)} fill="var(--text-muted)" opacity="0.28" />
        <path d={area(right)} fill="var(--accent)" opacity="0.22" />
        <path d={path(xs)} fill="none" stroke="var(--accent)" strokeWidth="2.2" />
        <line x1={X(split)} x2={X(split)} y1={padT - 4} y2={H - padB} stroke="var(--text)" strokeWidth="1.2" />
        <line x1={X(d.mean)} x2={X(d.mean)} y1={Y(ymax)} y2={H - padB} stroke="var(--accent)" strokeDasharray="4 3" />
        <text x={X(d.mean)} y={padT - 4} textAnchor="middle" fontSize="11" fontWeight="600" fill="var(--accent)">
          proj. {mode === "margin" ? `${d.mean > 0 ? homeAbbr : awayAbbr} by ${Math.abs(d.mean).toFixed(1)}` : d.mean.toFixed(1)}
        </text>
        {actual !== null && actual !== undefined && actual >= lo && actual <= hi && (
          <g>
            <circle cx={X(actual)} cy={H - padB} r="5" fill="var(--text)" />
            <text x={X(actual)} y={H - padB - 10} textAnchor="middle" fontSize="11" fontWeight="700" fill="var(--text)">actual</text>
          </g>
        )}
      </svg>
      <p className="mt-1 text-xs text-muted">
        {mode === "margin"
          ? "Model distribution of the final margin. The shaded area on each side of 0 is that team's win probability."
          : `Model distribution of combined points. Shaded areas are the over/under probabilities for ${split}.`}
      </p>
    </div>
  );
}

function Legend({ color, label, faded }: { color: string; label: string; faded?: boolean }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="h-2.5 w-2.5 rounded-sm" style={{ background: color, opacity: faded ? 0.5 : 1 }} />
      {label}
    </span>
  );
}

function niceTicks(lo: number, hi: number, n: number): number[] {
  const step0 = (hi - lo) / n;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= step0) ?? step0;
  const out: number[] = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) out.push(Math.round(t));
  return out;
}

function cdf(z: number) {
  // Abramowitz-Stegun approximation of the standard normal CDF
  const t = 1 / (1 + 0.2316419 * Math.abs(z));
  const d = 0.3989423 * Math.exp(-z * z / 2);
  const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return z > 0 ? 1 - p : p;
}

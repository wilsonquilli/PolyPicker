// Small server-rendered SVG charts.

/** Bars above/below zero, e.g. point margin per game (oldest -> newest). */
export function MarginBars({ values, labels, height = 120 }: { values: number[]; labels?: string[]; height?: number }) {
  if (!values.length) return null;
  const W = Math.max(values.length * 22, 200), H = height, mid = H / 2;
  const max = Math.max(...values.map(Math.abs), 10);
  const bw = W / values.length;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img"
      aria-label={`Point margins: ${values.map((v) => (v > 0 ? `+${v}` : v)).join(", ")}`}>
      <line x1="0" x2={W} y1={mid} y2={mid} stroke="var(--border)" />
      {values.map((v, i) => {
        const h = (Math.abs(v) / max) * (mid - 6);
        return (
          <rect key={i} x={i * bw + bw * 0.18} width={bw * 0.64} y={v >= 0 ? mid - h : mid} height={Math.max(h, 1)} rx="2"
            fill={v >= 0 ? "var(--win)" : "var(--loss)"}>
            <title>{`${labels?.[i] ?? ""} ${v > 0 ? `+${v}` : v}`}</title>
          </rect>
        );
      })}
    </svg>
  );
}

/** Bars per game with an average line, e.g. player points (oldest -> newest). Null = did not play. */
export function StatBars({ values, labels, average, height = 140 }: { values: (number | null)[]; labels: string[]; average?: number | null; height?: number }) {
  if (!values.length) return null;
  const W = Math.max(values.length * 24, 240), H = height, pad = 16;
  const max = Math.max(...values.map((v) => v ?? 0), average ?? 0, 5);
  const bw = W / values.length;
  const Y = (v: number) => H - pad - (v / max) * (H - pad * 2);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img"
      aria-label={`Per-game values: ${values.map((v) => v ?? "DNP").join(", ")}`}>
      <line x1="0" x2={W} y1={H - pad} y2={H - pad} stroke="var(--border)" />
      {values.map((v, i) => v === null ? (
        <text key={i} x={i * bw + bw / 2} y={H - pad - 4} textAnchor="middle" fontSize="8" fill="var(--text-faint)">DNP</text>
      ) : (
        <g key={i}>
          <rect x={i * bw + bw * 0.18} width={bw * 0.64} y={Y(v)} height={H - pad - Y(v)} rx="2" fill="var(--accent)" opacity="0.85">
            <title>{`${labels[i]}: ${v}`}</title>
          </rect>
          {values.length <= 20 && <text x={i * bw + bw / 2} y={Y(v) - 3} textAnchor="middle" fontSize="9" fill="var(--text-muted)">{v}</text>}
        </g>
      ))}
      {average !== null && average !== undefined && (
        <g>
          <line x1="0" x2={W} y1={Y(average)} y2={Y(average)} stroke="var(--text)" strokeDasharray="4 3" opacity="0.6" />
          <text x={W - 2} y={Y(average) - 4} textAnchor="end" fontSize="10" fill="var(--text-muted)">avg {average}</text>
        </g>
      )}
    </svg>
  );
}

/** Reliability diagram: predicted probability (x) vs observed frequency (y). */
export function CalibrationChart({ bins, size = 300 }: { bins: { mean_predicted: number; observed_rate: number; n: number }[]; size?: number }) {
  const pad = 44, S = size;
  const X = (v: number) => pad + v * (S - pad - 10);
  const Y = (v: number) => S - pad - v * (S - pad - 10);
  const maxN = Math.max(...bins.map((b) => b.n), 1);
  return (
    <svg viewBox={`0 0 ${S} ${S}`} className="h-auto w-full max-w-[360px]" role="img"
      aria-label={`Calibration: ${bins.map((b) => `predicted ${Math.round(b.mean_predicted * 100)}% observed ${Math.round(b.observed_rate * 100)}%`).join("; ")}`}>
      {[0, 0.25, 0.5, 0.75, 1].map((t) => (
        <g key={t}>
          <line x1={X(0)} x2={X(1)} y1={Y(t)} y2={Y(t)} stroke="var(--border)" strokeDasharray="2 4" />
          <text x={pad - 6} y={Y(t) + 3} textAnchor="end" fontSize="10" fill="var(--text-faint)">{t * 100}%</text>
          <text x={X(t)} y={S - pad + 14} textAnchor="middle" fontSize="10" fill="var(--text-faint)">{t * 100}%</text>
        </g>
      ))}
      <line x1={X(0)} y1={Y(0)} x2={X(1)} y2={Y(1)} stroke="var(--text-faint)" strokeDasharray="4 4" />
      <polyline fill="none" stroke="var(--accent)" strokeWidth="2"
        points={bins.map((b) => `${X(b.mean_predicted)},${Y(b.observed_rate)}`).join(" ")} />
      {bins.map((b, i) => (
        <circle key={i} cx={X(b.mean_predicted)} cy={Y(b.observed_rate)} r={3 + 5 * Math.sqrt(b.n / maxN)} fill="var(--accent)" opacity="0.85">
          <title>{`Predicted ${(b.mean_predicted * 100).toFixed(0)}% → observed ${(b.observed_rate * 100).toFixed(0)}% (n=${b.n})`}</title>
        </circle>
      ))}
      <text x={X(0.5)} y={S - 4} textAnchor="middle" fontSize="10" fill="var(--text-muted)">Forecast probability</text>
      <text x={9} y={Y(0.5)} textAnchor="middle" fontSize="10" fill="var(--text-muted)" transform={`rotate(-90 9 ${Y(0.5)})`}>Observed frequency</text>
    </svg>
  );
}

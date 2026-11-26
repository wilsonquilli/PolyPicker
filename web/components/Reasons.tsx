import type { Lang, T } from "@/lib/i18n";
import { reasonText, stance, topReason, type Reason, type ReasonTeams } from "@/lib/reasons";

type Pick = "home" | "away" | "draw" | null;

export function ReasonLine({ reasons, pick, teams, t, lang, className = "" }: {
  reasons: Reason[] | null | undefined; pick: Pick; teams: ReasonTeams; t: T; lang: Lang; className?: string;
}) {
  const r = topReason(reasons, pick);
  const text = r && reasonText(r, teams, t, lang);
  if (!text) return null;
  return (
    <p className={`flex min-w-0 items-start gap-1.5 text-[11px] leading-snug text-muted ${className}`} title={text}>
      <span aria-hidden className="shrink-0">💡</span>
      <span className="line-clamp-2">{text}</span>
    </p>
  );
}

export function ReasonList({ reasons, pick, teams, pickLabel, t, lang }: {
  reasons: Reason[] | null | undefined; pick: Pick; teams: ReasonTeams; pickLabel: string; t: T; lang: Lang;
}) {
  const rows = (reasons ?? []).map((r) => ({ r, text: reasonText(r, teams, t, lang), s: stance(r, pick) })).filter((x) => x.text);
  if (!rows.length) return null;
  const mark = { for: ["✓", "bg-win/15 text-win", t("Backs the pick")], against: ["!", "bg-warn/15 text-warn", t("Against the pick")],
    neutral: ["·", "bg-surface-2 text-muted", t("Context")] } as const;
  return (
    <section aria-labelledby="why-h" className="rounded-2xl border border-line bg-surface p-4 shadow-card">
      <h2 id="why-h" className="mb-2.5 text-sm font-semibold">{t("Why we pick {pick}", { pick: pickLabel })}</h2>
      <ul className="flex flex-col gap-2">
        {rows.map(({ r, text, s }, i) => (
          <li key={`${r.code}-${r.side}-${i}`} className="flex items-start gap-2.5 text-sm">
            <span role="img" aria-label={mark[s][2]}
              className={`mt-px inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${mark[s][1]}`}>{mark[s][0]}</span>
            <span>{text}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
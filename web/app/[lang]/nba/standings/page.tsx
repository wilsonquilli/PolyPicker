import Link from "next/link";
import { Suspense } from "react";

import { Card, Skeleton, TeamBadge } from "@/components/ui";
import { api } from "@/lib/api";
import type { StandingRow } from "@/lib/types";
import { getT } from "@/lib/i18n/server";

export async function generateMetadata() {
  return { title: (await getT())("Standings") };
}

export default async function StandingsPage() {
  const t = await getT();
  return (
    <div>
      <h1 className="mb-1 text-2xl font-bold tracking-tight">{t("Standings")}</h1>
      <p className="mb-6 text-sm text-muted">{t("Regular-season records. The NBA Cup final doesn’t count, as in official standings.")}</p>
      <Suspense fallback={<div className="grid gap-6 lg:grid-cols-2"><Skeleton className="h-[640px]" /><Skeleton className="h-[640px]" /></div>}>
        <Standings />
      </Suspense>
    </div>
  );
}

async function Standings() {
  const t = await getT();
  const res = await api<Record<string, StandingRow[]>>("/standings");
  const data = res?.data ?? {};
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      {["East", "West"].map((conf) => (
        <Card key={conf} className="overflow-x-auto">
          <h2 className="px-5 pt-4 text-lg font-semibold">{t(conf === "East" ? "Eastern Conference" : "Western Conference")}</h2>
          <table className="tabular mt-2 w-full text-sm">
            <thead>
              <tr className="text-[11px] uppercase tracking-wide text-muted">
                <th className="px-5 py-2 text-left font-semibold">{t("Team")}</th>
                <th className="px-2 text-right font-semibold">{t("W")}</th><th className="px-2 text-right font-semibold">{t("L")}</th>
                <th className="px-2 text-right font-semibold">{t("Pct")}</th><th className="px-2 text-right font-semibold">{t("GB")}</th>
                <th className="px-5 text-right font-semibold">{t("Diff")}</th>
              </tr>
            </thead>
            <tbody>
              {(data[conf] ?? []).map((row, i) => (
                <tr key={row.team_id} className={`border-t border-line ${i === 5 || i === 9 ? "border-b-2 border-b-line" : ""}`}>
                  <td className="px-5 py-2">
                    <Link href={`/nba/teams/${row.abbreviation}`} className="flex items-center gap-2.5 hover:underline">
                      <span className="w-4 text-right text-xs text-faint">{i + 1}</span>
                      <TeamBadge abbr={row.abbreviation} size={24} />
                      <span className="font-medium">{row.full_name}</span>
                    </Link>
                  </td>
                  <td className="px-2 text-right font-semibold">{row.wins}</td>
                  <td className="px-2 text-right">{row.losses}</td>
                  <td className="px-2 text-right text-muted">{row.win_pct?.toFixed(3).replace(/^0/, "") ?? "—"}</td>
                  <td className="px-2 text-right text-muted">{row.games_behind ? row.games_behind.toFixed(1) : "—"}</td>
                  <td className={`px-5 text-right font-medium ${(row.avg_margin ?? 0) > 0 ? "text-win" : (row.avg_margin ?? 0) < 0 ? "text-loss" : ""}`}>
                    {row.avg_margin !== null ? (row.avg_margin > 0 ? `+${row.avg_margin}` : row.avg_margin) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="px-5 py-3 text-[11px] text-muted">{t("Lines after 6th and 10th: playoff and play-in cutoffs. Diff = average point margin.")}</p>
        </Card>
      ))}
    </div>
  );
}

import Link from "next/link";
import { Suspense } from "react";

import { Card, Skeleton, TeamBadge } from "@/components/ui";
import { api } from "@/lib/api";
import type { StandingRow } from "@/lib/types";

export const metadata = { title: "Standings" };

export default function StandingsPage() {
  return (
    <div>
      <h1 className="mb-1 text-2xl font-bold tracking-tight">Standings</h1>
      <p className="mb-6 text-sm text-muted">Regular-season records. The NBA Cup final doesn’t count, as in official standings.</p>
      <Suspense fallback={<div className="grid gap-6 lg:grid-cols-2"><Skeleton className="h-[640px]" /><Skeleton className="h-[640px]" /></div>}>
        <Standings />
      </Suspense>
    </div>
  );
}

async function Standings() {
  const res = await api<Record<string, StandingRow[]>>("/standings");
  const data = res?.data ?? {};
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      {["East", "West"].map((conf) => (
        <Card key={conf} className="overflow-x-auto">
          <h2 className="px-5 pt-4 text-lg font-semibold">{conf}ern Conference</h2>
          <table className="tabular mt-2 w-full text-sm">
            <thead>
              <tr className="text-[11px] uppercase tracking-wide text-muted">
                <th className="px-5 py-2 text-left font-semibold">Team</th>
                <th className="px-2 text-right font-semibold">W</th><th className="px-2 text-right font-semibold">L</th>
                <th className="px-2 text-right font-semibold">Pct</th><th className="px-2 text-right font-semibold">GB</th>
                <th className="px-5 text-right font-semibold">Diff</th>
              </tr>
            </thead>
            <tbody>
              {(data[conf] ?? []).map((t, i) => (
                <tr key={t.team_id} className={`border-t border-line ${i === 5 || i === 9 ? "border-b-2 border-b-line" : ""}`}>
                  <td className="px-5 py-2">
                    <Link href={`/teams/${t.abbreviation}`} className="flex items-center gap-2.5 hover:underline">
                      <span className="w-4 text-right text-xs text-faint">{i + 1}</span>
                      <TeamBadge abbr={t.abbreviation} size={24} />
                      <span className="font-medium">{t.full_name}</span>
                    </Link>
                  </td>
                  <td className="px-2 text-right font-semibold">{t.wins}</td>
                  <td className="px-2 text-right">{t.losses}</td>
                  <td className="px-2 text-right text-muted">{t.win_pct?.toFixed(3).replace(/^0/, "") ?? "—"}</td>
                  <td className="px-2 text-right text-muted">{t.games_behind ? t.games_behind.toFixed(1) : "—"}</td>
                  <td className={`px-5 text-right font-medium ${(t.avg_margin ?? 0) > 0 ? "text-win" : (t.avg_margin ?? 0) < 0 ? "text-loss" : ""}`}>
                    {t.avg_margin !== null ? (t.avg_margin > 0 ? `+${t.avg_margin}` : t.avg_margin) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="px-5 py-3 text-[11px] text-muted">Lines after 6th and 10th: playoff and play-in cutoffs. Diff = average point margin.</p>
        </Card>
      ))}
    </div>
  );
}

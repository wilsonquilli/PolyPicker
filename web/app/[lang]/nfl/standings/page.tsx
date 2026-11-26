import Link from "next/link";
import { Suspense } from "react";
import { Card, Skeleton, TeamBadge } from "@/components/ui";
import { api } from "@/lib/api";
import type { NflStandingRow } from "@/lib/nfl";
import { getT } from "@/lib/i18n/server";

export async function generateMetadata() {
  const t = await getT();
  return { title: t("NFL standings") };
}

export default async function NflStandingsPage() {
  const t = await getT();
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{t("NFL standings")}</h1>
        <p className="text-sm text-muted">{t("Regular season. A tie counts as half a win in the win percentage.")}</p>
      </div>
      <Suspense fallback={<div className="grid gap-6 lg:grid-cols-2"><Skeleton className="h-[600px]" /><Skeleton className="h-[600px]" /></div>}>
        <Standings />
      </Suspense>
    </div>
  );
}

async function Standings() {
  const t = await getT();
  const res = await api<Record<string, Record<string, NflStandingRow[]>>>("/nfl/standings");
  const data = res?.data ?? {};
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      {["AFC", "NFC"].filter((c) => data[c]).map((conf) => (
        <div key={conf} className="flex min-w-0 flex-col gap-4">
          <h2 className="text-lg font-semibold">{conf}</h2>
          {Object.keys(data[conf]).sort().map((div) => (
            <Card key={div} className="overflow-x-auto">
              <table className="tabular w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-[11px] uppercase tracking-wide text-muted">
                    <th className="px-4 py-2 text-left font-semibold">{div}</th>
                    <th className="px-2 py-2 text-right font-semibold">W-L-T</th>
                    <th className="px-2 py-2 text-right font-semibold">{t("Pct")}</th>
                    <th className="hidden px-2 py-2 text-right font-semibold sm:table-cell">{t("PF")}</th>
                    <th className="hidden px-2 py-2 text-right font-semibold sm:table-cell">{t("PA")}</th>
                    <th className="px-2 py-2 text-right font-semibold">{t("Diff")}</th>
                    <th className="px-4 py-2 text-right font-semibold">{t("Div")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data[conf][div].map((tm) => (
                    <tr key={tm.team_id} className="border-t border-line first:border-0">
                      <td className="px-4 py-2">
                        <Link href={`/nfl/teams/${tm.abbreviation}`} className="flex items-center gap-2.5 hover:underline">
                          <TeamBadge abbr={tm.abbreviation} size={24} league="nfl" />
                          <span className="font-medium">{tm.name}</span>
                        </Link>
                      </td>
                      <td className="px-2 py-2 text-right">{tm.wins}-{tm.losses}-{tm.ties}</td>
                      <td className="px-2 py-2 text-right">{tm.win_pct === null ? "—" : tm.win_pct.toFixed(3).replace(/^0/, "")}</td>
                      <td className="hidden px-2 py-2 text-right sm:table-cell">{tm.points_for}</td>
                      <td className="hidden px-2 py-2 text-right sm:table-cell">{tm.points_against}</td>
                      <td className={`px-2 py-2 text-right ${tm.point_diff > 0 ? "text-win" : tm.point_diff < 0 ? "text-loss" : ""}`}>{tm.point_diff > 0 ? "+" : ""}{tm.point_diff}</td>
                      <td className="px-4 py-2 text-right text-muted">{tm.div_wins}-{tm.div_losses}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          ))}
        </div>
      ))}
    </div>
  );
}
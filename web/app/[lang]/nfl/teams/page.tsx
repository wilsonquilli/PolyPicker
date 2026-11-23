import Link from "next/link";
import { Suspense } from "react";

import { Skeleton, TeamBadge } from "@/components/ui";
import { api } from "@/lib/api";
import type { NflStandingRow } from "@/lib/nfl";
import { getT } from "@/lib/i18n/server";

export async function generateMetadata() {
  const t = await getT();
  return { title: t("NFL teams") };
}

export default async function NflTeamsPage() {
  const t = await getT();
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold tracking-tight">{t("NFL teams")}</h1>
      <Suspense fallback={<Skeleton className="h-[600px]" />}>
        <Teams />
      </Suspense>
    </div>
  );
}

async function Teams() {
  const teams = (await api<NflStandingRow[]>("/nfl/teams"))?.data ?? [];
  const byDiv = new Map<string, NflStandingRow[]>();
  for (const t of teams) byDiv.set(t.division, [...(byDiv.get(t.division) ?? []), t]);
  return (
    <div className="flex flex-col gap-8">
      {[...byDiv.entries()].sort().map(([div, ts]) => (
        <section key={div}>
          <h2 className="mb-3 text-sm font-semibold text-muted">{div}</h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {ts.map((tm) => (
              <Link key={tm.team_id} href={`/nfl/teams/${tm.abbreviation}`}
                className="flex items-center gap-3 rounded-2xl border border-line bg-surface p-4 shadow-card transition hover:-translate-y-0.5 hover:border-accent/40">
                <TeamBadge abbr={tm.abbreviation} size={40} league="nfl" />
                <div className="min-w-0">
                  <div className="truncate font-semibold">{tm.name}</div>
                  <div className="tabular text-xs text-muted">{tm.record} · {tm.city}</div>
                </div>
              </Link>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

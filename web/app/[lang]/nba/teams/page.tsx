import Link from "next/link";
import { Suspense } from "react";
import { Skeleton, TeamBadge } from "@/components/ui";
import { api } from "@/lib/api";
import type { StandingRow } from "@/lib/types";
import { getT } from "@/lib/i18n/server";

export async function generateMetadata() {
  const t = await getT();
  return { title: t("Teams") };
}

export default async function TeamsPage() {
  const t = await getT();
  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold tracking-tight">{t("Teams")}</h1>
      <Suspense fallback={<div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">{Array.from({ length: 30 }).map((_, i) => <Skeleton key={i} className="h-20" />)}</div>}>
        <TeamGrid />
      </Suspense>
    </div>
  );
}

async function TeamGrid() {
  const t = await getT();
  const teams = (await api<StandingRow[]>("/teams"))?.data ?? [];
  const byDiv = new Map<string, StandingRow[]>();
  for (const row of teams) byDiv.set(`${row.conference} · ${row.division}`, [...(byDiv.get(`${row.conference} · ${row.division}`) ?? []), row]);
  return (
    <div className="flex flex-col gap-8">
      {[...byDiv.entries()].sort().map(([div, ts]) => (
        <section key={div}>
          <h2 className="mb-3 text-sm font-semibold text-muted">{div.split(" · ").map((x) => t(x)).join(" · ")}</h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {ts.map((tm) => (
              <Link key={tm.team_id} href={`/nba/teams/${tm.abbreviation}`}
                className="flex items-center gap-3 rounded-2xl border border-line bg-surface p-4 shadow-card transition hover:-translate-y-0.5 hover:border-accent/40">
                <TeamBadge abbr={tm.abbreviation} size={40} />
                <div className="min-w-0">
                  <div className="truncate font-semibold">{tm.name}</div>
                  <div className="tabular text-xs text-muted">{tm.wins}-{tm.losses} · {tm.city}</div>
                </div>
              </Link>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { SoccerBadge } from "@/components/soccer/MatchCard";
import { Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { getT } from "@/lib/i18n/server";
import { soccerLeague, type SoccerLeague, type TableRow } from "@/lib/soccer";

type LParams = Promise<{ league: string }>;
export async function generateMetadata({ params }: { params: LParams }) {
  const lg = soccerLeague((await params).league);
  const t = await getT();
  return lg ? { title: t("{league} clubs", { league: lg.name }) } : {};
}

export default function TeamsPage({ params }: { params: LParams }) {
  return <Suspense fallback={<Skeleton className="h-[540px]" />}><TeamsBody params={params} /></Suspense>;
}

async function TeamsBody({ params }: { params: LParams }) {
  const t = await getT();
  const lg = soccerLeague((await params).league);
  if (!lg) notFound();
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold tracking-tight">{t("{league} clubs", { league: lg.name })}</h1>
      <Suspense fallback={<Skeleton className="h-[500px]" />}><Teams lg={lg} /></Suspense>
    </div>
  );
}

async function Teams({ lg }: { lg: SoccerLeague }) {
  const t = await getT();
  const teams = (await api<TableRow[]>(`${lg.api}/teams`))?.data ?? [];
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {teams.map((tm) => (
        <Link key={tm.team_id} href={`${lg.base}/teams/${tm.team_id}`}
          className="flex items-center gap-3 rounded-2xl border border-line bg-surface p-4 shadow-card transition hover:-translate-y-0.5 hover:border-accent/40">
          <SoccerBadge team={tm} size={40} />
          <div className="min-w-0">
            <div className="truncate font-semibold">{tm.name}</div>
            <div className="tabular text-xs text-muted">#{tm.position} · {tm.points} {t("pts")} · {tm.won}-{tm.drawn}-{tm.lost}</div>
          </div>
        </Link>
      ))}
    </div>
  );
}

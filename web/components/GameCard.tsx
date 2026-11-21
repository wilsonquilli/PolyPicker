import Link from "next/link";

import { RelativeTime } from "@/components/client";
import { Pill, ProbBar, StatusPill, TeamBadge } from "@/components/ui";
import { seasonTypeLabel, tipoffTime } from "@/lib/format";
import type { GameSide, GameSummary } from "@/lib/types";

function TeamRow({ side, p, isFavorite, final, won }: { side: GameSide; p: number | null; isFavorite: boolean;
  final: boolean; won: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <TeamBadge abbr={side.abbreviation} size={28} />
      <div className="min-w-0 flex-1">
        <div className={`truncate text-sm ${final ? (won ? "font-semibold" : "text-muted") : "font-medium"}`}>{side.name}</div>
        {side.record && <div className="tabular text-[11px] text-faint">{side.record}</div>}
      </div>
      {final && side.score !== null && (
        <span className={`tabular mr-1 text-base ${won ? "font-bold" : "text-muted"}`}>{side.score}</span>
      )}
      {p !== null && (
        <span
          className={`tabular min-w-[52px] rounded-lg px-2 py-1 text-center text-sm font-bold ${isFavorite ? "bg-accent text-accent-fg" : "bg-surface-2 text-ink"}`}
          title={isFavorite ? "Model favorite" : "Model underdog"}
        >
          {Math.round(p * 100)}%
        </span>
      )}
    </div>
  );
}

export function GameCard({ game }: { game: GameSummary }) {
  const pred = game.prediction;
  const final = game.status === "final";
  const pHome = pred?.p_home_win ?? null;
  const homeFav = pred ? pred.favorite === "home" : false;
  const pickTeam = pred ? (homeFav ? game.home : game.away) : null;
  const pickCorrect = final && pred && game.winner_team_id !== null ? pickTeam?.team_id === game.winner_team_id : null;

  return (
    <Link
      href={`/games/${game.game_id}`}
      className="group flex flex-col gap-3 rounded-2xl border border-line bg-surface p-4 shadow-card transition hover:-translate-y-0.5 hover:border-accent/40 hover:shadow-md"
    >
      <div className="flex items-center justify-between gap-2 text-[11px] text-muted">
        <div className="flex items-center gap-1.5 truncate">
          <span className="font-semibold text-ink">NBA</span>
          <span>·</span>
          <span className="truncate">{game.is_cup && game.season_type === "regular" ? "NBA Cup" : seasonTypeLabel(game.season_type)}</span>
          {game.sub_label && <span className="truncate">· {game.sub_label}</span>}
        </div>
        {final || game.status !== "scheduled" ? <StatusPill status={game.status} /> : <span className="tabular font-medium">{tipoffTime(game.tipoff_utc)}</span>}
      </div>

      <div className="flex flex-col gap-2">
        <TeamRow side={game.away} p={pred ? pred.p_away_win : null} isFavorite={!!pred && !homeFav} final={final}
          won={game.winner_team_id === game.away.team_id} />
        <TeamRow side={game.home} p={pHome} isFavorite={homeFav} final={final}
          won={game.winner_team_id === game.home.team_id} />
      </div>

      {pred ? (
        <ProbBar home={pred.p_home_win} homeAbbr={game.home.abbreviation} awayAbbr={game.away.abbreviation} />
      ) : (
        <div className="h-1.5 rounded-full bg-surface-2" />
      )}

      <div className="mt-auto flex items-center justify-between gap-2 text-[11px] text-muted">
        {pred ? (
          <span className="truncate">
            {pred.projected_home_points !== null && pred.projected_away_points !== null
              ? `Proj. ${Math.round(pred.projected_away_points)}–${Math.round(pred.projected_home_points)}`
              : "Projection pending"}
            {" · "}
            <span className="capitalize">{pred.favorite_label}</span>
          </span>
        ) : (
          <span>{game.status === "scheduled" ? "Forecast not published yet" : "No forecast"}</span>
        )}
        {pickCorrect !== null ? (
          <Pill tone={pickCorrect ? "win" : "loss"}>{pickCorrect ? "✓ Model pick" : "✗ Model pick"}</Pill>
        ) : pred ? (
          <span className="shrink-0">
            {pred.kind === "backtest" ? "Backtest" : <RelativeTime iso={pred.published_at} prefix="Updated " />}
          </span>
        ) : null}
      </div>
    </Link>
  );
}

export function GameCardSkeleton() {
  return (
    <div className="flex h-[188px] flex-col gap-3 rounded-2xl border border-line bg-surface p-4">
      <div className="h-3 w-1/2 animate-pulse rounded bg-surface-2" />
      <div className="h-7 animate-pulse rounded bg-surface-2" />
      <div className="h-7 animate-pulse rounded bg-surface-2" />
      <div className="h-1.5 animate-pulse rounded bg-surface-2" />
    </div>
  );
}

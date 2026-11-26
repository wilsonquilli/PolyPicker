import Link from "next/link";
import type { T } from "@/lib/i18n";
import { getLang, getT } from "@/lib/i18n/server";

import { UpdatedAt } from "@/components/client";
import { ReasonLine } from "@/components/Reasons";
import { Pill, ProbBar, StatusPill, TeamBadge } from "@/components/ui";
import { seasonTypeLabel, tipoffTime } from "@/lib/format";
import { kickoff } from "@/lib/nfl";
import { lastUpdated, type GameSide, type GameSummary } from "@/lib/types";

function TeamRow({ side, p, isFavorite, final, won, league, t }: { side: GameSide; p: number | null; isFavorite: boolean; t: T;
  final: boolean; won: boolean; league: "nba" | "nfl" }) {
  return (
    <div className="flex items-center gap-2.5">
      <TeamBadge abbr={side.abbreviation} size={28} league={league} />
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
          title={isFavorite ? t("Model favorite") : t("Model underdog")}
        >
          {Math.round(p * 100)}%
        </span>
      )}
    </div>
  );
}

/** Works for both leagues: NFL games (league="nfl") link to /nfl, show the week and the kickoff day. */
export async function GameCard({ game, league = "nba" }: { game: GameSummary; league?: "nba" | "nfl" }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const pred = game.prediction;
  const final = game.status === "final";
  const pHome = pred?.p_home_win ?? null;
  const homeFav = pred ? pred.favorite === "home" : false;
  const pickTeam = pred ? (homeFav ? game.home : game.away) : null;
  const tie = final && league === "nfl" && game.winner_team_id === null && game.home.score !== null;
  const pickCorrect = final && pred && game.winner_team_id !== null ? pickTeam?.team_id === game.winner_team_id : null;

  return (
    <Link
      href={`/${league}/games/${game.game_id}`}
      className="group flex flex-col gap-3 rounded-2xl border border-line bg-surface p-4 shadow-card transition hover:-translate-y-0.5 hover:border-accent/40 hover:shadow-md"
    >
      <div className="flex items-center justify-between gap-2 text-[11px] text-muted">
        <div className="flex items-center gap-1.5 truncate">
          <span className="font-semibold text-ink">{league.toUpperCase()}</span>
          <span>·</span>
          <span className="truncate">{league === "nfl" ? t(game.label ?? "") : game.is_cup && game.season_type === "regular" ? t("NBA Cup") : t(seasonTypeLabel(game.season_type))}</span>
          {game.sub_label && <span className="truncate">· {t(game.sub_label)}</span>}
        </div>
        {final || game.status !== "scheduled" ? <StatusPill status={game.status} /> : <span className="tabular font-medium">{league === "nfl" ? kickoff(game.tipoff_utc, lang) : tipoffTime(game.tipoff_utc, lang)}</span>}
      </div>

      <div className="flex flex-col gap-2">
        <TeamRow side={game.away} p={pred ? pred.p_away_win : null} isFavorite={!!pred && !homeFav} final={final}
          won={game.winner_team_id === game.away.team_id} league={league} t={t} />
        <TeamRow side={game.home} p={pHome} isFavorite={homeFav} final={final}
          won={game.winner_team_id === game.home.team_id} league={league} t={t} />
      </div>

      {pred ? (
        <ProbBar home={pred.p_home_win} homeAbbr={game.home.abbreviation} awayAbbr={game.away.abbreviation} />
      ) : (
        <div className="h-1.5 rounded-full bg-surface-2" />
      )}

      {pred && !final && <ReasonLine reasons={game.reasons} pick={pred.favorite} teams={{ home: game.home.name, away: game.away.name }} t={t} lang={lang} />}

      <div className="mt-auto flex items-center justify-between gap-2 text-[11px] text-muted">
        {pred ? (
          <span className="truncate">
            {pred.projected_home_points !== null && pred.projected_away_points !== null
              ? `${t("Proj.")} ${Math.round(pred.projected_away_points)}–${Math.round(pred.projected_home_points)}`
              : t("Projection pending")}
            {" · "}
            <span className="capitalize">{t(pred.favorite_label)}</span>
          </span>
        ) : (
          <span>{game.status === "scheduled" ? t("Forecast not published yet") : t("No forecast")}</span>
        )}
        {tie ? <Pill>{t("Tie · push")}</Pill> : pickCorrect !== null ? (
          <Pill tone={pickCorrect ? "win" : "loss"}>{pickCorrect ? `✓ ${t("Model pick")}` : `✗ ${t("Model pick")}`}</Pill>
        ) : pred ? (
          <span className="shrink-0">
            {pred.kind === "backtest" ? t("Backtest") : <UpdatedAt iso={lastUpdated(pred)} checked={pred.checked_at} odds={pred.odds_at} />}
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

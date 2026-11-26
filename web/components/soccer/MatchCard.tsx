import Link from "next/link";
import { getLang, getT } from "@/lib/i18n/server";

import { UpdatedAt } from "@/components/client";
import { lastUpdated } from "@/lib/types";
import { ReasonLine } from "@/components/Reasons";
import { Pill, StatusPill, TeamBadge, TriBar } from "@/components/ui";
import { decidedText, isStrongPick, kickoffTime, saferPicks, SOCCER_LEAGUES, stageText, teamCss, type SoccerGame, type SoccerSide } from "@/lib/soccer";

export function SoccerBadge({ team, size = 28 }: { team: Pick<SoccerSide, "abbreviation" | "color" | "alt_color" | "team_id">; size?: number }) {
  return <TeamBadge abbr={team.abbreviation} size={size} league="soccer" color={teamCss(team)} logoId={team.team_id} />;
}

function Row({ side, final, won }: { side: SoccerSide; final: boolean; won: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <SoccerBadge team={side} />
      <div className="min-w-0 flex-1">
        <div className={`truncate text-sm ${final ? (won ? "font-semibold" : "text-muted") : "font-medium"}`}>{side.short_name ?? side.name}</div>
        {side.record && <div className="tabular text-[11px] text-faint">{side.record}</div>}
      </div>
      {final && side.score !== null && <span className={`tabular mr-1 text-base ${won ? "font-bold" : "text-muted"}`}>{side.score}</span>}
    </div>
  );
}

/** Who won the match: the shootout or extra-time winner if it went that far, else the 90-minute result. */
export function matchWinner(g: SoccerGame): "home" | "away" | null {
  if (g.status !== "final") return null;
  if (g.decided === "pen" && g.shootout) return g.shootout.home > g.shootout.away ? "home" : "away";
  if (g.decided && g.home.score !== null && g.away.score !== null) return g.home.score > g.away.score ? "home" : g.away.score > g.home.score ? "away" : null;
  return g.result === "H" ? "home" : g.result === "A" ? "away" : null;
}

/** Match card: three outcomes (home / draw / away), the model's most likely one highlighted. */
export async function MatchCard({ game }: { game: SoccerGame }) {
  const [t, lang] = await Promise.all([getT(), getLang()]);
  const p = game.prediction;
  const final = game.status === "final";
  const pick = p?.pick;
  // Coin-flip matches (no result rated >= STRONG_PICK) get no strong pick: the likeliest result is only outlined.
  const strong = p ? isStrongPick(p) : false;
  const safer = p ? saferPicks(p) : null;
  const favAbbr = safer ? game[safer.fav].abbreviation : "";
  // Model picks are graded on the 90-minute result; the bold "winner" is who actually won (after extra time / pens).
  const correct = final && p && game.result ? ({ home: "H", draw: "D", away: "A" } as const)[p.pick] === game.result : null;
  const winner = matchWinner(game);
  const stage = stageText(game, t);
  const extra = final ? decidedText(game, t) : null;
  const chip = (k: "home" | "draw" | "away", label: string, v: number) => (
    <div className={`flex flex-col items-center rounded-lg px-2 py-1 ${pick === k ? (strong ? "bg-accent text-accent-fg" : "bg-surface-2 ring-1 ring-inset ring-accent/50") : "bg-surface-2"}`}>
      <span className={`text-[10px] font-medium ${pick === k && strong ? "opacity-90" : "text-muted"}`}>{label}</span>
      <span className="tabular text-sm font-bold">{Math.round(v * 100)}%</span>
    </div>
  );
  return (
    <Link href={`/soccer/${game.league}/games/${game.game_id}`}
      className="group flex flex-col gap-3 rounded-2xl border border-line bg-surface p-4 shadow-card transition hover:-translate-y-0.5 hover:border-accent/40 hover:shadow-md">
      <div className="flex items-center justify-between gap-2 text-[11px] text-muted">
        <span className="truncate"><span className="font-semibold text-ink">{SOCCER_LEAGUES[game.league]?.short ?? t("Soccer")}</span> · {stage ?? t(game.season_label)}</span>
        {game.status !== "scheduled" ? <StatusPill status={game.status} /> : <span className="tabular font-medium">{kickoffTime(game.tipoff_utc, lang)}</span>}
      </div>
      <div className="flex flex-col gap-2">
        <Row side={game.home} final={final} won={winner === "home"} />
        <Row side={game.away} final={final} won={winner === "away"} />
        {extra && <div className="text-[11px] text-muted">{extra}{game.score_90 ? ` · 90 min ${game.score_90.home}–${game.score_90.away}` : ""}</div>}
      </div>
      {p ? (
        <>
          <div className="grid grid-cols-3 gap-1.5">
            {chip("home", game.home.abbreviation, p.p_home)}{chip("draw", t("Draw"), p.p_draw)}{chip("away", game.away.abbreviation, p.p_away)}
          </div>
          <TriBar home={p.p_home} draw={p.p_draw} away={p.p_away} labels={[game.home.abbreviation, game.away.abbreviation]} />
          {safer && (
            <div className="tabular flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted">
              <span>{t("Double chance")}: <span className="font-medium text-ink">{t("{team} or draw", { team: favAbbr })}</span> {Math.round(safer.doubleChance * 100)}%</span>
              <span>{t("Draw no bet")}: <span className="font-medium text-ink">{favAbbr}</span> {Math.round(safer.drawNoBet * 100)}%</span>
            </div>
          )}
          {game.status === "scheduled" && (
            <ReasonLine reasons={game.reasons} pick={p.pick} teams={{ home: game.home.short_name ?? game.home.name, away: game.away.short_name ?? game.away.name }} t={t} lang={lang} />
          )}
        </>
      ) : <div className="h-1.5 rounded-full bg-surface-2" />}
      <div className="mt-auto flex items-center justify-between gap-2 text-[11px] text-muted">
        <span className="truncate">
          {p?.xg_home !== undefined && p?.xg_away !== undefined ? `${t("Exp. goals")} ${p.xg_home.toFixed(1)}–${p.xg_away.toFixed(1)}`
            : game.status === "scheduled" ? t("Forecast not published yet") : t("No forecast")}
        </span>
        {p && !strong && (correct !== null || game.status === "scheduled") ? <span className="shrink-0">{t("No strong pick")}</span>
          : correct !== null ? <Pill tone={correct ? "win" : "loss"}>{correct ? `✓ ${t("Model pick")}` : `✗ ${t("Model pick")}`}</Pill>
          : p ? <span className="shrink-0">{p.kind === "backtest" ? t("Backtest") : <UpdatedAt iso={lastUpdated(p)} checked={p.checked_at} odds={p.odds_at} />}</span> : null}
      </div>
    </Link>
  );
}

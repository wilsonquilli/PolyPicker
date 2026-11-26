"""Player-based team strength and availability (point-in-time).

Each player carries two per-minute impact ratings built only from earlier games:

* ``gs``  -- Hollinger game score per minute (box-score production)
* ``pm``  -- plus-minus per minute (on-court results)
* ``apm`` -- adjusted plus-minus (polypicker/ml/apm.py): margin impact net of teammates and opponents

Both are recency-weighted (older games decay), carried across seasons at reduced weight, and
shrunk toward replacement level until the player has enough minutes. A team's strength for a
game is the minutes-weighted rating of the players expected to play, scaled to 240 team minutes;
"missing" measures how much of the usual rotation is out.

Who is expected to play:
* final games (training / backtests): the players who actually played. That mirrors the
  official injury report, which is settled shortly before tip-off -- the forecast refresh that
  matters. Minutes are always the *pregame* expectation, never the actual minutes.
* upcoming games: the injury report (``availability`` callback: player -> weight 0..1), falling
  back to recent box-score status (inactive in each of the team's last 2 games -> out).

Same day-batch discipline as the team features: a day's features are computed before any of
that day's results are applied.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import polars as pl
from sqlalchemy import Engine

from polypicker.ml.apm import UNRATED, apm_ratings
from polypicker.ml.data import COMPETITIVE, _read

DECAY = 0.965             # per game the player plays (half-life ~20 games)
SEASON_CARRY = 0.5        # weight kept from earlier seasons at a season change
GS_PRIOR_MIN = 250.0      # minutes of replacement-level prior
PM_PRIOR_MIN = 500.0      # plus-minus is noisier: heavier prior
REPL_GS_FACTOR = 0.7      # replacement level = 70% of league-average game score per minute
REPL_PM_PER_MIN = -0.06   # about -2.9 per 48 minutes
MIN_EWM_ALPHA = 0.25
DEFAULT_MIN = 12.0        # expected minutes for a player with no recent history
MAX_MIN = 42.0
ROSTER_LOOKBACK = 15      # a player belongs to a team's rotation if he played in its last 15 games
TEAM_MINUTES = 240.0
PM_MODE = "raw"            # "raw" plus-minus, or "relative" to the team's margin (teammate-adjusted)

LINEUP_FEATURES = ["lineup_pm", "lineup_gs", "full_pm", "missing_pm", "missing_min", "lineup_apm", "full_apm", "missing_apm"]

AvailabilityFn = Callable[[int, int, int], float | None]  # (game_id, team_id, player_id) -> weight or None


def load_player_impact_box(engine: Engine) -> pl.DataFrame:
    return _read(engine, f"""
        SELECT p.game_id, p.player_id, p.team_id, p.participation, p.minutes, p.points, p.fgm, p.fga, p.ftm, p.fta,
               p.oreb, p.dreb, p.assists, p.steals, p.blocks, p.turnovers, p.fouls, p.plus_minus
        FROM player_game_stats p JOIN games g USING (game_id)
        WHERE g.season_type IN {COMPETITIVE} AND g.status = 'final'""",
                 schema_overrides={"minutes": pl.Float64})


def game_score(b: dict) -> float:
    v = lambda k: b.get(k) or 0  # noqa: E731
    return (v("points") + 0.4 * v("fgm") - 0.7 * v("fga") - 0.4 * (v("fta") - v("ftm")) + 0.7 * v("oreb")
            + 0.3 * v("dreb") + v("steals") + 0.7 * v("assists") + 0.7 * v("blocks") - 0.4 * v("fouls")
            - v("turnovers"))


@dataclass
class _P:
    team: int | None = None
    season: int | None = None
    last_idx: int = -10_000           # team-game index of the last appearance for `team`
    min_ewm: float | None = None
    gs: float = 0.0
    pm: float = 0.0
    mins: float = 0.0
    status: dict = field(default_factory=dict)  # game_id -> participation
    apm: float = UNRATED                        # today's adjusted plus-minus per 48 (set before each day)

    def gs_rate(self, repl: float) -> float:
        return (self.gs + GS_PRIOR_MIN * repl) / (self.mins + GS_PRIOR_MIN)

    def pm_rate(self) -> float:
        return (self.pm + PM_PRIOR_MIN * REPL_PM_PER_MIN) / (self.mins + PM_PRIOR_MIN)


def _strength(players: list[tuple[_P, float]], repl_gs: float) -> tuple[float, float, float]:
    """Minutes-weighted ratings (per 48 minutes) of the players expected to play: (pm, gs, apm)."""
    mins = [(p, w * (p.min_ewm if p.min_ewm is not None else DEFAULT_MIN)) for p, w in players]
    total = sum(m for _, m in mins)
    if total < 1:
        return 48 * REPL_PM_PER_MIN, 48 * repl_gs, 48 * REPL_PM_PER_MIN
    scale = TEAM_MINUTES / total
    pm = gs = apm = 0.0
    for p, m in mins:
        m = min(m * scale, MAX_MIN)
        pm += m * p.pm_rate()
        gs += m * p.gs_rate(repl_gs)
        apm += m * p.apm / 48
    used = sum(min(m * scale, MAX_MIN) for _, m in mins)
    # minutes lost to the cap are played by a replacement-level player
    pm += (TEAM_MINUTES - used) * REPL_PM_PER_MIN
    gs += (TEAM_MINUTES - used) * repl_gs
    apm += (TEAM_MINUTES - used) * REPL_PM_PER_MIN
    return 48 * pm / TEAM_MINUTES, 48 * gs / TEAM_MINUTES, 48 * apm / TEAM_MINUTES


def build_lineup_features(games: pl.DataFrame, box: pl.DataFrame, availability: AvailabilityFn | None = None,
                          *, pregame_only: bool = False, apm: dict | None = None) -> pl.DataFrame:
    """One row per (game_id, team_id): lineup strength, full-strength rating, what's missing, who's out.

    ``pregame_only``: treat finished games like upcoming ones (``availability`` / box-score fallback instead
    of who actually played) -- an honest replay of what was knowable before tip-off, for evaluation."""
    apm = apm if apm is not None else apm_ratings(games, box)
    by_game: dict[int, list[dict]] = defaultdict(list)
    for r in box.iter_rows(named=True):
        by_game[r["game_id"]].append(r)
    ps: dict[int, _P] = defaultdict(_P)
    team_idx: dict[int, int] = defaultdict(int)          # final games played so far, per team
    team_games: dict[int, list[int]] = defaultdict(list)
    members: dict[int, set[int]] = defaultdict(set)
    lg_gs = lg_min = 0.0
    out: list[dict] = []

    margin = {}
    for g in games.filter(pl.col("status") == "final").iter_rows(named=True):
        margin[(g["game_id"], g["home_team_id"])] = g["home_score"] - g["away_score"]
        margin[(g["game_id"], g["away_team_id"])] = g["away_score"] - g["home_score"]

    for (day,), day_games in games.sort("tipoff_utc").group_by(["game_date_et"], maintain_order=True):
        rows = list(day_games.iter_rows(named=True))
        repl = REPL_GS_FACTOR * (lg_gs / lg_min if lg_min > 5000 else 0.45)
        today = apm.get(day, {})
        for g in rows:
            gid, final = g["game_id"], g["status"] == "final"
            played = {b["player_id"]: b for b in by_game.get(gid, []) if b["participation"] == "played"}
            for team in (g["home_team_id"], g["away_team_id"]):
                roster = [pid for pid in members[team] if team_idx[team] - ps[pid].last_idx <= ROSTER_LOOKBACK]
                last2 = team_games[team][-2:]
                weights: dict[int, float] = {}
                reasons: dict[int, str] = {}
                if final and played and not pregame_only:
                    for pid in roster:
                        weights[pid] = 1.0 if pid in played else 0.0
                    for pid, b in played.items():
                        if b["team_id"] == team and pid not in weights:
                            weights[pid] = 1.0
                else:
                    for pid in roster:
                        w = availability(gid, team, pid) if availability else None
                        if w is None:
                            gone = len(last2) == 2 and all(ps[pid].status.get(x) == "inactive" for x in last2)
                            w = 0.0 if gone else 1.0
                            if gone:
                                reasons[pid] = "inactive the last 2 games"
                        else:
                            reasons[pid] = "injury report"
                        weights[pid] = w
                avail = [(ps[pid], w) for pid, w in weights.items() if w > 0]
                full = [(ps[pid], 1.0) for pid in roster]
                for pid in weights:
                    ps[pid].apm = today.get(pid, UNRATED)  # point-in-time rating for this game day
                l_pm, l_gs, l_apm = _strength(avail, repl)
                f_pm, f_gs, f_apm = _strength(full, repl) if full else (l_pm, l_gs, l_apm)
                exp = {pid: ps[pid].min_ewm or DEFAULT_MIN for pid in roster}
                tot = sum(exp.values())
                miss = sum(exp[pid] * (1 - weights.get(pid, 1.0)) for pid in roster) / tot if tot else 0.0
                outs = sorted((pid for pid in roster if weights.get(pid, 1.0) < 1.0), key=lambda p: -exp[p])
                out.append({"game_id": gid, "team_id": team, "lineup_pm": l_pm, "lineup_gs": l_gs,
                            "full_pm": f_pm, "missing_pm": f_pm - l_pm, "missing_min": miss,
                            "lineup_apm": l_apm, "full_apm": f_apm, "missing_apm": f_apm - l_apm,
                            "out_players": [{"player_id": pid, "exp_minutes": round(exp[pid], 1),
                                             "pm48": round(48 * ps[pid].pm_rate(), 1),
                                             "weight": round(weights.get(pid, 1.0), 2),
                                             "reason": reasons.get(pid)} for pid in outs[:6]] if not final else []})
        # apply today's results
        for g in rows:
            if g["status"] != "final":
                continue
            gid, season = g["game_id"], g["season"]
            for team in (g["home_team_id"], g["away_team_id"]):
                team_idx[team] += 1
                team_games[team].append(gid)
            for b in by_game.get(gid, []):
                p = ps[b["player_id"]]
                team = b["team_id"]
                if p.season != season:
                    if p.season is not None:
                        p.gs *= SEASON_CARRY; p.pm *= SEASON_CARRY; p.mins *= SEASON_CARRY
                    p.season = season
                p.status[gid] = b["participation"]
                if p.team != team:
                    if p.team is not None:
                        members[p.team].discard(b["player_id"])
                    p.team = team
                members[team].add(b["player_id"])
                if b["participation"] == "dnp" and p.min_ewm is not None:
                    p.min_ewm *= 1 - MIN_EWM_ALPHA  # coach's decision: out of the rotation, not injured
                if b["participation"] != "played" or not b["minutes"]:
                    continue
                m = float(b["minutes"])
                p.last_idx = team_idx[team]
                p.min_ewm = m if p.min_ewm is None else MIN_EWM_ALPHA * m + (1 - MIN_EWM_ALPHA) * p.min_ewm
                gs = game_score(b)
                p.gs = DECAY * p.gs + gs
                pm = b["plus_minus"] or 0
                if PM_MODE == "relative":
                    pm -= margin.get((gid, team), 0) * m / 48
                p.pm = DECAY * p.pm + pm
                p.mins = DECAY * p.mins + m
                lg_gs += gs
                lg_min += m

    return pl.DataFrame(out, infer_schema_length=None)


def add_lineup(design_df: pl.DataFrame, lineup: pl.DataFrame) -> pl.DataFrame:
    """Join own + opponent lineup features onto the per-team-perspective design frame."""
    cols = LINEUP_FEATURES
    own = lineup.select("game_id", "team_id", *cols)
    opp = lineup.select("game_id", pl.col("team_id").alias("opp_team_id"), *[pl.col(c).alias(f"opp_{c}") for c in cols])
    df = design_df.join(own, on=["game_id", "team_id"], how="left").join(opp, on=["game_id", "opp_team_id"], how="left")
    return df.with_columns((pl.col("lineup_pm") - pl.col("opp_lineup_pm")).alias("lineup_pm_diff"))

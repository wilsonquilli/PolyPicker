"""Point-in-time team features (FR-05.3, NFR-09.1).

Games are processed one ET calendar day at a time: features for every game on a day are
computed *before* any result from that day is applied. So a game's features only use results
from earlier days -- exactly what was knowable at the start of game day. Upcoming games get
features from every final game before them, which is the live-prediction case.

Rest days come from the *schedule* (known in advance), not from results.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date

import polars as pl

from polypicker.ml.venues import ALTITUDE_M, ARENAS, km, venue

LEAGUE_ORTG_SEED = 114.5   # points per 100 possessions, recent-era league average
LEAGUE_PACE_SEED = 99.0
PRIOR_GAMES = 8            # weight (in games) of the prior when blending early-season stats
PRIOR_REGRESS = 0.3        # how far last season's rating is pulled toward league average

ELO_INIT, ELO_K, ELO_HFA, ELO_CARRY = 1500.0, 20.0, 70.0, 0.75


def possessions(fga: float, oreb: float, tov: float, fta: float) -> float:
    return fga - oreb + tov + 0.44 * fta


@dataclass
class _Game:
    d: date
    pts: float
    opp_pts: float
    poss: float  # average of both teams' possession estimates


@dataclass
class _TeamState:
    elo: float = ELO_INIT
    season: int | None = None
    games: list[_Game] = field(default_factory=list)          # current season
    recent: deque = field(default_factory=lambda: deque(maxlen=10))
    prior_ortg: float | None = None
    prior_drtg: float | None = None
    prior_pace: float | None = None


class _League:
    def __init__(self):
        self.pts = defaultdict(float)
        self.poss = defaultdict(float)
        self.games = defaultdict(int)

    def ortg(self, season: int) -> float:
        for s in (season, season - 1):
            if self.poss[s] > 2000:
                return 100 * self.pts[s] / self.poss[s]
        return LEAGUE_ORTG_SEED

    def pace(self, season: int) -> float:
        for s in (season, season - 1):
            if self.games[s] > 20:
                return self.poss[s] / self.games[s] / 2  # two team-entries per game
        return LEAGUE_PACE_SEED


def _rtg(games, attr) -> float | None:
    poss = sum(g.poss for g in games)
    return 100 * sum(getattr(g, attr) for g in games) / poss if poss else None


def _blend(n: int, season_val: float | None, prior: float) -> float:
    if season_val is None or n == 0:
        return prior
    return (n * season_val + PRIOR_GAMES * prior) / (n + PRIOR_GAMES)


def elo_win_prob(diff: float) -> float:
    return 1 / (1 + 10 ** (-diff / 400))


# Travel / schedule-density features (travel_km ... visitor_altitude) were tested for game model v2 on the
# 2024-25 and 2025-26 hold-outs (2026-11-21) and did not improve accuracy or log loss beyond rest days and
# back-to-backs, so the game model doesn't use them. They stay available as context.
TEAM_FEATURES = ["ortg", "drtg", "pace", "ortg_l10", "drtg_l10", "net_l10", "elo", "rest_days", "b2b",
                 "season_games", "travel_km", "tz_shift", "games_l6", "three_in_four", "road_trip", "visitor_altitude"]


def build_team_features(games: pl.DataFrame, team_box: pl.DataFrame) -> pl.DataFrame:
    """One row per (game_id, team_id) with that team's pregame state, plus league context."""
    box = {(r["game_id"], r["team_id"]): r for r in team_box.iter_rows(named=True)}
    states: dict[int, _TeamState] = defaultdict(_TeamState)
    league = _League()
    last_sched: dict[int, date] = {}
    # travel / schedule density (from the schedule, known in advance)
    last_venue: dict[int, tuple] = {}
    recent_dates: dict[int, deque] = defaultdict(lambda: deque(maxlen=8))
    away_streak: dict[int, int] = defaultdict(int)
    out: list[dict] = []

    for (day,), day_games in games.sort("tipoff_utc").group_by(["game_date_et"], maintain_order=True):
        rows = list(day_games.iter_rows(named=True))
        # 1) pregame features for every game today
        for g in rows:
            season = g["season"]
            lg_ortg, lg_pace = league.ortg(season), league.pace(season)
            for team, opp, is_home in ((g["home_team_id"], g["away_team_id"], True),
                                       (g["away_team_id"], g["home_team_id"], False)):
                st = states[team]
                if st.season != season:
                    _roll_season(st, season, lg_ortg, lg_pace)
                n = len(st.games)
                prior_o = st.prior_ortg if st.prior_ortg is not None else lg_ortg
                prior_d = st.prior_drtg if st.prior_drtg is not None else lg_ortg
                prior_p = st.prior_pace if st.prior_pace is not None else lg_pace
                ortg = _blend(n, _rtg(st.games, "pts"), prior_o)
                drtg = _blend(n, _rtg(st.games, "opp_pts"), prior_d)
                pace = _blend(n, (sum(x.poss for x in st.games) / n) if n else None, prior_p)
                rec = list(st.recent)
                o10 = _rtg(rec, "pts") if rec else ortg
                d10 = _rtg(rec, "opp_pts") if rec else drtg
                prev = last_sched.get(team)
                rest = min((day - prev).days, 4) if prev else 4
                here = venue(g["home_team_id"], g.get("arena_city"))
                before = last_venue.get(team)
                fresh = prev is None or (day - prev).days > 10  # season start / long break: no travel carry-over
                travel = km(before, here) if (here and before and not fresh) else 0.0
                tz = (here[2] - before[2]) if (here and before and not fresh) else 0.0
                dates = recent_dates[team]
                g6 = sum(1 for d0 in dates if 0 < (day - d0).days <= 6)
                g3 = sum(1 for d0 in dates if 0 < (day - d0).days <= 3)
                home_alt = ARENAS.get(team, (0, 0, 0, 0))[3]
                trip = 0 if is_home else away_streak[team] + 1
                out.append({"game_id": g["game_id"], "team_id": team, "opp_team_id": opp,
                            "is_home": is_home, "season": season, "game_date_et": day,
                            "status": g["status"], "ortg": ortg, "drtg": drtg, "pace": pace,
                            "ortg_l10": o10, "drtg_l10": d10, "net_l10": o10 - d10, "elo": st.elo,
                            "rest_days": rest, "b2b": rest == 1, "season_games": n,
                            "travel_km": travel, "tz_shift": tz, "games_l6": g6, "three_in_four": g3 >= 2,
                            "road_trip": min(trip, 8),
                            "visitor_altitude": bool(here and here[3] > ALTITUDE_M and home_alt < ALTITUDE_M),
                            "league_ortg": lg_ortg, "league_pace": lg_pace})
        # 2) apply today's results
        for g in rows:
            here = venue(g["home_team_id"], g.get("arena_city"))
            for t in (g["home_team_id"], g["away_team_id"]):
                last_sched[t] = day
                recent_dates[t].append(day)
                if here:
                    last_venue[t] = here
            away_streak[g["home_team_id"]] = 0
            away_streak[g["away_team_id"]] += 1
            if g["status"] != "final":
                continue
            h, a = box.get((g["game_id"], g["home_team_id"])), box.get((g["game_id"], g["away_team_id"]))
            if not h or not a or any(v is None for v in (h["fga"], a["fga"])):
                continue
            ph = possessions(h["fga"], h["oreb"], h["tov"], h["fta"])
            pa = possessions(a["fga"], a["oreb"], a["tov"], a["fta"])
            poss = (ph + pa) / 2
            hs, as_ = g["home_score"], g["away_score"]
            states[g["home_team_id"]].games.append(_Game(day, hs, as_, poss))
            states[g["away_team_id"]].games.append(_Game(day, as_, hs, poss))
            states[g["home_team_id"]].recent.append(_Game(day, hs, as_, poss))
            states[g["away_team_id"]].recent.append(_Game(day, as_, hs, poss))
            league.pts[g["season"]] += hs + as_
            league.poss[g["season"]] += 2 * poss
            league.games[g["season"]] += 1
            _update_elo(states[g["home_team_id"]], states[g["away_team_id"]], hs - as_)

    return pl.DataFrame(out, infer_schema_length=None)


def _roll_season(st: _TeamState, season: int, lg_ortg: float, lg_pace: float) -> None:
    if st.games:
        o, d = _rtg(st.games, "pts"), _rtg(st.games, "opp_pts")
        p = sum(x.poss for x in st.games) / len(st.games)
        st.prior_ortg = (1 - PRIOR_REGRESS) * o + PRIOR_REGRESS * lg_ortg
        st.prior_drtg = (1 - PRIOR_REGRESS) * d + PRIOR_REGRESS * lg_ortg
        st.prior_pace = (1 - PRIOR_REGRESS) * p + PRIOR_REGRESS * lg_pace
    if st.season is not None:
        st.elo = ELO_CARRY * st.elo + (1 - ELO_CARRY) * ELO_INIT
    st.season = season
    st.games = []


def _update_elo(home: _TeamState, away: _TeamState, margin: float) -> None:
    diff = home.elo + ELO_HFA - away.elo
    p = elo_win_prob(diff)
    won = 1.0 if margin > 0 else 0.0
    # FiveThirtyEight-style margin-of-victory multiplier (dampened for favorites).
    mult = ((abs(margin) + 3) ** 0.8) / (7.5 + 0.006 * (diff if won else -diff))
    delta = ELO_K * mult * (won - p)
    home.elo += delta
    away.elo -= delta


def matchup_frame(team_feats: pl.DataFrame) -> pl.DataFrame:
    """Join each team row with its opponent's row -> one row per team-perspective."""
    opp = team_feats.select("game_id", pl.col("team_id").alias("opp_team_id"),
                            *[pl.col(c).alias(f"opp_{c}") for c in TEAM_FEATURES])
    df = team_feats.join(opp, on=["game_id", "opp_team_id"])
    return df.with_columns(
        ((pl.col("pace") + pl.col("opp_pace")) / 2).alias("exp_pace"),
        (pl.col("elo") - pl.col("opp_elo") + pl.when(pl.col("is_home")).then(ELO_HFA).otherwise(-ELO_HFA))
        .alias("elo_diff"),
    ).with_columns(
        (pl.col("exp_pace") * (pl.col("ortg") + pl.col("opp_drtg")) / 200).alias("struct_pts"),
        (pl.col("exp_pace") * (pl.col("ortg_l10") + pl.col("opp_drtg_l10")) / 200).alias("struct_pts_l10"),
        (pl.col("rest_days") - pl.col("opp_rest_days")).alias("rest_diff"),
    )

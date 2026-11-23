"""Point-in-time soccer features. Matches are processed one calendar date at a time: features for a date's
matches use only results from earlier dates (same discipline as the NBA/NFL).

Per team: exponentially weighted goals / shots / shots on target for and against, season averages blended
with a regressed prior, an Elo rating with a goal-difference multiplier, rest days and a promoted-team flag.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import polars as pl

EW_ALPHA = 0.12            # weight of the newest match in the exponentially weighted averages
PRIOR_GAMES = 6            # games of prior weight when blending season averages
CARRY = 0.65               # share of last season's form carried into the new season
ELO_INIT, ELO_K, ELO_HFA = 1500.0, 20.0, 60.0
PROMOTED_ELO = 1420.0
LG_GOALS, LG_SHOTS, LG_SOT = 1.40, 12.5, 4.4   # per team per match seeds (overwritten by league data)
METRICS = ("gf", "ga", "sf", "sa", "tf", "ta")  # goals, shots, shots on target (for / against)


def elo_expect(diff: float) -> float:
    return 1 / (1 + 10 ** (-diff / 400))


@dataclass
class _Team:
    elo: float = ELO_INIT
    season: int | None = None
    ew: dict = field(default_factory=dict)
    tot: dict = field(default_factory=lambda: defaultdict(float))
    n: int = 0
    last_date = None
    seasons_seen: set = field(default_factory=set)


class _League:
    def __init__(self):
        self.goals = defaultdict(float)
        self.shots = defaultdict(float)
        self.sot = defaultdict(float)
        self.n = defaultdict(int)

    def avg(self, season: int) -> dict:
        for s in (season, season - 1):
            if self.n[s] >= 40:
                return {"g": self.goals[s] / self.n[s], "s": self.shots[s] / self.n[s], "t": self.sot[s] / self.n[s]}
        return {"g": LG_GOALS, "s": LG_SHOTS, "t": LG_SOT}


TEAM_FEATURES = ["gf_ew", "ga_ew", "sf_ew", "sa_ew", "tf_ew", "ta_ew", "gf_season", "ga_season", "elo", "rest_days",
                 "promoted", "games"]


def build_team_features(games: pl.DataFrame, stats: pl.DataFrame) -> pl.DataFrame:
    st = {(r["game_id"], r["team_id"]): r for r in stats.iter_rows(named=True)}
    teams: dict[int, _Team] = defaultdict(_Team)
    lg = _League()
    in_season: dict[int, set] = defaultdict(set)
    for r in games.select("season", "home_team_id", "away_team_id").iter_rows():
        in_season[r[0]].update((r[1], r[2]))
    out = []
    for (day,), dg in games.sort("kickoff_utc").group_by(["game_date"], maintain_order=True):
        rows = list(dg.iter_rows(named=True))
        for g in rows:
            season = g["season"]
            la = lg.avg(season)
            for team, opp, home in ((g["home_team_id"], g["away_team_id"], True), (g["away_team_id"], g["home_team_id"], False)):
                t = teams[team]
                if t.season != season:
                    _roll(t, season, la, team not in in_season.get(season - 1, set()) and bool(in_season.get(season - 1)))
                prior = {"gf": t.ew["gf"], "ga": t.ew["ga"]}
                rest = min((day - t.last_date).days, 14) if t.last_date else 14
                out.append({
                    "game_id": g["game_id"], "team_id": team, "opp_team_id": opp, "is_home": home, "season": season,
                    "game_date": day, "status": g["status"],
                    **{f"{m}_ew": t.ew[m] for m in METRICS},
                    "gf_season": (t.tot["gf"] + PRIOR_GAMES * prior["gf"]) / (t.n + PRIOR_GAMES),
                    "ga_season": (t.tot["ga"] + PRIOR_GAMES * prior["ga"]) / (t.n + PRIOR_GAMES),
                    "elo": t.elo, "rest_days": rest, "promoted": t.promoted, "games": t.n,
                    "league_goals": la["g"], "neutral": bool(g.get("neutral")),
                })
        for g in rows:
            if g["status"] != "final" or g["home_score"] is None:
                continue
            h, a = g["home_team_id"], g["away_team_id"]
            sh, sa = st.get((g["game_id"], h)) or {}, st.get((g["game_id"], a)) or {}
            vals = {h: {"gf": g["home_score"], "ga": g["away_score"], "sf": sh.get("shots"), "sa": sa.get("shots"),
                        "tf": sh.get("shots_on_target"), "ta": sa.get("shots_on_target")},
                    a: {"gf": g["away_score"], "ga": g["home_score"], "sf": sa.get("shots"), "sa": sh.get("shots"),
                        "tf": sa.get("shots_on_target"), "ta": sh.get("shots_on_target")}}
            for team, v in vals.items():
                t = teams[team]
                for m in METRICS:
                    if v[m] is not None:
                        t.ew[m] = EW_ALPHA * v[m] + (1 - EW_ALPHA) * t.ew[m]
                t.tot["gf"] += v["gf"]; t.tot["ga"] += v["ga"]
                t.n += 1
                t.last_date = day
            lg.goals[g["season"]] += g["home_score"] + g["away_score"]
            lg.n[g["season"]] += 2
            for v in vals.values():
                if v["sf"] is not None and v["tf"] is not None:
                    lg.shots[g["season"]] += v["sf"]; lg.sot[g["season"]] += v["tf"]
            _elo(teams[h], teams[a], g["home_score"] - g["away_score"])
    return pl.DataFrame(out, infer_schema_length=None)


def _roll(t: _Team, season: int, la: dict, promoted: bool) -> None:
    seed = {"gf": la["g"], "ga": la["g"], "sf": la["s"], "sa": la["s"], "tf": la["t"], "ta": la["t"]}
    if promoted or not t.ew:
        # Promoted sides historically score less and concede more than the league average.
        base = {"gf": 0.80, "ga": 1.22, "sf": 0.88, "sa": 1.12, "tf": 0.85, "ta": 1.15} if promoted else dict.fromkeys(seed, 1.0)
        t.ew = {m: seed[m] * base[m] for m in METRICS}
        if promoted or t.season is None:
            t.elo = PROMOTED_ELO if promoted else ELO_INIT
    else:
        t.ew = {m: CARRY * t.ew[m] + (1 - CARRY) * seed[m] for m in METRICS}
        t.elo = 0.8 * t.elo + 0.2 * ELO_INIT
    t.promoted = promoted
    t.season, t.tot, t.n = season, defaultdict(float), 0


def _elo(h: _Team, a: _Team, gd: int) -> None:
    diff = h.elo + ELO_HFA - a.elo
    res = 1.0 if gd > 0 else 0.5 if gd == 0 else 0.0
    mult = 1.0 if abs(gd) <= 1 else 1.5 if abs(gd) == 2 else (11 + abs(gd)) / 8
    d = ELO_K * mult * (res - elo_expect(diff))
    h.elo += d
    a.elo -= d


_Team.promoted = False  # default for type checkers


def matchup_frame(tf: pl.DataFrame) -> pl.DataFrame:
    opp = tf.select("game_id", pl.col("team_id").alias("opp_team_id"), *[pl.col(c).alias(f"opp_{c}") for c in TEAM_FEATURES])
    df = tf.join(opp, on=["game_id", "opp_team_id"])
    return df.with_columns(
        (pl.col("elo") - pl.col("opp_elo")
         + pl.when(pl.col("neutral")).then(0.0).when(pl.col("is_home")).then(ELO_HFA).otherwise(-ELO_HFA)).alias("elo_diff"),
        ((pl.col("gf_ew") + pl.col("opp_ga_ew")) / 2).alias("goal_matchup"),
        ((pl.col("tf_ew") + pl.col("opp_ta_ew")) / 2).alias("sot_matchup"),
        ((pl.col("sf_ew") + pl.col("opp_sa_ew")) / 2).alias("shot_matchup"),
        ((pl.col("gf_season") + pl.col("opp_ga_season")) / 2).alias("season_matchup"),
        (pl.col("is_home") & ~pl.col("neutral")).cast(pl.Int8).alias("home"),   # finals at neutral venues: no home edge
    )

"""Point-in-time player features, eligibility and availability flags (FR-03.1, FR-03.5, FR-03.6).

Same day-batch discipline as team features: everything for a day is computed before that
day's box scores are applied.

Two outputs:
* ``train``      -- every actual appearance, with features known before tipoff (model training)
* ``candidates`` -- players we would have forecast *before* each game, chosen only from
                    pregame information (roster history), never from who actually played.
                    Used for backtests and live predictions.

No injury/lineup feed is licensed yet, so availability is inferred from recent box-score
status (played / DNP / inactive) and labeled as such.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date

import polars as pl

STATS = ("points", "assists", "rebounds")
LEAGUE_RATE_SEED = {"points": 0.47, "assists": 0.11, "rebounds": 0.19}  # per minute
RATE_PRIOR_MIN = 150.0      # minutes of prior weight when blending per-minute rates
ELIG_MIN_GAMES_L10 = 3      # must have played >= 3 of the team's last 10 games ...
ELIG_MIN_MINUTES = 12.0     # ... averaging >= 12 minutes
EWM_ALPHA = 0.3


@dataclass
class _PState:
    team: int | None = None
    season: int | None = None
    last_listed: date | None = None
    last_played: date | None = None
    recent: deque = field(default_factory=lambda: deque(maxlen=10))  # (min, pts, ast, reb, starter, team)
    min_ewm: float | None = None
    s_min: float = 0.0
    s_games: int = 0
    s_tot: dict = field(default_factory=lambda: dict.fromkeys(STATS, 0.0))
    prior_rate: dict = field(default_factory=dict)
    status_by_game: dict = field(default_factory=dict)  # game_id -> (participation, comment)


class _LeagueRates:
    def __init__(self):
        self.mins = defaultdict(float)
        self.tot = defaultdict(lambda: dict.fromkeys(STATS, 0.0))

    def rate(self, season: int, stat: str) -> float:
        for s in (season, season - 1):
            if self.mins[s] > 20000:
                return self.tot[s][stat] / self.mins[s]
        return LEAGUE_RATE_SEED[stat]


def _features(ps: _PState, season: int, day: date, team_row: dict, lg: _LeagueRates,
              team_recent_games: list[int]) -> dict:
    rec = [r for r in ps.recent]
    mins = [r[0] for r in rec]
    l5 = mins[-5:]
    f = {
        "min_l5": sum(l5) / len(l5) if l5 else 0.0,
        "min_l10": sum(mins) / len(mins) if mins else 0.0,
        "min_ewm": ps.min_ewm or 0.0,
        "min_season": (ps.s_min / ps.s_games) if (ps.s_games and ps.season == season) else 0.0,
        "start_rate_l10": (sum(1 for r in rec if r[4]) / len(rec)) if rec else 0.0,
        "gp_rate_l10": (sum(1 for g in team_recent_games
                            if ps.status_by_game.get(g, ("",))[0] == "played") / len(team_recent_games))
        if team_recent_games else 0.0,
        "season_games": ps.s_games if ps.season == season else 0,
        "days_since_played": min((day - ps.last_played).days, 30) if ps.last_played else 30,
        "is_home": team_row["is_home"], "b2b": team_row["b2b"],
        "pace_factor": team_row["exp_pace"] / team_row["league_pace"],
        "opp_def_factor": team_row["opp_drtg"] / team_row["league_ortg"],
        "opp_def_factor_l10": team_row["opp_drtg_l10"] / team_row["league_ortg"],
    }
    same_season = ps.season == season
    for st in STATS:
        lg_rate = lg.rate(season, st)
        prior = ps.prior_rate.get(st, lg_rate)
        s_tot = ps.s_tot[st] if same_season else 0.0
        s_min = ps.s_min if same_season else 0.0
        rate = (s_tot + RATE_PRIOR_MIN * prior) / (s_min + RATE_PRIOR_MIN)
        idx = {"points": 1, "assists": 2, "rebounds": 3}[st]
        rmin = sum(mins)
        f[f"{st}_rate"] = rate
        f[f"{st}_rate_l10"] = (sum(r[idx] for r in rec) / rmin) if rmin else rate
        f[f"{st}_rate_vs_league"] = rate / lg_rate
        base = f["min_ewm"] * rate * f["pace_factor"]
        if st == "points":
            base *= f["opp_def_factor"]
        f[f"{st}_base"] = base
        f[f"{st}_season_avg"] = (s_tot / ps.s_games) if (same_season and ps.s_games) else \
            (prior * (f["min_l10"] or 20.0))
    return f


def _availability(ps: _PState, team_games: list[int]) -> tuple[str, str | None, bool]:
    """Return (availability, reason, exclude). Uses only pregame information."""
    if not team_games:
        return "uncertain", "no prior team games this season", False
    last = ps.status_by_game.get(team_games[-1])
    prev = ps.status_by_game.get(team_games[-2]) if len(team_games) > 1 else None
    if last and last[0] == "inactive" and prev and prev[0] == "inactive":
        return "likely_out", f"inactive in each of the team's last 2 games ({last[1] or 'no reason given'})", True
    if last is None:
        return "uncertain", "not listed in the team's last game", False
    if last[0] == "played":
        return "available", None, False
    if last[0] == "inactive":
        return "doubtful", f"inactive last game ({last[1] or 'no reason given'})", False
    if last[0] == "dnp":
        return "uncertain", f"did not play last game ({last[1] or 'coach decision'})", False
    return "uncertain", "participation unknown last game", False


def build_player_features(games: pl.DataFrame, player_box: pl.DataFrame, team_matchups: pl.DataFrame
                          ) -> tuple[pl.DataFrame, pl.DataFrame]:
    tm = {(r["game_id"], r["team_id"]): r for r in team_matchups.iter_rows(named=True)}
    box_by_game: dict[int, list[dict]] = defaultdict(list)
    for r in player_box.iter_rows(named=True):
        box_by_game[r["game_id"]].append(r)

    players: dict[int, _PState] = defaultdict(_PState)
    lg = _LeagueRates()
    team_games: dict[int, list[int]] = defaultdict(list)        # final games per team, in order
    team_season_games: dict[tuple[int, int], list[int]] = defaultdict(list)
    train: list[dict] = []
    cands: list[dict] = []

    for (day,), day_games in games.sort("tipoff_utc").group_by(["game_date_et"], maintain_order=True):
        rows = list(day_games.iter_rows(named=True))
        roster_by_team: dict[int, list[int]] = defaultdict(list)
        for pid, ps in players.items():
            if ps.team is not None:
                roster_by_team[ps.team].append(pid)

        for g in rows:
            season = g["season"]
            for team in (g["home_team_id"], g["away_team_id"]):
                trow = tm.get((g["game_id"], team))
                if trow is None:
                    continue
                recent10 = team_games[team][-10:]
                # --- candidates (pregame roster-based selection)
                for pid in roster_by_team.get(team, []):
                    ps = players[pid]
                    played_l10 = [ps.status_by_game.get(x, ("",))[0] == "played" for x in recent10]
                    if sum(played_l10) < ELIG_MIN_GAMES_L10:
                        continue
                    f = _features(ps, season, day, trow, lg, recent10)
                    if f["min_l10"] < ELIG_MIN_MINUTES:
                        continue
                    avail, reason, exclude = _availability(ps, team_season_games[(team, season)][-2:]
                                                           or team_games[team][-2:])
                    cands.append({"game_id": g["game_id"], "player_id": pid, "team_id": team,
                                  "season": season, "game_date_et": day, "status": g["status"],
                                  "availability": avail, "availability_reason": reason,
                                  "excluded": exclude, **f})
                # --- training rows (actual appearances, pregame features)
                if g["status"] == "final":
                    for b in box_by_game.get(g["game_id"], []):
                        if b["team_id"] != team or b["participation"] != "played" or b["minutes"] is None:
                            continue
                        ps = players[b["player_id"]]
                        f = _features(ps, season, day, trow, lg, recent10)
                        train.append({"game_id": g["game_id"], "player_id": b["player_id"], "team_id": team,
                                      "season": season, "game_date_et": day, "n_recent": len(ps.recent),
                                      **f, **{f"y_{s}": b[s] for s in STATS}, "y_minutes": b["minutes"]})

        # --- apply today's box scores
        for g in rows:
            if g["status"] != "final":
                continue
            season = g["season"]
            for team in (g["home_team_id"], g["away_team_id"]):
                team_games[team].append(g["game_id"])
                team_season_games[(team, season)].append(g["game_id"])
            for b in box_by_game.get(g["game_id"], []):
                ps = players[b["player_id"]]
                if ps.season != season:
                    if ps.s_min >= 200:
                        ps.prior_rate = {s: ps.s_tot[s] / ps.s_min for s in STATS}
                    ps.season, ps.s_min, ps.s_games = season, 0.0, 0
                    ps.s_tot = dict.fromkeys(STATS, 0.0)
                ps.team = b["team_id"]
                ps.last_listed = day
                ps.status_by_game[g["game_id"]] = (b["participation"], b["status_comment"])
                if b["participation"] == "played" and b["minutes"] is not None:
                    m = b["minutes"]
                    ps.recent.append((m, b["points"] or 0, b["assists"] or 0, b["rebounds"] or 0,
                                      bool(b["starter"]), b["team_id"]))
                    ps.min_ewm = m if ps.min_ewm is None else EWM_ALPHA * m + (1 - EWM_ALPHA) * ps.min_ewm
                    ps.s_min += m
                    ps.s_games += 1
                    ps.last_played = day
                    for s in STATS:
                        ps.s_tot[s] += b[s] or 0
                        lg.tot[season][s] += b[s] or 0
                    lg.mins[season] += m
        # Prune stale status history (memory) -- keep only what availability/gp_rate needs.
        if len(team_games) and day.day == 1:
            for ps in players.values():
                if len(ps.status_by_game) > 200:
                    ps.status_by_game = dict(list(ps.status_by_game.items())[-120:])

    return pl.DataFrame(train, infer_schema_length=None), pl.DataFrame(cands, infer_schema_length=None)

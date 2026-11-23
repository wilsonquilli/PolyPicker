"""Point-in-time NFL features (same discipline as the NBA: nothing from a game's own week leaks in).

Games are processed one (season, week) at a time: features for every game in a week are computed
*before* any result from that week is applied, so they only use earlier weeks -- what was knowable
before kickoff. Schedule facts (rest, venue, roof) and the week's official injury report are known
in advance and may be used.

Outputs
* team rows       one per (game, team) with pregame team state, joined into matchup rows
* player train    every actual appearance with pregame features (model training)
* player cands    players we would forecast before each game, chosen from pregame info only
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field

import polars as pl

# ---------------------------------------------------------------- team state

LEAGUE_PPG_SEED, LEAGUE_EPA_SEED, LEAGUE_PLAYS_SEED = 22.5, 0.0, 63.0
PRIOR_GAMES = 4            # weight (games) of the prior when blending early-season stats
PRIOR_REGRESS = 1 / 3      # pull of last season's numbers toward league average
RECENT_N = 4
ELO_INIT, ELO_K, ELO_HFA, ELO_CARRY = 1505.0, 20.0, 48.0, 2 / 3
OUT_STATUSES = ("Out", "Doubtful")


def elo_win_prob(diff: float) -> float:
    return 1 / (1 + 10 ** (-diff / 400))


@dataclass
class _G:
    pts: float
    opp_pts: float
    plays: float
    epa: float           # offensive EPA (pass + rush)
    opp_epa: float       # EPA allowed
    pass_yds_allowed: float
    rush_yds_allowed: float
    pass_att: float
    carries: float


@dataclass
class _Team:
    elo: float = ELO_INIT
    season: int | None = None
    games: list = field(default_factory=list)
    recent: deque = field(default_factory=lambda: deque(maxlen=RECENT_N))
    prior: dict = field(default_factory=dict)
    qb_attempts: dict = field(default_factory=lambda: defaultdict(float))   # current-season pass attempts by QB
    last_qb1: str | None = None


def _mean(xs, attr):
    return sum(getattr(x, attr) for x in xs) / len(xs) if xs else None


def _per_play(xs, attr):
    plays = sum(x.plays for x in xs)
    return sum(getattr(x, attr) for x in xs) / plays if plays else None


def _blend(n, val, prior):
    return prior if (val is None or n == 0) else (n * val + PRIOR_GAMES * prior) / (n + PRIOR_GAMES)


class _League:
    def __init__(self):
        self.pts = defaultdict(float)
        self.games = defaultdict(int)
        self.plays = defaultdict(float)
        self.pass_yds = defaultdict(float)
        self.rush_yds = defaultdict(float)

    def _val(self, season, num, den, seed, min_den):
        for s in (season, season - 1):
            if den[s] > min_den:
                return num[s] / den[s]
        return seed

    def ppg(self, s):
        return self._val(s, self.pts, defaultdict(float, {k: 2 * v for k, v in self.games.items()}), LEAGUE_PPG_SEED, 60)

    def pass_ypg(self, s):
        return self._val(s, self.pass_yds, defaultdict(float, {k: 2 * v for k, v in self.games.items()}), 215.0, 60)

    def rush_ypg(self, s):
        return self._val(s, self.rush_yds, defaultdict(float, {k: 2 * v for k, v in self.games.items()}), 115.0, 60)


TEAM_FEATURES = ["ppg", "papg", "off_epa", "def_epa", "ppg_l4", "papg_l4", "off_epa_l4", "def_epa_l4", "elo",
                 "plays_pg", "pass_ypg_allowed", "rush_ypg_allowed", "pass_att_pg", "carries_pg", "rest_days",
                 "short_week", "off_bye", "qb_out", "season_games"]


def _qb1(st: _Team) -> str | None:
    if st.qb_attempts:
        return max(st.qb_attempts.items(), key=lambda kv: kv[1])[0]
    return st.last_qb1


def week_groups(games: pl.DataFrame):
    """(season, week) batches in kickoff order."""
    order = games.group_by("season", "week").agg(pl.col("kickoff_utc").min().alias("k")).sort("k")
    by = {k: g for k, g in games.sort("kickoff_utc").group_by(["season", "week"], maintain_order=True)}
    for s, w in order.select("season", "week").iter_rows():
        yield (s, w), by[(s, w)]


def build_team_features(games: pl.DataFrame, team_box: pl.DataFrame, player_box: pl.DataFrame,
                        injuries: pl.DataFrame) -> pl.DataFrame:
    box = {(r["game_id"], r["team_id"]): r for r in team_box.iter_rows(named=True)}
    qb_box: dict[tuple, list] = defaultdict(list)
    for r in player_box.filter((pl.col("position") == "QB") & (pl.col("participation") == "played")).iter_rows(named=True):
        qb_box[(r["game_id"], r["team_id"])].append((r["player_id"], r["pass_attempts"] or 0))
    out_list = {(r["season"], r["week"], r["team_id"], r["player_id"])
                for r in injuries.filter(pl.col("report_status").is_in(OUT_STATUSES)).iter_rows(named=True)}
    states: dict[int, _Team] = defaultdict(_Team)
    lg = _League()
    out: list[dict] = []

    for (season, week), wk in week_groups(games):
        rows = list(wk.iter_rows(named=True))
        lg_ppg = lg.ppg(season)
        for g in rows:
            for team, opp, is_home, rest in ((g["home_team_id"], g["away_team_id"], True, g["home_rest"]),
                                             (g["away_team_id"], g["home_team_id"], False, g["away_rest"])):
                st = states[team]
                if st.season != season:
                    _roll(st, season, lg, lg_ppg)
                n = len(st.games)
                p = st.prior
                rec = list(st.recent)
                ppg = _blend(n, _mean(st.games, "pts"), p["ppg"])
                papg = _blend(n, _mean(st.games, "opp_pts"), p["papg"])
                off = _blend(n, _per_play(st.games, "epa"), p["off_epa"])
                de = _blend(n, _per_play(st.games, "opp_epa"), p["def_epa"])
                rest_d = min(int(rest), 14) if rest is not None else 7
                qb1 = _qb1(st)
                out.append({
                    "game_id": g["game_id"], "team_id": team, "opp_team_id": opp, "is_home": is_home,
                    "season": season, "week": week, "game_date_et": g["game_date_et"], "status": g["status"],
                    "ppg": ppg, "papg": papg, "off_epa": off, "def_epa": de,
                    "ppg_l4": _mean(rec, "pts") if rec else ppg, "papg_l4": _mean(rec, "opp_pts") if rec else papg,
                    "off_epa_l4": _per_play(rec, "epa") if rec else off,
                    "def_epa_l4": _per_play(rec, "opp_epa") if rec else de,
                    "elo": st.elo, "plays_pg": _blend(n, _mean(st.games, "plays"), p["plays_pg"]),
                    "pass_ypg_allowed": _blend(n, _mean(st.games, "pass_yds_allowed"), p["pass_ypg_allowed"]),
                    "rush_ypg_allowed": _blend(n, _mean(st.games, "rush_yds_allowed"), p["rush_ypg_allowed"]),
                    "pass_att_pg": _blend(n, _mean(st.games, "pass_att"), p["pass_att_pg"]),
                    "carries_pg": _blend(n, _mean(st.games, "carries"), p["carries_pg"]),
                    "rest_days": rest_d, "short_week": rest_d <= 5 and week > 1, "off_bye": rest_d >= 12 and week > 1,
                    "qb1": qb1, "qb_out": bool(qb1 and (season, week, team, qb1) in out_list),
                    "season_games": n, "league_ppg": lg_ppg, "league_pass_ypg": lg.pass_ypg(season),
                    "league_rush_ypg": lg.rush_ypg(season),
                    "neutral": bool(g["neutral_site"]), "dome": g["roof"] in ("dome", "closed"),
                    "div_game": bool(g["div_game"]), "is_playoffs": g["season_type"] == "playoffs",
                })
        # apply this week's results
        for g in rows:
            if g["status"] != "final":
                continue
            h, a = box.get((g["game_id"], g["home_team_id"])), box.get((g["game_id"], g["away_team_id"]))
            if not h or not a or not h["plays"] or not a["plays"]:
                continue
            for me, op, team in ((h, a, g["home_team_id"]), (a, h, g["away_team_id"])):
                epa = (me["passing_epa"] or 0) + (me["rushing_epa"] or 0)
                opp_epa = (op["passing_epa"] or 0) + (op["rushing_epa"] or 0)
                x = _G(me["points"], op["points"], me["plays"], epa, opp_epa, op["passing_yards"] or 0,
                       op["rushing_yards"] or 0, me["pass_attempts"] or 0, me["carries"] or 0)
                states[team].games.append(x)
                states[team].recent.append(x)
                for pid, att in qb_box.get((g["game_id"], team), []):
                    states[team].qb_attempts[pid] += att
                lg.pass_yds[g["season"]] += me["passing_yards"] or 0
                lg.rush_yds[g["season"]] += me["rushing_yards"] or 0
            lg.pts[g["season"]] += g["home_score"] + g["away_score"]
            lg.games[g["season"]] += 1
            _update_elo(states[g["home_team_id"]], states[g["away_team_id"]], g["home_score"] - g["away_score"],
                        bool(g["neutral_site"]))
    return pl.DataFrame(out, infer_schema_length=None)


def _roll(st: _Team, season: int, lg: _League, lg_ppg: float) -> None:
    r = PRIOR_REGRESS
    lg_pass, lg_rush = lg.pass_ypg(season), lg.rush_ypg(season)
    if st.games:
        st.prior = {"ppg": (1 - r) * _mean(st.games, "pts") + r * lg_ppg,
                    "papg": (1 - r) * _mean(st.games, "opp_pts") + r * lg_ppg,
                    "off_epa": (1 - r) * _per_play(st.games, "epa"), "def_epa": (1 - r) * _per_play(st.games, "opp_epa"),
                    "plays_pg": (1 - r) * _mean(st.games, "plays") + r * LEAGUE_PLAYS_SEED,
                    "pass_ypg_allowed": (1 - r) * _mean(st.games, "pass_yds_allowed") + r * lg_pass,
                    "rush_ypg_allowed": (1 - r) * _mean(st.games, "rush_yds_allowed") + r * lg_rush,
                    "pass_att_pg": (1 - r) * _mean(st.games, "pass_att") + r * 34.0,
                    "carries_pg": (1 - r) * _mean(st.games, "carries") + r * 26.0}
        st.last_qb1 = _qb1(st)
    elif not st.prior:
        st.prior = {"ppg": lg_ppg, "papg": lg_ppg, "off_epa": LEAGUE_EPA_SEED, "def_epa": LEAGUE_EPA_SEED,
                    "plays_pg": LEAGUE_PLAYS_SEED, "pass_ypg_allowed": lg_pass, "rush_ypg_allowed": lg_rush,
                    "pass_att_pg": 34.0, "carries_pg": 26.0}
    if st.season is not None:
        st.elo = ELO_CARRY * st.elo + (1 - ELO_CARRY) * ELO_INIT
    st.season, st.games = season, []
    st.recent.clear()
    st.qb_attempts = defaultdict(float)


def _update_elo(home: _Team, away: _Team, margin: float, neutral: bool) -> None:
    diff = home.elo + (0 if neutral else ELO_HFA) - away.elo
    p = elo_win_prob(diff)
    result = 1.0 if margin > 0 else (0.5 if margin == 0 else 0.0)
    fav_diff = diff if margin > 0 else -diff
    mult = math.log(abs(margin) + 1) * 2.2 / (fav_diff * 0.001 + 2.2)  # FiveThirtyEight NFL MOV multiplier
    delta = ELO_K * mult * (result - p)
    home.elo += delta
    away.elo -= delta


def matchup_frame(tf: pl.DataFrame) -> pl.DataFrame:
    opp = tf.select("game_id", pl.col("team_id").alias("opp_team_id"), *[pl.col(c).alias(f"opp_{c}") for c in TEAM_FEATURES])
    df = tf.join(opp, on=["game_id", "opp_team_id"])
    home_field = pl.when(pl.col("neutral")).then(0).when(pl.col("is_home")).then(1).otherwise(-1)
    return df.with_columns(
        home_field.alias("home_field"),
        (pl.col("elo") - pl.col("opp_elo") + home_field * ELO_HFA).alias("elo_diff"),
        ((pl.col("ppg") + pl.col("opp_papg")) / 2).alias("struct_pts"),
        ((pl.col("ppg_l4") + pl.col("opp_papg_l4")) / 2).alias("struct_pts_l4"),
        (pl.col("off_epa") + pl.col("opp_def_epa")).alias("epa_matchup"),
        (pl.col("off_epa_l4") + pl.col("opp_def_epa_l4")).alias("epa_matchup_l4"),
        ((pl.col("plays_pg") + pl.col("opp_plays_pg")) / 2).alias("exp_plays"),
    )


# ---------------------------------------------------------------- player state

# Stats we forecast. "scrimmage_tds" = rushing + receiving TDs ("anytime touchdown").
STATS = ("passing_yards", "passing_tds", "rushing_yards", "receptions", "receiving_yards", "scrimmage_tds")
USAGE = ("pass_attempts", "carries", "targets")
RECENT_P = 8
PRIOR_GAMES_P = 3.0


@dataclass
class _P:
    team: int | None = None
    position: str | None = None
    season: int | None = None
    recent: deque = field(default_factory=lambda: deque(maxlen=RECENT_P))   # dicts of played games
    s_games: int = 0
    s_tot: dict = field(default_factory=lambda: defaultdict(float))
    prior_pg: dict = field(default_factory=dict)
    status_by_game: dict = field(default_factory=dict)


def _avg(rec, key, n=None):
    xs = list(rec)[-n:] if n else list(rec)
    return sum(x[key] for x in xs) / len(xs) if xs else 0.0


def eligible_stats(f: dict, position: str | None) -> list[str]:
    """Which props a player gets (by recent usage, from pregame info only)."""
    out = []
    att, car, tgt = f["pass_attempts_l3"], f["carries_l3"], f["targets_l3"]
    if position == "QB" and att >= 15:
        out += ["passing_yards", "passing_tds"]
    if car >= 4 or (position == "QB" and att >= 15 and car >= 2):
        out.append("rushing_yards")
    if tgt >= 2.5 and position != "QB":
        out += ["receptions", "receiving_yards"]
    if position in ("RB", "WR", "TE", "FB") and car + tgt >= 4:
        out.append("scrimmage_tds")
    return out


def _player_features(ps: _P, season: int, trow: dict) -> dict:
    same = ps.season == season
    rec = list(ps.recent)
    n = ps.s_games if same else 0
    f = {"games_l8": len(rec), "season_games": n,
         "snap_l3": _avg(rec, "offense_pct", 3), "snap_l8": _avg(rec, "offense_pct")}
    for u in USAGE:
        f[f"{u}_l3"] = _avg(rec, u, 3)
        f[f"{u}_l8"] = _avg(rec, u)
    team_att, team_car = max(trow["pass_att_pg"], 1.0), max(trow["carries_pg"], 1.0)
    f["target_share"] = f["targets_l8"] / team_att
    f["carry_share"] = f["carries_l8"] / team_car
    for st in STATS:
        prior = ps.prior_pg.get(st, _avg(rec, st))
        s_avg = (ps.s_tot[st] / n) if n else None
        f[f"{st}_season"] = _blend_p(n, s_avg, prior)
        f[f"{st}_l3"] = _avg(rec, st, 3)
        f[f"{st}_l8"] = _avg(rec, st)
    lg_pass, lg_rush = trow["league_pass_ypg"], trow["league_rush_ypg"]
    f.update({
        "team_struct_pts": trow["struct_pts"], "team_epa_matchup": trow["epa_matchup"],
        "opp_pass_def": trow["opp_pass_ypg_allowed"] / lg_pass, "opp_rush_def": trow["opp_rush_ypg_allowed"] / lg_rush,
        "exp_plays": trow["exp_plays"], "is_home": trow["is_home"], "dome": trow["dome"],
        "team_qb_out": trow["qb_out"], "spread_proxy": trow["elo_diff"],
        "pos_qb": ps.position == "QB", "pos_rb": ps.position in ("RB", "FB"), "pos_wr": ps.position == "WR",
        "pos_te": ps.position == "TE",
    })
    return f


def _blend_p(n, val, prior):
    return prior if (val is None or n == 0) else (n * val + PRIOR_GAMES_P * prior) / (n + PRIOR_GAMES_P)


ROSTER_REASON = {"RES": "on a reserve list (injured reserve, PUP or similar)", "CUT": "released",
                 "TRD": "traded", "RET": "retired", "DEV": "on the practice squad", "EXE": "on the exempt list"}


class _Rosters:
    """Latest pregame roster status per (season, player) at or before a week. Game-day 'INA' designations
    are announced ~90 minutes before kickoff, so they are treated as active here."""

    def __init__(self, rw: pl.DataFrame | None):
        self.by: dict[tuple[int, str], list[tuple[int, int, str]]] = defaultdict(list)
        if rw is not None:
            for r in rw.sort("week").iter_rows(named=True):
                self.by[(r["season"], r["player_id"])].append((r["week"], r["team_id"], r["status"]))

    def status(self, season: int, week: int, pid: str) -> tuple[int, str] | None:
        hist = self.by.get((season, pid))
        if not hist:
            return None
        cur = [h for h in hist if h[0] <= week]
        if not cur:
            return None
        last_week = cur[-1][0]
        entries = [h for h in cur if h[0] == last_week]
        act = next((h for h in entries if h[2] in ("ACT", "INA")), None)
        h = act or entries[-1]
        return h[1], h[2]


def build_player_features(games: pl.DataFrame, player_box: pl.DataFrame, matchups: pl.DataFrame,
                          injuries: pl.DataFrame, roster_weeks: pl.DataFrame | None = None
                          ) -> tuple[pl.DataFrame, pl.DataFrame]:
    rosters = _Rosters(roster_weeks)
    tm = {(r["game_id"], r["team_id"]): r for r in matchups.iter_rows(named=True)}
    box_by_game: dict[int, list[dict]] = defaultdict(list)
    for r in player_box.iter_rows(named=True):
        box_by_game[r["game_id"]].append(r)
    inj = {(r["season"], r["week"], r["team_id"], r["player_id"]): (r["report_status"], r["injury"])
           for r in injuries.iter_rows(named=True)}
    players: dict[str, _P] = defaultdict(_P)
    team_games: dict[int, list[int]] = defaultdict(list)
    train, cands = [], []

    for (season, week), wk in week_groups(games):
        rows = list(wk.iter_rows(named=True))
        roster: dict[int, list[str]] = defaultdict(list)
        for pid, ps in players.items():
            if ps.team is not None:
                roster[ps.team].append(pid)
        for g in rows:
            for team in (g["home_team_id"], g["away_team_id"]):
                trow = tm.get((g["game_id"], team))
                if trow is None:
                    continue
                last3 = team_games[team][-3:]
                for pid in roster.get(team, []):
                    ps = players[pid]
                    if not any(ps.status_by_game.get(x) == "played" for x in last3):
                        continue
                    f = _player_features(ps, season, trow)
                    elig = eligible_stats(f, ps.position)
                    if not elig:
                        continue
                    rep = inj.get((season, week, team, pid))
                    status, injury = (rep or (None, None))
                    rs = rosters.status(season, week, pid)
                    if rs and (rs[0] != team or rs[1] not in ("ACT", "INA")):
                        reason = "no longer with the team" if rs[0] != team else ROSTER_REASON.get(rs[1], f"roster status {rs[1]}")
                        avail, reason, excluded = "out", reason[0].upper() + reason[1:], True
                    elif status in OUT_STATUSES:
                        avail, reason, excluded = "out", f"{status} on the injury report ({injury or 'no detail'})", True
                    elif status == "Questionable":
                        avail, reason, excluded = "questionable", f"Questionable on the injury report ({injury or 'no detail'})", False
                    else:
                        avail, reason, excluded = "available", None, False
                    cands.append({"game_id": g["game_id"], "player_id": pid, "team_id": team, "season": season,
                                  "week": week, "game_date_et": g["game_date_et"], "status": g["status"],
                                  "position": ps.position, "eligible": elig, "availability": avail,
                                  "availability_reason": reason, "excluded": excluded,
                                  "questionable": status == "Questionable", **f})
                if g["status"] == "final":
                    for b in box_by_game.get(g["game_id"], []):
                        if b["team_id"] != team or b["participation"] != "played":
                            continue
                        ps = players[b["player_id"]]
                        if ps.position is None:
                            ps.position = b["position"]
                        f = _player_features(ps, season, trow)
                        rep = inj.get((season, week, team, b["player_id"]))
                        train.append({"game_id": g["game_id"], "player_id": b["player_id"], "team_id": team,
                                      "season": season, "week": week, "game_date_et": g["game_date_et"],
                                      "position": ps.position, "eligible": eligible_stats(f, ps.position),
                                      "questionable": bool(rep and rep[0] == "Questionable"),
                                      **f, **{f"y_{s}": b[s] for s in STATS}})
        # apply results
        for g in rows:
            if g["status"] != "final":
                continue
            for t in (g["home_team_id"], g["away_team_id"]):
                team_games[t].append(g["game_id"])
            for b in box_by_game.get(g["game_id"], []):
                ps = players[b["player_id"]]
                if ps.season != g["season"]:
                    if ps.s_games >= 4:
                        ps.prior_pg = {s: ps.s_tot[s] / ps.s_games for s in STATS}
                    ps.season, ps.s_games, ps.s_tot = g["season"], 0, defaultdict(float)
                ps.team, ps.position = b["team_id"], b["position"] or ps.position
                ps.status_by_game[g["game_id"]] = b["participation"]
                if b["participation"] == "played":
                    rec = {"offense_pct": b["offense_pct"] or 0.0, **{u: b[u] or 0 for u in USAGE},
                           **{s: b[s] or 0 for s in STATS}}
                    ps.recent.append(rec)
                    ps.s_games += 1
                    for s in STATS:
                        ps.s_tot[s] += b[s] or 0
        if week == 1:
            for ps in players.values():
                if len(ps.status_by_game) > 80:
                    ps.status_by_game = dict(list(ps.status_by_game.items())[-40:])

    return pl.DataFrame(train, infer_schema_length=None), pl.DataFrame(cands, infer_schema_length=None)

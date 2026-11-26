"""Championship forecasts ("futures"): who wins the NBA title, the Super Bowl and each soccer competition.

Method (same idea for every sport):

1. **Our game model forecasts every remaining fixture** (the same model behind the per-game pages).
2. **Team ratings are fitted to those forecasts**: a points-margin rating for NBA/NFL, attack and defence goal
   ratings for soccer. They price match-ups that aren't on the schedule yet (playoffs, knockout ties).
3. **Monte Carlo**: the rest of the season and the post-season are played out ``N`` times. Every simulation
   draws a strength shock per team, because a team's true level is uncertain, not only single results. A team's
   title chance is the share of simulations it wins.

Simplifications, stated plainly in the API meta and the UI: tie-breakers beyond points / goal difference are
random, the NBA's 30 Cup-dependent games that aren't scheduled yet are not simulated, extra time / penalties
are a coin flip, and post-seasons already underway aren't modelled (re-run once the bracket is set).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta, datetime, timezone

import numpy as np
import polars as pl
from scipy.stats import norm
from sqlalchemy import Engine, insert, text

from polypicker.db import models as m

log = logging.getLogger(__name__)
N_SIMS = 20_000


def _rows(engine: Engine, sql: str, **params) -> list[dict]:
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(sql), params)]


def _save(engine: Engine, sport: str, competition: str, season: int, payload: dict, sims: int) -> None:
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    with engine.begin() as c:
        c.execute(insert(m.FuturesForecast).values(sport=sport, competition=competition, season=season,
                                                   simulations=sims, payload=payload))


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _o(n: int) -> str:
    """Spanish ordinal: 1.º, 2.º"""
    return f"{n}.º"


def _n(x: int) -> str:
    """Spanish thousands separator: 20.000"""
    return f"{x:,}".replace(",", ".")


def _form_es(form: str) -> str:
    return " ".join({"W": "G", "D": "E", "L": "P", "T": "E"}.get(c, c) for c in form)


def _pct(p: float) -> str:
    return "<1%" if 0 < p < 0.005 else f"{round(p * 100)}%"


# ---------------------------------------------------------------- rating fits

def fit_margin_ratings(games: list[tuple[int, int, float]], teams: list[int], ridge: float = 0.05) -> tuple[float, np.ndarray]:
    """Least squares: margin_home = hfa + r_home - r_away, ratings centred on 0 (an average team)."""
    ix = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    X = np.zeros((len(games) + 1 + n, n + 1))
    y = np.zeros(len(games) + 1 + n)
    for k, (h, a, mg) in enumerate(games):
        X[k, 0] = 1.0
        X[k, 1 + ix[h]] += 1.0
        X[k, 1 + ix[a]] -= 1.0
        y[k] = mg
    X[len(games), 1:] = 10.0                                        # sum of ratings = 0
    X[len(games) + 1:, 1:] = np.eye(n) * ridge                      # tiny pull to average for sparse teams
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    return float(beta[0]), beta[1:]


@dataclass
class GoalRatings:
    teams: list[int]
    base: float           # log goals of an average away side
    hfa: float            # log home advantage
    att: np.ndarray
    dfn: np.ndarray

    def idx(self, t: int) -> int:
        return self.teams.index(t)


def fit_goal_ratings(games: list[tuple[int, int, float, float, bool]], teams: list[int], ridge: float = 0.15) -> GoalRatings:
    """log xg_home = base + hfa + att_h - def_a ; log xg_away = base + att_a - def_h (neutral: no hfa)."""
    ix = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    rows, ys = [], []
    for h, a, lh, la, neutral in games:
        r = np.zeros(2 + 2 * n); r[0] = 1; r[1] = 0 if neutral else 1; r[2 + ix[h]] = 1; r[2 + n + ix[a]] = -1
        rows.append(r); ys.append(np.log(lh))
        r = np.zeros(2 + 2 * n); r[0] = 1; r[2 + ix[a]] = 1; r[2 + n + ix[h]] = -1
        rows.append(r); ys.append(np.log(la))
    c1 = np.zeros(2 + 2 * n); c1[2:2 + n] = 10
    c2 = np.zeros(2 + 2 * n); c2[2 + n:] = 10
    reg = np.hstack([np.zeros((2 * n, 2)), np.eye(2 * n) * ridge])
    X = np.vstack([*rows, c1, c2, reg])
    y = np.r_[ys, 0, 0, np.zeros(2 * n)]
    b = np.linalg.lstsq(X, y, rcond=None)[0]
    return GoalRatings(teams, float(b[0]), float(b[1]), b[2:2 + n], b[2 + n:])


# ---------------------------------------------------------------- NBA

NBA_SHOCK = 2.75      # sd (points) of a team's true-strength uncertainty for the rest of the season


def _real_winner(w: np.ndarray, a, b, results: list[tuple[int, int, int]]) -> np.ndarray:
    """Replace simulated winners with real ones wherever the simulated pairing is a game already played."""
    a = np.broadcast_to(a, w.shape); b = np.broadcast_to(b, w.shape)
    for x, y, win in results:
        m = ((a == x) & (b == y)) | ((a == y) & (b == x))
        if m.any():
            w = np.where(m, win, w)
    return w


def _nba_postseason(engine: Engine, season: int, ix: dict[int, int], as_of=None) -> dict:
    """Real play-in games and playoff series so far (team indices): series state (home court, wins, games played),
    final play-in results, the play-in seeds 7-10 and, once the bracket is set, seeds 1-8 per conference."""
    rows = _rows(engine, """SELECT season_type, game_label, game_sub_label, tipoff_utc, home_team_id, away_team_id,
                                   home_score, away_score, status
                            FROM games WHERE season = :s AND season_type IN ('play_in', 'playoffs') ORDER BY tipoff_utc""", s=season)
    rows = [r for r in rows if r["home_team_id"] in ix and r["away_team_id"] in ix]
    if as_of is not None:     # replay: games after as_of not played yet; a series exists once its game 1 was scheduled
        first = {}
        for r in rows:
            first.setdefault((r["game_label"], frozenset((r["home_team_id"], r["away_team_id"]))), r["tipoff_utc"])
        rows = [r if r["tipoff_utc"] <= as_of else {**r, "status": "scheduled", "home_score": None, "away_score": None}
                for r in rows if r["season_type"] == "play_in" or first[(r["game_label"], frozenset((r["home_team_id"], r["away_team_id"])))] <= as_of + timedelta(days=2)]
    done = lambda r: r["status"] == "final" and r["home_score"] is not None and r["home_score"] != r["away_score"]  # noqa: E731
    win = lambda r: ix[r["home_team_id"]] if r["home_score"] > r["away_score"] else ix[r["away_team_id"]]  # noqa: E731
    wins = _rows(engine, """SELECT t.team_id, count(*) FILTER (WHERE g.winner_team_id = t.team_id)::float / greatest(count(*), 1) pct
                            FROM teams t JOIN games g ON (g.home_team_id = t.team_id OR g.away_team_id = t.team_id)
                            WHERE g.season = :s AND g.season_type = 'regular' AND g.status = 'final' GROUP BY t.team_id""", s=season)
    pct = {ix[r["team_id"]]: r["pct"] for r in wins if r["team_id"] in ix}
    out: dict = {"series": [], "play_in_results": [], "play_in": {}, "first_round": {}}
    pi_by: dict[str, list[dict]] = {}
    for r in rows:
        if r["season_type"] == "play_in":
            pi_by.setdefault(r["game_sub_label"] or "", []).append(r)
            if done(r):
                out["play_in_results"].append((ix[r["home_team_id"]], ix[r["away_team_id"]], win(r)))
    for conf, gs in pi_by.items():
        firsts = gs[:2]
        if len(firsts) == 2:     # 7 v 8 is the pairing of the better records; the home side is the higher seed
            g78, g910 = sorted(firsts, key=lambda r: -(pct.get(ix[r["home_team_id"]], 0) + pct.get(ix[r["away_team_id"]], 0)))
            out["play_in"][conf] = [ix[g78["home_team_id"]], ix[g78["away_team_id"]], ix[g910["home_team_id"]], ix[g910["away_team_id"]]]
    by: dict[tuple, list[dict]] = {}
    for r in rows:
        if r["season_type"] == "playoffs":
            by.setdefault((r["game_label"] or "", frozenset((r["home_team_id"], r["away_team_id"]))), []).append(r)
    r1: dict[str, list[dict]] = {}
    for (label, _), gs in by.items():
        hc = ix[gs[0]["home_team_id"]]
        other = ix[gs[0]["away_team_id"]]
        fin = [g for g in gs if done(g)]
        x = {"label": label, "hc": hc, "other": other, "played": len(fin),
             "w_hc": sum(win(g) == hc for g in fin), "w_other": sum(win(g) == other for g in fin)}
        out["series"].append(x)
        if label.endswith("First Round"):
            r1.setdefault(label.split()[0], []).append(x)
    for conf, xs in r1.items():
        if len(xs) == 4:         # home court = higher seed; the four home sides ranked by record are seeds 1-4
            xs = sorted(xs, key=lambda x: -pct.get(x["hc"], 0))
            out["first_round"][conf] = [x["hc"] for x in xs] + [x["other"] for x in reversed(xs)]
    return out


def simulate_nba(engine: Engine, n: int = N_SIMS, seed: int = 7, *, season: int | None = None, save: bool = True,
                 as_of=None) -> dict:
    """Rest of the regular season, the play-in and the playoffs. Play-in games and playoff series already played keep
    their real results (a series in progress continues from its real score); once the bracket is set the real seeds
    are used. ``season`` / ``save=False`` replay a past season without storing it."""
    from polypicker.ml import pipeline as P
    rng = np.random.default_rng(seed)
    fs = P.build_features(engine)
    season = season or int(fs.games.filter(pl.col("status") == "final")["season"].max())
    gm = P.load_model(engine, P.active_model_ids(engine)["game"])
    base = getattr(gm, "base", gm)          # v3 market-aware model: lines exist ~1 day out, use the v2 model
    sigma = float(base.sigma_win)
    sched = fs.games.filter((pl.col("season") == season) & (pl.col("status") == "scheduled")
                            & (pl.col("season_type") == "regular"))
    sched_design = fs.game_design.filter(pl.col("game_id").is_in(sched["game_id"].implode()))
    preds = base.predict_games(sched_design) if sched_design.height else []   # none left once the regular season ends

    teams = _rows(engine, "SELECT team_id, abbreviation, city, name, full_name, conference FROM teams WHERE is_active ORDER BY team_id")
    tids = [t["team_id"] for t in teams]
    ix = {t: i for i, t in enumerate(tids)}
    # Ratings from the model's margins for the games still to play; near the end of the regular season (and in the
    # playoffs) too few remain, so the whole season's regular-season games are used instead.
    fit_on = preds
    if len(preds) < 150:
        season_ids = fs.games.filter((pl.col("season") == season) & (pl.col("season_type") == "regular"))["game_id"]
        fit_on = base.predict_games(fs.game_design.filter(pl.col("game_id").is_in(season_ids.implode())))
    hfa, rating = fit_margin_ratings([(p["home_team_id"], p["away_team_id"], p["mu_margin"]) for p in fit_on
                                      if p["home_team_id"] in ix and p["away_team_id"] in ix], tids)
    post = _nba_postseason(engine, season, ix, as_of)

    finals = _rows(engine, """SELECT game_id, game_date_et, home_team_id, away_team_id, home_score, away_score FROM games
                              WHERE season = :s AND season_type = 'regular' AND status = 'final'
                                AND game_id::text LIKE '2%' ORDER BY tipoff_utc""", s=season)
    w0 = np.zeros(len(tids)); l0 = np.zeros(len(tids)); pf = np.zeros(len(tids)); pa = np.zeros(len(tids))
    last10: dict[int, list[int]] = {t: [] for t in tids}
    for g in finals:
        h, a = ix[g["home_team_id"]], ix[g["away_team_id"]]
        hw = g["home_score"] > g["away_score"]
        w0[h] += hw; l0[h] += not hw; w0[a] += not hw; l0[a] += hw
        pf[h] += g["home_score"]; pa[h] += g["away_score"]; pf[a] += g["away_score"]; pa[a] += g["home_score"]
        last10[g["home_team_id"]].append(int(hw)); last10[g["away_team_id"]].append(int(not hw))

    # Regular season: the model's own per-game margin (rest, travel, injuries) plus the team shock.
    shock = rng.normal(0, NBA_SHOCK, (n, len(tids)))
    wins = np.tile(w0, (n, 1))
    for p in preds:
        h, a = ix[p["home_team_id"]], ix[p["away_team_id"]]
        z = (p["mu_margin"] + shock[:, h] - shock[:, a]) / sigma
        hw = rng.random(n) < norm.cdf(z)
        wins[:, h] += hw; wins[:, a] += ~hw
    strength = rating[None, :] + shock
    key = wins + rng.random(wins.shape) * 0.5          # random tie-breaks
    ar = np.arange(n)

    def p_win(a, b, a_home):  # a, b: (n,) team indices
        mg = strength[ar, a] - strength[ar, b] + np.where(a_home, hfa, -hfa)
        return norm.cdf(mg / sigma)

    def game(a, b, a_home):
        w = np.where(rng.random(n) < p_win(a, b, a_home), a, b)
        return _real_winner(w, a, b, post["play_in_results"])     # a play-in game already played keeps its result

    def series(a, b):  # a has home court (2-2-1-1-1); winner of the best of 7 = more wins over all 7 games
        a = np.broadcast_to(a, (n,)).copy(); b = np.broadcast_to(b, (n,)).copy()
        hc, ot = a.copy(), b.copy()
        w_hc, w_ot, start = np.zeros(n), np.zeros(n), np.zeros(n, int)
        for x in post["series"]:                                  # a real series: its home court, wins so far
            m = ((a == x["hc"]) & (b == x["other"])) | ((a == x["other"]) & (b == x["hc"]))
            if m.any():
                hc[m], ot[m], w_hc[m], w_ot[m], start[m] = x["hc"], x["other"], x["w_hc"], x["w_other"], x["played"]
        for k, home in enumerate((True, True, False, False, True, False, True)):
            r = rng.random(n) < p_win(hc, ot, np.full(n, home))
            play = k >= start
            w_hc += play & r; w_ot += play & ~r
        return np.where(w_hc >= 4, hc, ot)

    def better(a, b):  # (higher seed first) by regular-season wins
        swap = key[ar, b] > key[ar, a]
        return np.where(swap, b, a), np.where(swap, a, b)

    made = np.zeros((n, len(tids)), bool); conf_win = np.zeros((n, len(tids)), bool); top_seed = np.zeros((n, len(tids)), bool)
    champs, finalists = [], []
    for conf in ("East", "West"):
        members = np.array([ix[t["team_id"]] for t in teams if t["conference"] == conf])
        order = members[np.argsort(-key[:, members], axis=1)]          # (n, 15) seeds 1..15
        s = [order[:, i] for i in range(10)]
        r1_real = post["first_round"].get(conf)
        if r1_real:                                                    # the bracket is set: real seeds 1..8
            seeds = [np.full(n, t) for t in r1_real]
        else:
            pi = post["play_in"].get(conf)
            if pi:                                                     # play-in set: real 7..10 seeds
                s[6:10] = [np.full(n, t) for t in pi]
            w78 = game(s[6], s[7], np.full(n, True))
            l78 = np.where(w78 == s[6], s[7], s[6])
            w910 = game(s[8], s[9], np.full(n, True))
            eighth = game(l78, w910, np.full(n, True))
            seeds = s[:6] + [w78, eighth]
        top_seed[ar, seeds[0]] = True
        for t in seeds:
            made[ar, t] = True
        r1 = [series(seeds[0], seeds[7]), series(seeds[3], seeds[4]), series(seeds[2], seeds[5]), series(seeds[1], seeds[6])]
        r2 = [series(*better(r1[0], r1[1])), series(*better(r1[2], r1[3]))]
        cf = series(*better(r2[0], r2[1]))
        conf_win[ar, cf] = True
        finalists.append(cf)
    champ = series(*better(finalists[0], finalists[1]))
    title = np.bincount(champ, minlength=len(tids)) / n

    # Stats behind the pick
    top_scorer = {r["team_id"]: r for r in _rows(engine, """
        SELECT DISTINCT ON (s.team_id) s.team_id, p.full_name, avg(s.points) ppg, count(*) gp
        FROM player_game_stats s JOIN games g USING (game_id) JOIN players p USING (player_id)
        WHERE g.season = :s AND g.season_type = 'regular' AND s.points IS NOT NULL AND s.minutes > 0
        GROUP BY s.team_id, p.full_name HAVING count(*) >= 5 ORDER BY s.team_id, avg(s.points) DESC""", s=season)}
    gp = np.maximum(w0 + l0, 1)
    diff = (pf - pa) / gp
    diff_rank = {tids[i]: r + 1 for r, i in enumerate(np.argsort(-diff))}
    rate_rank = {tids[i]: r + 1 for r, i in enumerate(np.argsort(-rating))}
    rec_rank = {tids[i]: r + 1 for r, i in enumerate(np.argsort(-(w0 / gp)))}
    out = []
    for t in teams:
        i = ix[t["team_id"]]
        l10 = last10[t["team_id"]][-10:]
        ts = top_scorer.get(t["team_id"])
        out.append({
            "team_id": t["team_id"], "abbr": t["abbreviation"], "name": t["full_name"], "short": t["name"],
            "conference": t["conference"], "p_title": round(float(title[i]), 4),
            "p_finals": round(float(conf_win[:, i].mean()), 4), "p_playoffs": round(float(made[:, i].mean()), 4),
            "p_top_seed": round(float(top_seed[:, i].mean()), 4),
            "projected_wins": round(float(wins[:, i].mean()), 1),
            "stats": {"wins": int(w0[i]), "losses": int(l0[i]), "record_rank": rec_rank[t["team_id"]],
                      "point_diff": round(float(diff[i]), 1), "point_diff_rank": diff_rank[t["team_id"]],
                      "rating": round(float(rating[i]), 1), "rating_rank": rate_rank[t["team_id"]],
                      "last10": f"{sum(l10)}-{len(l10) - sum(l10)}",
                      "top_scorer": {"name": ts["full_name"], "ppg": round(float(ts["ppg"]), 1)} if ts else None},
        })
    out.sort(key=lambda x: -x["p_title"])
    fav = out[0]
    st = fav["stats"]
    reasons = [
        f"{st['wins']}-{st['losses']} record, " + ("the best in the league" if st['record_rank'] == 1 else f"{_ordinal(st['record_rank'])} best in the league"),
        f"{st['point_diff']:+.1f} points per game scoring margin ({_ordinal(st['point_diff_rank'])})",
        f"Our model rates them {st['rating']:+.1f} points per game better than an average team ({_ordinal(st['rating_rank'])})",
        f"Projected to finish around {round(fav['projected_wins'])} wins; No. 1 seed in the {fav['conference']} in {_pct(fav['p_top_seed'])} of simulations",
        f"Reach the NBA Finals in {_pct(fav['p_finals'])} of simulations",
    ]
    if st["top_scorer"]:
        reasons.append(f"Led by {st['top_scorer']['name']} ({st['top_scorer']['ppg']} points per game)")
    reasons_es = [
        f"Récord de {st['wins']}-{st['losses']}, " + ("el mejor de la liga" if st["record_rank"] == 1 else f"el {_o(st['record_rank'])} mejor de la liga"),
        f"Diferencia de {st['point_diff']:+.1f} puntos por partido ({_o(st['point_diff_rank'])})",
        f"Nuestro modelo los considera {st['rating']:+.1f} puntos por partido mejores que un equipo promedio ({_o(st['rating_rank'])})",
        f"Proyectados para terminar con unas {round(fav['projected_wins'])} victorias; primer sembrado del {'Este' if fav['conference'] == 'East' else 'Oeste'} en el {_pct(fav['p_top_seed'])} de las simulaciones",
        f"Llegan a las Finales de la NBA en el {_pct(fav['p_finals'])} de las simulaciones",
    ]
    if st["top_scorer"]:
        reasons_es.append(f"Liderados por {st['top_scorer']['name']} ({st['top_scorer']['ppg']} puntos por partido)")
    es = {"title_es": "Campeón de la NBA", "event_es": "Finales de la NBA", "reasons_es": reasons_es,
          "method_es": f"{_n(n)} simulaciones de los {len(preds)} partidos restantes de la temporada regular, el play-in y cada ronda de playoffs."}
    payload = {**es, "title": "NBA champion", "event": "NBA Finals", "season_label": f"{season}-{str(season + 1)[2:]}",
               "status": "simulated", "remaining_games": len(preds), "teams": out, "reasons": reasons,
               "method": f"{n:,} simulations of the remaining {len(preds)} regular-season games, the play-in and every playoff round."}
    if save:
        _save(engine, "nba", "nba", season, payload, n)
    return {"competition": "nba", "favorite": fav["name"], "p": fav["p_title"], "remaining_games": len(preds),
            "teams": None if save else out}


# ---------------------------------------------------------------- NFL

NFL_SHOCK = 3.0


def _nfl_postseason(engine: Engine, season: int, ix: dict[int, int], conf_of: dict[int, str], as_of=None) -> dict:
    """Real playoff results so far and, once the wild-card round is set, the real seeds 1-7 per conference
    (wild-card hosts = seeds 2-4 by record, their opponents 7-5, the team with the bye = seed 1)."""
    rows = _rows(engine, """SELECT game_type, kickoff_utc, home_team_id, away_team_id, home_score, away_score, status
                            FROM nfl_games WHERE season = :s AND game_type IN ('WC', 'DIV', 'CON', 'SB') ORDER BY kickoff_utc""", s=season)
    rows = [r for r in rows if r["home_team_id"] in ix and r["away_team_id"] in ix]
    if as_of is not None:
        rows = [r if r["kickoff_utc"] <= as_of else {**r, "status": "scheduled", "home_score": None, "away_score": None}
                for r in rows if r["kickoff_utc"] <= as_of + timedelta(days=7)]
    pct = {ix[r["team_id"]]: r["pct"] for r in _rows(engine, """
        SELECT t.team_id, (count(*) FILTER (WHERE g.winner_team_id = t.team_id) + 0.5 * count(*) FILTER (WHERE g.home_score = g.away_score))::float
               / greatest(count(*), 1) pct
        FROM nfl_teams t JOIN nfl_games g ON (g.home_team_id = t.team_id OR g.away_team_id = t.team_id)
        WHERE g.season = :s AND g.game_type = 'REG' AND g.status = 'final' GROUP BY t.team_id""", s=season) if r["team_id"] in ix}
    out: dict = {"results": [], "seeds": {}}
    for r in rows:
        if r["status"] == "final" and r["home_score"] is not None and r["home_score"] != r["away_score"]:
            w = r["home_team_id"] if r["home_score"] > r["away_score"] else r["away_team_id"]
            out["results"].append((ix[r["home_team_id"]], ix[r["away_team_id"]], ix[w]))
    inv = {i: t for t, i in ix.items()}
    for conf in ("AFC", "NFC"):
        wc = [r for r in rows if r["game_type"] == "WC" and conf_of.get(r["home_team_id"]) == conf]
        if len(wc) != 3:
            continue
        wc.sort(key=lambda r: -pct.get(ix[r["home_team_id"]], 0))
        in_wc = {t for r in wc for t in (r["home_team_id"], r["away_team_id"])}
        bye = max((i for i in ix.values() if conf_of.get(inv[i]) == conf and inv[i] not in in_wc), key=lambda i: pct.get(i, 0))
        out["seeds"][conf] = [bye] + [ix[r["home_team_id"]] for r in wc] + [ix[r["away_team_id"]] for r in reversed(wc)]
    return out


def simulate_nfl(engine: Engine, n: int = N_SIMS, seed: int = 11, *, season: int | None = None, save: bool = True,
                 as_of=None) -> dict:
    """Rest of the regular season and the 14-team playoff. Once the wild-card round is set the real seeds are used,
    and playoff games already played keep their real results. ``season`` / ``save=False`` / ``as_of`` replay a past
    season without storing it."""
    from polypicker.ml.pipeline import load_model
    from polypicker.nfl.ml import pipeline as NP
    rng = np.random.default_rng(seed)
    fs = NP.build_features(engine)
    season = season or int(fs.games.filter(pl.col("status") == "final")["season"].max())
    gm = load_model(engine, NP.active_model_ids(engine)["game"])
    sigma = float(gm.sigma_win)
    sched = fs.games.filter((pl.col("season") == season) & (pl.col("status") != "final") & (pl.col("game_type") == "REG"))
    sched_design = fs.game_design.filter(pl.col("game_id").is_in(sched["game_id"].implode()))
    preds = gm.predict_games(sched_design) if sched_design.height else []    # none left once the regular season ends
    teams = _rows(engine, "SELECT team_id, abbreviation, name, full_name, conference, division FROM nfl_teams WHERE is_active ORDER BY team_id")
    tids = [t["team_id"] for t in teams]
    ix = {t: i for i, t in enumerate(tids)}
    fit_on = preds
    if len(preds) < 48:      # last weeks / playoffs: rate teams on the model's view of the whole regular season
        season_ids = fs.games.filter((pl.col("season") == season) & (pl.col("game_type") == "REG"))["game_id"]
        fit_on = gm.predict_games(fs.game_design.filter(pl.col("game_id").is_in(season_ids.implode())))
    hfa, rating = fit_margin_ratings([(p["home_team_id"], p["away_team_id"], p["mu_margin"]) for p in fit_on
                                      if p["home_team_id"] in ix and p["away_team_id"] in ix], tids)
    post = _nfl_postseason(engine, season, ix, {t["team_id"]: t["conference"] for t in teams}, as_of)

    finals = _rows(engine, """SELECT home_team_id, away_team_id, home_score, away_score FROM nfl_games
                              WHERE season = :s AND game_type = 'REG' AND status = 'final' ORDER BY kickoff_utc""", s=season)
    w0, l0, t0, pf, pa = (np.zeros(len(tids)) for _ in range(5))
    form: dict[int, list[str]] = {t: [] for t in tids}
    for g in finals:
        h, a = ix[g["home_team_id"]], ix[g["away_team_id"]]
        d = g["home_score"] - g["away_score"]
        if d == 0:
            t0[h] += 1; t0[a] += 1
        else:
            w0[h] += d > 0; l0[h] += d < 0; w0[a] += d < 0; l0[a] += d > 0
        pf[h] += g["home_score"]; pa[h] += g["away_score"]; pf[a] += g["away_score"]; pa[a] += g["home_score"]
        form[g["home_team_id"]].append("W" if d > 0 else "L" if d < 0 else "T")
        form[g["away_team_id"]].append("W" if d < 0 else "L" if d > 0 else "T")

    shock = rng.normal(0, NFL_SHOCK, (n, len(tids)))
    wins = np.tile(w0 + 0.5 * t0, (n, 1))
    for p in preds:
        h, a = ix[p["home_team_id"]], ix[p["away_team_id"]]
        hw = rng.random(n) < norm.cdf((p["mu_margin"] + shock[:, h] - shock[:, a]) / p.get("sigma_win", sigma))
        wins[:, h] += hw; wins[:, a] += ~hw
    key = wins + rng.random(wins.shape) * 0.5
    strength = rating[None, :] + shock
    ar = np.arange(n)

    def game(a, b, where):  # where: 1 = a at home, 0 = neutral
        mg = strength[ar, a] - strength[ar, b] + hfa * where
        return _real_winner(np.where(rng.random(n) < norm.cdf(mg / sigma), a, b), a, b, post["results"])

    made = np.zeros((n, len(tids)), bool); div_win = np.zeros((n, len(tids)), bool)
    conf_win = np.zeros((n, len(tids)), bool); top_seed = np.zeros((n, len(tids)), bool)
    champs = []
    for conf in ("AFC", "NFC"):
        divs = sorted({t["division"] for t in teams if t["conference"] == conf})
        winners = []
        for d in divs:
            mem = np.array([ix[t["team_id"]] for t in teams if t["division"] == d])
            winners.append(mem[np.argmax(key[:, mem], axis=1)])
        winners = np.stack(winners, axis=1)                                    # (n, 4)
        winners = np.take_along_axis(winners, np.argsort(-key[ar[:, None], winners], axis=1), axis=1)
        conf_teams = np.array([ix[t["team_id"]] for t in teams if t["conference"] == conf])
        k = key[:, conf_teams].copy()
        for j in range(4):
            k[conf_teams[None, :] == winners[:, j:j + 1]] = -1e9
        wild = conf_teams[np.argsort(-k, axis=1)[:, :3]]
        seeds = np.hstack([winners, wild])                                     # (n, 7) seeds 1..7
        if conf in post["seeds"]:                                              # the real bracket once it's set
            seeds = np.tile(np.array(post["seeds"][conf]), (n, 1))
            winners = seeds[:, :4]
        for j in range(7):
            made[ar, seeds[:, j]] = True
        for j in range(4):
            div_win[ar, winners[:, j]] = True
        top_seed[ar, seeds[:, 0]] = True
        wc = []
        for hi, lo in ((1, 6), (2, 5), (3, 4)):
            w = game(seeds[:, hi], seeds[:, lo], 1)
            wc.append((w, np.where(w == seeds[:, hi], hi, lo)))
        T = np.stack([w for w, _ in wc], axis=1); S = np.stack([s for _, s in wc], axis=1)
        o = np.argsort(S, axis=1)
        T = np.take_along_axis(T, o, axis=1); S = np.take_along_axis(S, o, axis=1)
        d1 = game(seeds[:, 0], T[:, 2], 1)                                     # 1 seed hosts the lowest seed left
        d2 = game(T[:, 0], T[:, 1], 1)                                         # higher seed hosts
        s_d2 = np.where(d2 == T[:, 0], S[:, 0], S[:, 1])
        s_d1 = np.where(d1 == seeds[:, 0], 0, S[:, 2])
        d1_home = s_d1 < s_d2
        cc = np.where(d1_home, game(d1, d2, 1), game(d2, d1, 1))
        conf_win[ar, cc] = True
        champs.append(cc)
    sb = game(champs[0], champs[1], 0)
    title = np.bincount(sb, minlength=len(tids)) / n

    gp = np.maximum(w0 + l0 + t0, 1)
    diff = (pf - pa) / gp
    rk = lambda v: {tids[i]: r + 1 for r, i in enumerate(np.argsort(-v))}  # noqa: E731
    diff_rank, rate_rank, pf_rank, pa_rank = rk(diff), rk(rating), rk(pf / gp), rk(-pa / gp)
    rec_rank = rk((w0 + 0.5 * t0) / gp)
    out = []
    for t in teams:
        i = ix[t["team_id"]]
        rec = f"{int(w0[i])}-{int(l0[i])}" + (f"-{int(t0[i])}" if t0[i] else "")
        out.append({
            "team_id": t["team_id"], "abbr": t["abbreviation"], "name": t["full_name"], "short": t["name"],
            "conference": t["conference"], "division": t["division"], "p_title": round(float(title[i]), 4),
            "p_conference": round(float(conf_win[:, i].mean()), 4), "p_playoffs": round(float(made[:, i].mean()), 4),
            "p_division": round(float(div_win[:, i].mean()), 4), "p_top_seed": round(float(top_seed[:, i].mean()), 4),
            "projected_wins": round(float(wins[:, i].mean()), 1),
            "stats": {"record": rec, "record_rank": rec_rank[t["team_id"]], "point_diff": round(float(diff[i]), 1),
                      "point_diff_rank": diff_rank[t["team_id"]], "ppg": round(float(pf[i] / gp[i]), 1),
                      "ppg_rank": pf_rank[t["team_id"]], "opp_ppg": round(float(pa[i] / gp[i]), 1),
                      "opp_ppg_rank": pa_rank[t["team_id"]], "rating": round(float(rating[i]), 1),
                      "rating_rank": rate_rank[t["team_id"]], "last5": "".join(form[t["team_id"]][-5:])},
        })
    out.sort(key=lambda x: -x["p_title"])
    fav = out[0]
    st = fav["stats"]
    reasons = [
        f"{st['record']} record ({_ordinal(st['record_rank'])} in the league), last five: {' '.join(st['last5'])}",
        f"{st['point_diff']:+.1f} points per game scoring margin ({_ordinal(st['point_diff_rank'])})",
        f"Scoring {st['ppg']} per game ({_ordinal(st['ppg_rank'])}) and allowing {st['opp_ppg']} ({_ordinal(st['opp_ppg_rank'])})",
        f"Our model rates them {st['rating']:+.1f} points better than an average team on a neutral field ({_ordinal(st['rating_rank'])})",
        f"Make the playoffs in {_pct(fav['p_playoffs'])} of simulations and earn the {fav['conference']} No. 1 seed (the only first-round bye) in {_pct(fav['p_top_seed'])}",
        f"Win the {fav['conference']} in {_pct(fav['p_conference'])} of simulations",
    ]
    reasons_es = [
        f"Récord de {st['record']} ({_o(st['record_rank'])} de la liga), últimos cinco: {_form_es(st['last5'])}",
        f"Diferencia de {st['point_diff']:+.1f} puntos por partido ({_o(st['point_diff_rank'])})",
        f"Anotan {st['ppg']} por partido ({_o(st['ppg_rank'])}) y reciben {st['opp_ppg']} ({_o(st['opp_ppg_rank'])})",
        f"Nuestro modelo los considera {st['rating']:+.1f} puntos mejores que un equipo promedio en campo neutral ({_o(st['rating_rank'])})",
        f"Llegan a playoffs en el {_pct(fav['p_playoffs'])} de las simulaciones y obtienen el primer sembrado de la {fav['conference']} (el único descanso en la primera ronda) en el {_pct(fav['p_top_seed'])}",
        f"Ganan la {fav['conference']} en el {_pct(fav['p_conference'])} de las simulaciones",
    ]
    es = {"title_es": "Campeón del Super Bowl", "event_es": f"Super Bowl {_roman(season - 1965)}", "reasons_es": reasons_es,
          "method_es": f"{_n(n)} simulaciones de los {len(preds)} partidos restantes de la temporada regular y los playoffs de 14 equipos."}
    payload = {**es, "title": "Super Bowl champion", "event": f"Super Bowl {_roman(season - 1965)}",
               "season_label": str(season), "status": "simulated", "remaining_games": len(preds), "teams": out,
               "reasons": reasons,
               "method": f"{n:,} simulations of the remaining {len(preds)} regular-season games and the 14-team playoff."}
    if save:
        _save(engine, "nfl", "nfl", season, payload, n)
    return {"competition": "nfl", "favorite": fav["name"], "p": fav["p_title"], "remaining_games": len(preds),
            "teams": None if save else out}


def _roman(x: int) -> str:
    out = ""
    for v, s in ((50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while x >= v:
            out += s; x -= v
    return out


# ---------------------------------------------------------------- soccer

SOCCER_SHOCK = 0.18   # sd of a club's log-strength uncertainty over the rest of the season (≈ ±18% goals)


@dataclass
class _Soccer:
    league: object
    season: int
    fs: object
    model: object
    teams: dict[int, dict]


def _soccer_setup(engine: Engine, slug: str) -> _Soccer:
    from polypicker.ml.pipeline import load_model
    from polypicker.soccer.leagues import get_league
    from polypicker.soccer.ml import pipeline as SP
    lg = get_league(slug)
    fs = SP.build_features(engine, lg)
    season = int(fs.games.filter(pl.col("status") == "final")["season"].max())
    model = load_model(engine, SP.active_model_id(engine, lg))
    teams = {r["team_id"]: r for r in _rows(engine, "SELECT team_id, name, short_name, abbreviation, color FROM soccer_teams")}
    return _Soccer(lg, season, fs, model, teams)


def _team(s: _Soccer, tid: int) -> dict:
    t = s.teams[tid]
    return {"team_id": tid, "name": t["name"], "short": t["short_name"] or t["name"], "abbr": t["abbreviation"],
            "color": t["color"]}


def _xg(s: _Soccer, game_ids: list[int]) -> dict[int, dict]:
    if not game_ids:
        return {}
    return {p["game_id"]: p for p in s.model.predict_games(s.fs.design.filter(pl.col("game_id").is_in(game_ids)))}


class _Table:
    """Simulated points / goal difference for a set of clubs across n simulations."""

    def __init__(self, tids: list[int], n: int, rng):
        self.tids, self.n, self.rng = tids, n, rng
        self.ix = {t: i for i, t in enumerate(tids)}
        self.pts = np.zeros((n, len(tids))); self.gd = np.zeros((n, len(tids))); self.gf = np.zeros((n, len(tids)))

    def add_result(self, h: int, a: int, hg, ag):
        i, j = self.ix[h], self.ix[a]
        hg = np.asarray(hg, float); ag = np.asarray(ag, float)
        self.pts[:, i] += np.where(hg > ag, 3, np.where(hg == ag, 1, 0))
        self.pts[:, j] += np.where(ag > hg, 3, np.where(hg == ag, 1, 0))
        self.gd[:, i] += hg - ag; self.gd[:, j] += ag - hg
        self.gf[:, i] += hg; self.gf[:, j] += ag

    def order(self) -> np.ndarray:
        key = self.pts * 1e6 + (self.gd + 500) * 1e3 + self.gf + self.rng.random(self.pts.shape)
        return np.argsort(-key, axis=1)          # (n, teams) column indices, 1st place first


def _sim_match(rng, lh, la):
    return rng.poisson(lh), rng.poisson(la)


def _current(engine: Engine, comp: str, season: int, stage_sql: str = "TRUE") -> list[dict]:
    return _rows(engine, f"""SELECT game_id, home_team_id, away_team_id, home_score, away_score, status, kickoff_utc, stage,
                                    coalesce(neutral, false) neutral, decided, home_shootout, away_shootout
                             FROM soccer_games WHERE competition = :c AND season = :s AND status NOT IN ('canceled')
                               AND {stage_sql} ORDER BY kickoff_utc""", c=comp, s=season)


def _league_stats(s: _Soccer, finals: list[dict], tids: list[int]) -> dict[int, dict]:
    st = {t: {"p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "form": []} for t in tids}
    for g in finals:
        for t, gf, ga in ((g["home_team_id"], g["home_score"], g["away_score"]), (g["away_team_id"], g["away_score"], g["home_score"])):
            if t not in st:
                continue
            x = st[t]; x["p"] += 1; x["gf"] += gf; x["ga"] += ga
            r = "W" if gf > ga else "D" if gf == ga else "L"
            x["w" if r == "W" else "d" if r == "D" else "l"] += 1; x["form"].append(r)
    for x in st.values():
        x["pts"] = 3 * x["w"] + x["d"]; x["gd"] = x["gf"] - x["ga"]; x["form"] = "".join(x["form"][-5:])
    return st


def _clinched(current: dict[int, int], left_per_team: dict[int, int]) -> int | None:
    """The leader can't be caught: more points now than anyone else could reach."""
    leader = max(current, key=current.get)
    best_other = max((current[t] + 3 * left_per_team.get(t, 0) for t in current if t != leader), default=-1)
    return leader if current[leader] > best_other else None


def _rating_stats(gr: GoalRatings, t: int) -> tuple[float, int]:
    """Expected goal difference per match against an average club on a neutral pitch, and its rank."""
    egd = np.exp(gr.base + gr.att) - np.exp(gr.base - gr.dfn)
    order = np.argsort(-egd)
    i = gr.idx(t)
    return round(float(egd[i]), 2), int(np.where(order == i)[0][0]) + 1


def _soccer_reasons_league(fav: dict, st: dict, pos: int, lead: int, n_teams: int) -> list[str]:
    gp = max(st["p"], 1)
    r = [f"{_ordinal(pos)} in the table on {st['pts']} points after {st['p']} matches ({st['w']}W {st['d']}D {st['l']}L)"
         + (f", {lead} point{'s' if lead != 1 else ''} clear" if pos == 1 and lead > 0 else f", {-lead} behind the leaders" if lead < 0 else ""),
         f"Goal difference {st['gd']:+d}: scoring {st['gf'] / gp:.2f} and conceding {st['ga'] / gp:.2f} per match",
         f"Our model expects them to outscore an average club by {fav['stats']['rating']:+.2f} goals a match ({_ordinal(fav['stats']['rating_rank'])})"]
    if st["form"]:
        r.append(f"Recent form (latest last): {' '.join(st['form'])}")
    r.append(f"Projected to finish on about {round(fav['projected_points'])} points; finish top four in {_pct(fav['p_top4'])} of simulations")
    return r


def _soccer_reasons_league_es(fav: dict, st: dict, pos: int, lead: int) -> list[str]:
    gp = max(st["p"], 1)
    r = [f"{_o(pos)} en la tabla con {st['pts']} puntos tras {st['p']} partidos ({st['w']}G {st['d']}E {st['l']}P)"
         + (f", {lead} punto{'s' if lead != 1 else ''} de ventaja" if pos == 1 and lead > 0 else f", a {-lead} del líder" if lead < 0 else ""),
         f"Diferencia de goles {st['gd']:+d}: anotan {st['gf'] / gp:.2f} y reciben {st['ga'] / gp:.2f} por partido",
         f"Nuestro modelo espera que superen a un club promedio por {fav['stats']['rating']:+.2f} goles por partido ({_o(fav['stats']['rating_rank'])})"]
    if st["form"]:
        r.append(f"Forma reciente (la más reciente al final): {_form_es(st['form'])}")
    r.append(f"Proyectados para terminar con unos {round(fav['projected_points'])} puntos; terminan entre los cuatro primeros en el {_pct(fav['p_top4'])} de las simulaciones")
    return r


def simulate_soccer_league(engine: Engine, slug: str, n: int = N_SIMS, seed: int = 3) -> dict:
    rng = np.random.default_rng(seed)
    s = _soccer_setup(engine, slug)
    games = _current(engine, s.league.competition, s.season)
    finals = [g for g in games if g["status"] == "final" and g["home_score"] is not None]
    left = [g for g in games if g["status"] != "final"]
    tids = sorted({g["home_team_id"] for g in games} | {g["away_team_id"] for g in games})
    xg = _xg(s, [g["game_id"] for g in left])
    gr = fit_goal_ratings([(p["home_team_id"], p["away_team_id"], p["xg_home"], p["xg_away"], False) for p in xg.values()], tids)
    shock = rng.normal(0, SOCCER_SHOCK, (n, len(tids)))
    tab = _Table(tids, n, rng)
    for g in finals:
        tab.add_result(g["home_team_id"], g["away_team_id"], np.full(n, g["home_score"]), np.full(n, g["away_score"]))
    for g in left:
        h, a = g["home_team_id"], g["away_team_id"]
        p = xg.get(g["game_id"])
        i, j = gr.idx(h), gr.idx(a)
        lh = p["xg_home"] if p else np.exp(gr.base + gr.hfa + gr.att[i] - gr.dfn[j])
        la = p["xg_away"] if p else np.exp(gr.base + gr.att[j] - gr.dfn[i])
        e = shock[:, i] - shock[:, j]
        tab.add_result(h, a, *_sim_match(rng, lh * np.exp(e), la * np.exp(-e)))
    order = tab.order()
    ar = np.arange(n)
    title = np.bincount(order[:, 0], minlength=len(tids)) / n
    pos = np.empty_like(order); pos[ar[:, None], order] = np.arange(len(tids))[None, :]
    st = _league_stats(s, finals, tids)
    table_now = sorted(tids, key=lambda t: (-st[t]["pts"], -st[t]["gd"], -st[t]["gf"]))
    out = []
    for t in tids:
        i = tab.ix[t]
        rating, rr = _rating_stats(gr, t)
        out.append({**_team(s, t), "p_title": round(float(title[i]), 4), "p_top4": round(float((pos[:, i] < 4).mean()), 4),
                    "p_relegation": round(float((pos[:, i] >= len(tids) - 3).mean()), 4),
                    "projected_points": round(float(tab.pts[:, i].mean()), 1),
                    "stats": {"position": table_now.index(t) + 1, "points": st[t]["pts"], "played": st[t]["p"],
                              "won": st[t]["w"], "drawn": st[t]["d"], "lost": st[t]["l"], "gd": st[t]["gd"],
                              "gf": st[t]["gf"], "ga": st[t]["ga"], "form": st[t]["form"], "rating": rating, "rating_rank": rr}})
    out.sort(key=lambda x: -x["p_title"])
    fav = out[0]
    fs_ = st[fav["team_id"]]
    pos_now = fav["stats"]["position"]
    other = table_now[1] if pos_now == 1 else table_now[0]
    lead = fs_["pts"] - st[other]["pts"]
    left_n = {t: 0 for t in tids}
    for g in left:
        left_n[g["home_team_id"]] += 1; left_n[g["away_team_id"]] += 1
    clinched = _clinched({t: st[t]["pts"] for t in tids}, left_n)
    es = {"title_es": f"Campeón de {s.league.name}", "event_es": s.league.name,
          "reasons_es": _soccer_reasons_league_es(fav, fs_, pos_now, lead),
          "method_es": f"{_n(n)} simulaciones de los {len(left)} partidos restantes. Los empates a puntos se resuelven por diferencia de goles y luego goles anotados."}
    payload = {**es, "title": f"{s.league.name} champion", "event": s.league.name, "season_label": s.league.season_label(s.season),
               "status": "clinched" if clinched else "simulated", "champion_id": clinched, "remaining_games": len(left), "teams": out,
               "reasons": _soccer_reasons_league(fav, fs_, pos_now, lead, len(tids)),
               "method": f"{n:,} simulations of the remaining {len(left)} matches. Ties on points split by goal difference, then goals scored."}
    _save(engine, "soccer", slug, s.season, payload, n)
    return {"competition": slug, "favorite": fav["name"], "p": fav["p_title"], "remaining_games": len(left)}


def _two_legs(rng, gr: GoalRatings, strength_shock, first_home, second_home, n):
    """Winner of a two-legged tie; second_home hosts the second leg. Level aggregate: coin flip (extra time / pens)."""
    ar = np.arange(n)
    a, b = first_home, second_home
    def lam(h, aw):
        e = strength_shock[ar, h] - strength_shock[ar, aw]
        return (np.exp(gr.base + gr.hfa + gr.att[h] - gr.dfn[aw] + e), np.exp(gr.base + gr.att[aw] - gr.dfn[h] - e))
    l1h, l1a = lam(a, b)
    l2h, l2a = lam(b, a)
    g1a, g1b = rng.poisson(l1h), rng.poisson(l1a)
    g2b, g2a = rng.poisson(l2h), rng.poisson(l2a)
    agg = (g1a + g2a) - (g1b + g2b)
    return np.where(agg > 0, a, np.where(agg < 0, b, np.where(rng.random(n) < 0.5, a, b)))


def _one_match_neutral(rng, gr: GoalRatings, shock, a, b, n):
    ar = np.arange(n)
    e = shock[ar, a] - shock[ar, b]
    ga = rng.poisson(np.exp(gr.base + gr.att[a] - gr.dfn[b] + e)); gb = rng.poisson(np.exp(gr.base + gr.att[b] - gr.dfn[a] - e))
    return np.where(ga > gb, a, np.where(gb > ga, b, np.where(rng.random(n) < 0.5, a, b)))


KO_STAGES = ("knockout-round-playoffs", "round-of-16", "quarterfinals", "semifinals", "final")
KO_NAMES = {"knockout-round-playoffs": ("knockout play-offs", "playoffs de eliminatoria"), "round-of-16": ("round of 16", "octavos de final"),
            "quarterfinals": ("quarter-finals", "cuartos de final"), "semifinals": ("semi-finals", "semifinales"), "final": ("final", "final")}


def _ko_ties(games: list[dict], stage: str, members: set[int]) -> list[dict]:
    """The drawn ties of one knockout round (empty until the draw has put real clubs in every fixture)."""
    by: dict[frozenset, list[dict]] = {}
    for g in games:
        if g["stage"] != stage:
            continue
        if g["home_team_id"] not in members or g["away_team_id"] not in members:
            return []                                   # placeholder sides: not drawn yet
        by.setdefault(frozenset((g["home_team_id"], g["away_team_id"])), []).append(g)
    return [{"stage": stage, "teams": tuple(sorted(k)), "legs": sorted(v, key=lambda g: g["kickoff_utc"])} for k, v in by.items()]


def _tie_state(tie: dict, team: int) -> tuple[int, int, bool]:
    """(goals for, goals against, every leg played) for ``team`` over the legs played so far (extra time included)."""
    gf = ga = 0
    done = True
    for g in tie["legs"]:
        if g["status"] != "final" or g["home_score"] is None:
            done = False
            continue
        home = g["home_team_id"] == team
        gf += g["home_score"] if home else g["away_score"]
        ga += g["away_score"] if home else g["home_score"]
    return gf, ga, done


def _tie_outcome(rng, gr: GoalRatings, shock, tie: dict, n: int) -> np.ndarray:
    """Winner (rating index, per simulation) of a real tie: legs already played count, the rest are simulated.
    A level aggregate after the last leg went to extra time / penalties: the real shootout if played, else 50/50."""
    a, b = tie["teams"]
    ia, ib = gr.idx(a), gr.idx(b)
    ar = np.arange(n)
    ga, gb = np.zeros(n), np.zeros(n)
    final_leg = tie["stage"] == "final"
    for g in tie["legs"]:
        h, aw = g["home_team_id"], g["away_team_id"]
        if g["status"] == "final" and g["home_score"] is not None:
            hs, as_ = float(g["home_score"]), float(g["away_score"])
        else:
            ih, iw = gr.idx(h), gr.idx(aw)
            e = shock[ar, ih] - shock[ar, iw]
            hfa = 0.0 if (final_leg or g.get("neutral")) else gr.hfa
            hs = rng.poisson(np.exp(gr.base + hfa + gr.att[ih] - gr.dfn[iw] + e))
            as_ = rng.poisson(np.exp(gr.base + gr.att[iw] - gr.dfn[ih] - e))
        if h == a:
            ga = ga + hs; gb = gb + as_
        else:
            ga = ga + as_; gb = gb + hs
    level = np.where(rng.random(n) < 0.5, ia, ib)
    last = tie["legs"][-1]
    if last["status"] == "final" and last.get("home_shootout") is not None and last.get("away_shootout") is not None:
        home_won = last["home_shootout"] > last["away_shootout"]
        level = np.full(n, gr.idx(last["home_team_id"] if home_won else last["away_team_id"]))
    return np.where(ga > gb, ia, np.where(gb > ga, ib, level))


def _resolve_round(actual: list[dict], default_pairs: list[tuple], play_default, play_actual) -> list[np.ndarray]:
    """Winners of every slot of a round. Drawn ties replace the simulated pairing: each goes to the bracket slot whose
    possible clubs include both of its sides (so later rounds follow the real path); any left over fill the rest."""
    if not actual:
        return [play_default(a, b) for a, b in default_pairs]
    cands = [set(np.unique(a).tolist()) | set(np.unique(b).tolist()) for a, b in default_pairs]
    slots: list = [None] * len(default_pairs)
    rest = []
    for tie in actual:
        ti = set(tie["_idx"])
        j = next((j for j, c in enumerate(cands) if slots[j] is None and ti <= c), None)
        if j is None:
            rest.append(tie)
        else:
            slots[j] = play_actual(tie)
    for j in range(len(slots)):
        if slots[j] is None:
            slots[j] = play_actual(rest.pop(0)) if rest else play_default(*default_pairs[j])
    return slots


def _as_of_view(games: list[dict], as_of) -> list[dict]:
    """The competition as it stood at ``as_of`` (for replaying a past season): later results hidden, and a knockout
    round's fixtures only once the previous round had finished (its draw done)."""
    end: dict[str, object] = {}
    for g in games:
        st = g["stage"] or "league-phase"
        end[st] = max(end.get(st, g["kickoff_utc"]), g["kickoff_utc"])
    order = ("league-phase", *KO_STAGES)
    out = []
    for g in games:
        st = g["stage"] or "league-phase"
        if st in KO_STAGES:
            prev = order[order.index(st) - 1]
            if prev in end and end[prev] >= as_of:
                continue
        if g["kickoff_utc"] > as_of:
            g = {**g, "status": "scheduled", "home_score": None, "away_score": None, "home_shootout": None, "away_shootout": None}
        out.append(g)
    return out


def simulate_uefa(engine: Engine, slug: str, n: int = N_SIMS, seed: int = 5, *, season: int | None = None,
                  as_of=None, save: bool = True) -> dict:
    """Champions / Europa League: 36-club league phase, knockout play-offs (9th-24th), then a seeded bracket.

    Rounds that have been drawn use the real ties and results (legs still to play are simulated, a level aggregate
    goes 50/50 unless the shootout was played); rounds not drawn yet use UEFA's seeding pairs. Once the final has
    been played the competition is ``decided``."""
    rng = np.random.default_rng(seed)
    s = _soccer_setup(engine, slug)
    if season is not None:
        s.season = season
    games = _current(engine, s.league.competition, s.season)
    if as_of is not None:
        games = _as_of_view(games, as_of)
    lp = [g for g in games if g["stage"] == "league-phase"]
    finals = [g for g in lp if g["status"] == "final" and g["home_score"] is not None]
    left = [g for g in lp if g["status"] != "final"]
    tids = sorted({g["home_team_id"] for g in lp} | {g["away_team_id"] for g in lp})
    members = set(tids)
    ko = {st: _ko_ties(games, st, members) for st in KO_STAGES}
    # Ratings from the model's expected goals: the matches still to play, or the whole season once few remain
    # (after the league phase only a handful of knockout fixtures are scheduled at a time).
    todo = [g["game_id"] for g in games if g["status"] != "final"]
    xg_all = _xg(s, todo if len(todo) >= 30 else [g["game_id"] for g in games])
    neutral = {g["game_id"]: g["stage"] == "final" or bool(g.get("neutral")) for g in games}
    gr = fit_goal_ratings([(p["home_team_id"], p["away_team_id"], p["xg_home"], p["xg_away"], neutral.get(p["game_id"], False))
                           for p in xg_all.values() if p["home_team_id"] in members and p["away_team_id"] in members], tids)
    xg = {gid: xg_all[gid] for gid in (g["game_id"] for g in left) if gid in xg_all}
    shock = rng.normal(0, SOCCER_SHOCK, (n, len(tids)))
    tab = _Table(tids, n, rng)
    for g in finals:
        tab.add_result(g["home_team_id"], g["away_team_id"], np.full(n, g["home_score"]), np.full(n, g["away_score"]))
    for g in left:
        i, j = gr.idx(g["home_team_id"]), gr.idx(g["away_team_id"])
        p = xg.get(g["game_id"])
        lh = p["xg_home"] if p else np.exp(gr.base + gr.hfa + gr.att[i] - gr.dfn[j])
        la = p["xg_away"] if p else np.exp(gr.base + gr.att[j] - gr.dfn[i])
        e = shock[:, i] - shock[:, j]
        tab.add_result(g["home_team_id"], g["away_team_id"], *_sim_match(rng, lh * np.exp(e), la * np.exp(-e)))
    order = tab.order()                                     # column indices == rating indices (same sorted tids)
    ar = np.arange(n)
    seed_ = lambda k: order[:, k - 1]                       # noqa: E731
    for st in KO_STAGES:
        for tie in ko[st]:
            tie["_idx"] = [gr.idx(t) for t in tie["teams"]]
    two_legs = lambda a, b: _two_legs(rng, gr, shock, a, b, n)  # noqa: E731  (b, the seeded side, hosts leg 2)
    real = lambda tie: _tie_outcome(rng, gr, shock, tie, n)     # noqa: E731
    # Knockout play-offs: 9/10 v 23/24, 11/12 v 21/22, 13/14 v 19/20, 15/16 v 17/18; seeded side hosts leg 2.
    keys = (((9, 10), (23, 24)), ((11, 12), (21, 22)), ((13, 14), (19, 20)), ((15, 16), (17, 18)))
    po_pairs = []
    for hi, lo in keys:
        x = rng.random(n) < 0.5
        h1, h2 = np.where(x, seed_(hi[0]), seed_(hi[1])), np.where(x, seed_(hi[1]), seed_(hi[0]))
        y = rng.random(n) < 0.5
        l1, l2 = np.where(y, seed_(lo[0]), seed_(lo[1])), np.where(y, seed_(lo[1]), seed_(lo[0]))
        po_pairs += [(l1, h1), (l2, h2)]
    po_w = _resolve_round(ko["knockout-round-playoffs"], po_pairs, two_legs, real)
    # Round of 16: 1/2 v play-off winners from 15-18, 3/4 v 13-14/19-20, 5/6 v 11-12/21-22, 7/8 v 9-10/23-24.
    po = {hi: (po_w[2 * k], po_w[2 * k + 1]) for k, (hi, _) in enumerate(keys)}
    r16_pairs = []
    for top, key in (((1, 2), (15, 16)), ((3, 4), (13, 14)), ((5, 6), (11, 12)), ((7, 8), (9, 10))):
        x = rng.random(n) < 0.5
        a1, a2 = po[key]
        r16_pairs += [(np.where(x, a1, a2), seed_(top[0])), (np.where(x, a2, a1), seed_(top[1]))]
    r16 = _resolve_round(ko["round-of-16"], r16_pairs, two_legs, real)
    # Fixed bracket: 1 and 2 in opposite halves.
    qf = _resolve_round(ko["quarterfinals"], [(r16[6], r16[0]), (r16[4], r16[2]), (r16[7], r16[1]), (r16[5], r16[3])], two_legs, real)
    sf = _resolve_round(ko["semifinals"], [(qf[1], qf[0]), (qf[3], qf[2])], two_legs, real)
    champ = _resolve_round(ko["final"], [(sf[0], sf[1])], lambda a, b: _one_match_neutral(rng, gr, shock, a, b, n), real)[0]
    k = len(tids)
    cnt = lambda arrs: sum(np.bincount(x, minlength=k) for x in arrs) / n  # noqa: E731
    p_title = np.bincount(champ, minlength=k) / n
    p_final, p_sf, p_qf = cnt(sf), cnt(qf), cnt(r16)
    pos = np.empty_like(order); pos[ar[:, None], order] = np.arange(k)[None, :]
    st = _league_stats(s, finals, tids)
    table_now = sorted(tids, key=lambda t: (-st[t]["pts"], -st[t]["gd"], -st[t]["gf"]))
    out = []
    for t in tids:
        i = tids.index(t)
        rating, rr = _rating_stats(gr, t)
        out.append({**_team(s, t), "p_title": round(float(p_title[i]), 4), "p_final": round(float(p_final[i]), 4),
                    "p_semis": round(float(p_sf[i]), 4), "p_quarters": round(float(p_qf[i]), 4),
                    "p_top8": round(float((pos[:, i] < 8).mean()), 4), "p_knockouts": round(float((pos[:, i] < 24).mean()), 4),
                    "projected_points": round(float(tab.pts[:, i].mean()), 1),
                    "stats": {"position": table_now.index(t) + 1, "points": st[t]["pts"], "played": st[t]["p"],
                              "won": st[t]["w"], "drawn": st[t]["d"], "lost": st[t]["l"], "gd": st[t]["gd"],
                              "gf": st[t]["gf"], "ga": st[t]["ga"], "form": st[t]["form"], "rating": rating, "rating_rank": rr}})
    out.sort(key=lambda x: -x["p_title"])
    fav = out[0]
    x = fav["stats"]
    gp = max(x["played"], 1)
    decided = bool(ko["final"]) and all(g["status"] == "final" for g in ko["final"][0]["legs"])
    ko_started = any(ko[st_] for st_ in KO_STAGES)
    reasons = [
        f"{_ordinal(x['position'])} of 36 in the league phase: {x['points']} points from {x['played']} matches ({x['won']}W {x['drawn']}D {x['lost']}L)",
        f"Goal difference {x['gd']:+d}: scoring {x['gf'] / gp:.2f} and conceding {x['ga'] / gp:.2f} per match",
        f"Our model rates them {x['rating']:+.2f} goals a match better than an average {s.league.name} side ({_ordinal(x['rating_rank'])} of 36)",
    ]
    reasons_es = [
        f"{_o(x['position'])} de 36 en la fase de liga: {x['points']} puntos en {x['played']} partidos ({x['won']}G {x['drawn']}E {x['lost']}P)",
        f"Diferencia de goles {x['gd']:+d}: anotan {x['gf'] / gp:.2f} y reciben {x['ga'] / gp:.2f} por partido",
        f"Nuestro modelo los considera {x['rating']:+.2f} goles por partido mejores que un equipo promedio de la {s.league.name} ({_o(x['rating_rank'])} de 36)",
    ]
    ko_line = _ko_reason(s, ko, fav["team_id"])
    if ko_line:
        reasons.insert(0, ko_line[0]); reasons_es.insert(0, ko_line[1])
    if not ko_started:
        reasons.append(f"Finish in the top eight (straight to the round of 16) in {_pct(fav['p_top8'])} of simulations")
        reasons_es.append(f"Terminan entre los ocho primeros (directo a octavos de final) en el {_pct(fav['p_top8'])} de las simulaciones")
    if not decided:
        reasons.append(f"Reach the quarter-finals in {_pct(fav['p_quarters'])}, the final in {_pct(fav['p_final'])}")
        reasons_es.append(f"Llegan a cuartos de final en el {_pct(fav['p_quarters'])} y a la final en el {_pct(fav['p_final'])}")
    drawn = [KO_NAMES[st_] for st_ in KO_STAGES if ko[st_]]
    if ko_started:
        method = (f"{n:,} simulations of the knockout rounds. Drawn ties use the real fixtures and results "
                  f"({', '.join(d[0] for d in drawn)}); legs still to play are simulated, and rounds not drawn yet follow UEFA's seeding pairs.")
        method_es = (f"{_n(n)} simulaciones de las eliminatorias. Los cruces ya sorteados usan los partidos y resultados reales "
                     f"({', '.join(d[1] for d in drawn)}); los partidos pendientes se simulan y las rondas aún no sorteadas siguen los pares de cabezas de serie de la UEFA.")
    else:
        method = (f"{n:,} simulations of the remaining {len(left)} league-phase matches, the knockout play-offs and a seeded "
                  "bracket to the final. The real knockout draw isn't made yet, so pairings are drawn at random within UEFA's seeding pairs.")
        method_es = (f"{_n(n)} simulaciones de los {len(left)} partidos restantes de la fase de liga, los playoffs de eliminatoria y un cuadro "
                     "con cabezas de serie hasta la final. El sorteo real aún no se ha hecho, así que los cruces se sortean al azar dentro de los pares de cabezas de serie de la UEFA.")
    es = {"title_es": f"Ganador de la {s.league.name}", "event_es": f"Final de la {s.league.name}", "reasons_es": reasons_es, "method_es": method_es}
    payload = {**es, "title": f"{s.league.name} winner", "event": f"{s.league.name} final", "season_label": s.league.season_label(s.season),
               "status": "decided" if decided else "simulated", "remaining_games": len(left) + sum(
                   1 for st_ in KO_STAGES for tie in ko[st_] for g in tie["legs"] if g["status"] != "final"),
               "teams": out, "reasons": reasons, "method": method}
    if decided:
        payload["champion_id"] = fav["team_id"]
    if save:
        _save(engine, "soccer", slug, s.season, payload, n)
    return {"competition": slug, "favorite": fav["name"], "p": fav["p_title"], "remaining_games": payload["remaining_games"],
            "status": payload["status"], "teams": out if not save else None}


def _ko_reason(s: _Soccer, ko: dict, team: int) -> tuple[str, str] | None:
    """The favourite's latest knockout tie in words (through / leading / facing), or None before the knockouts."""
    for st in reversed(KO_STAGES):
        tie = next((t for t in ko[st] if team in t["teams"]), None)
        if not tie:
            continue
        opp = s.teams[next(t for t in tie["teams"] if t != team)]
        on, oes = opp["short_name"] or opp["name"], opp["short_name"] or opp["name"]
        en, es_ = KO_NAMES[st]
        gf, ga, done = _tie_state(tie, team)
        played = any(g["status"] == "final" for g in tie["legs"])
        if done:
            if st == "final":
                return (f"Won the final against {on} ({gf}-{ga})", f"Ganaron la final ante {oes} ({gf}-{ga})")
            return (f"Knocked out {on} {gf}-{ga} on aggregate in the {en}", f"Eliminaron a {oes} {gf}-{ga} en el global de {es_}")
        if played:
            word = ("Lead", "Ganan") if gf > ga else ("Trail", "Pierden") if gf < ga else ("Level with", "Empatan con")
            return (f"{word[0]} {on} {gf}-{ga} after the first leg of the {en}", f"{word[1]} {gf}-{ga} ante {oes} tras la ida de {es_}")
        return (f"Face {on} in the {en}", f"Se enfrentan a {oes} en {es_}")
    return None


def _cup_record(engine: Engine, comp: str, season: int, tid: int) -> dict:
    gs = _rows(engine, """SELECT home_team_id, away_team_id, home_score, away_score, decided, home_shootout, away_shootout
                          FROM soccer_games WHERE competition = :c AND season = :s AND status = 'final'
                            AND (home_team_id = :t OR away_team_id = :t)""", c=comp, s=season, t=tid)
    w = d = l = gf = ga = 0
    for g in gs:
        mine, theirs = (g["home_score"], g["away_score"]) if g["home_team_id"] == tid else (g["away_score"], g["home_score"])
        gf += mine; ga += theirs
        if mine > theirs: w += 1
        elif mine < theirs: l += 1
        else: d += 1
    return {"played": len(gs), "won": w, "drawn": d, "lost": l, "gf": gf, "ga": ga}


def libertadores_or_sudamericana(engine: Engine, slug: str) -> dict:
    """South American cups end in a one-off final: decided -> record the champion, scheduled -> forecast it."""
    s = _soccer_setup(engine, slug)
    final = _current(engine, s.league.competition, s.season, "stage = 'final'")
    if not final:
        raise RuntimeError(f"{slug}: no final on the schedule yet; a full bracket simulation isn't built for this cup")
    g = final[0]
    h, a = g["home_team_id"], g["away_team_id"]
    rec = {t: _cup_record(engine, s.league.competition, s.season, t) for t in (h, a)}
    if g["status"] == "final":
        hs, as_ = g["home_score"], g["away_score"]
        if hs != as_:
            win = h if hs > as_ else a
        else:
            win = h if (g["home_shootout"] or 0) > (g["away_shootout"] or 0) else a
        lose = a if win == h else h
        score = f"{max(hs, as_)}-{min(hs, as_)}" if hs != as_ else f"{hs}-{as_} ({max(g['home_shootout'] or 0, g['away_shootout'] or 0)}-{min(g['home_shootout'] or 0, g['away_shootout'] or 0)} on penalties)"
        r = rec[win]
        teams = [{**_team(s, win), "p_title": 1.0, "stats": r}, {**_team(s, lose), "p_title": 0.0, "stats": rec[lose]}]
        reasons = [f"Beat {_team(s, lose)['short']} {score} in the final on {g['kickoff_utc']:%b %-d}",
                   f"Tournament record: {r['won']}W {r['drawn']}D {r['lost']}L across {r['played']} matches",
                   f"Scored {r['gf']} and conceded {r['ga']} along the way"]
        months = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
        score_es = score.replace("on penalties", "en penales")
        es = {"title_es": f"Campeón de la {s.league.name}", "event_es": f"Final de la {s.league.name}",
              "reasons_es": [f"Venció a {_team(s, lose)['short']} {score_es} en la final del {g['kickoff_utc'].day} {months[g['kickoff_utc'].month - 1]}",
                             f"Récord en el torneo: {r['won']}G {r['drawn']}E {r['lost']}P en {r['played']} partidos",
                             f"Anotó {r['gf']} y recibió {r['ga']} en el camino"],
              "method_es": "Final jugada; resultado tomado del feed de partidos."}
        payload = {**es, "title": f"{s.league.name} champion", "event": f"{s.league.name} final", "season_label": str(s.season),
                   "status": "decided", "remaining_games": 0, "teams": teams, "reasons": reasons, "champion_id": win,
                   "method": "Final played; result from the match feed."}
        _save(engine, "soccer", slug, s.season, payload, 0)
        return {"competition": slug, "champion": _team(s, win)["name"]}
    p = _xg(s, [g["game_id"]]).get(g["game_id"])
    if p is None:
        raise RuntimeError(f"{slug}: the model couldn't price the final")
    # Level after 90 minutes -> extra time and penalties: split in proportion to the sides' 90-minute strength.
    share = p["home"] / (p["home"] + p["away"])
    ph = p["home"] + p["draw"] * share
    teams = []
    for t, pt, win90 in ((h, ph, p["home"]), (a, 1 - ph, p["away"])):
        teams.append({**_team(s, t), "p_title": round(float(pt), 4), "p_win_90": round(float(win90), 4),
                      "xg": round(float(p["xg_home"] if t == h else p["xg_away"]), 2), "stats": rec[t]})
    teams.sort(key=lambda x: -x["p_title"])
    fav, dog = teams
    r = fav["stats"]
    venue = _rows(engine, "SELECT venue FROM soccer_games WHERE game_id = :g", g=g["game_id"])[0]["venue"]
    reasons = [f"Final vs {dog['short']} on {g['kickoff_utc']:%b %-d}" + (f" at {venue}" if venue else ""),
               f"Win inside 90 minutes in {_pct(fav['p_win_90'])} of cases, draw {_pct(p['draw'])} (then extra time and penalties)",
               f"Expected goals in the final: {fav['xg']} vs {dog['xg']}",
               f"Tournament record: {r['won']}W {r['drawn']}D {r['lost']}L, {r['gf']} scored and {r['ga']} conceded in {r['played']} matches"]
    for f in (p.get("factors") or [])[:2]:
        side = h if f["favors"] == "home" else a
        if side == fav["team_id"]:
            reasons.append(f"{f['factor']}: worth {abs(f['home_goal_edge']):.2f} goals to them")
    months = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    ko = g["kickoff_utc"]
    reasons_es = [f"Final ante {dog['short']} el {ko.day} {months[ko.month - 1]}" + (f" en el {venue}" if venue else ""),
                  f"Ganan en los 90 minutos en el {_pct(fav['p_win_90'])} de los casos; empate {_pct(p['draw'])} (luego prórroga y penales)",
                  f"Goles esperados en la final: {fav['xg']} contra {dog['xg']}",
                  f"Récord en el torneo: {r['won']}G {r['drawn']}E {r['lost']}P, {r['gf']} goles a favor y {r['ga']} en contra en {r['played']} partidos"]
    factor_es = {"Overall team rating (Elo)": "Calificación general del equipo (Elo)", "Recent scoring vs. opponent's defence": "Goles recientes frente a la defensa rival",
                 "Chance creation (shots on target)": "Creación de ocasiones (tiros a puerta)", "Shot volume": "Volumen de tiros",
                 "Season-long goal record": "Registro de goles de la temporada", "Home advantage": "Ventaja de local", "Rest days": "Días de descanso",
                 "Newly promoted": "Recién ascendido"}
    for f in (p.get("factors") or [])[:2]:
        side = h if f["favors"] == "home" else a
        if side == fav["team_id"]:
            reasons_es.append(f"{factor_es.get(f['factor'], f['factor'])}: les vale {abs(f['home_goal_edge']):.2f} goles")
    es = {"title_es": f"Campeón de la {s.league.name}", "event_es": f"Final de la {s.league.name}", "reasons_es": reasons_es,
          "method_es": "Pronóstico de nuestro modelo para la final a partido único; un empate tras 90 minutos se reparte según la fuerza de cada equipo."}
    payload = {**es, "title": f"{s.league.name} champion", "event": f"{s.league.name} final", "season_label": str(s.season),
               "status": "final_scheduled", "remaining_games": 1, "teams": teams, "reasons": reasons, "game_id": g["game_id"],
               "method": "Our match model's forecast for the one-off final; a draw after 90 minutes is split by the sides' strength."}
    _save(engine, "soccer", slug, s.season, payload, 0)
    return {"competition": slug, "favorite": fav["name"], "p": fav["p_title"]}


def simulate_ecuador(engine: Engine, n: int = N_SIMS, seed: int = 13) -> dict:
    """LigaPro: the top six play a ten-round hexagonal carrying their first-stage points; first place is champion."""
    rng = np.random.default_rng(seed)
    s = _soccer_setup(engine, "ecuador")
    comp = s.league.competition
    groups = _rows(engine, "SELECT team_id, group_name FROM soccer_groups WHERE competition = :c AND season = :s", c=comp, s=s.season)
    hexa = sorted(r["team_id"] for r in groups if r["group_name"] == "Group 1")
    if len(hexa) != 6:
        raise RuntimeError("ecuador: title hexagonal (Group 1, six clubs) not in the data yet")
    first = [g for g in _current(engine, comp, s.season, "stage = 'first-stage'") if g["status"] == "final"]
    fin = [g for g in _current(engine, comp, s.season, "stage = 'final-stage'")
           if g["home_team_id"] in hexa and g["away_team_id"] in hexa]
    played = [g for g in fin if g["status"] == "final" and g["home_score"] is not None]
    sched = [g for g in fin if g["status"] != "final"]
    seen = {(g["home_team_id"], g["away_team_id"]) for g in fin}
    missing = [(h, a) for h in hexa for a in hexa if h != a and (h, a) not in seen]   # double round robin
    all_left = [g for g in _current(engine, comp, s.season) if g["status"] != "final"]
    xg_all = _xg(s, [g["game_id"] for g in all_left])
    rated = sorted({p["home_team_id"] for p in xg_all.values()} | {p["away_team_id"] for p in xg_all.values()} | set(hexa))
    gr = fit_goal_ratings([(p["home_team_id"], p["away_team_id"], p["xg_home"], p["xg_away"], False) for p in xg_all.values()], rated)
    shock = rng.normal(0, SOCCER_SHOCK, (n, len(rated)))
    tab = _Table(hexa, n, rng)
    first_stats = _league_stats(s, first, hexa)
    for t in hexa:                                     # carried first-stage points / goals
        tab.pts[:, tab.ix[t]] += first_stats[t]["pts"]; tab.gd[:, tab.ix[t]] += first_stats[t]["gd"]; tab.gf[:, tab.ix[t]] += first_stats[t]["gf"]
    for g in played:
        tab.add_result(g["home_team_id"], g["away_team_id"], np.full(n, g["home_score"]), np.full(n, g["away_score"]))
    fixtures = [(g["home_team_id"], g["away_team_id"], xg_all.get(g["game_id"])) for g in sched] + [(h, a, None) for h, a in missing]
    for h, a, p in fixtures:
        i, j = gr.idx(h), gr.idx(a)
        lh = p["xg_home"] if p else np.exp(gr.base + gr.hfa + gr.att[i] - gr.dfn[j])
        la = p["xg_away"] if p else np.exp(gr.base + gr.att[j] - gr.dfn[i])
        e = shock[:, i] - shock[:, j]
        tab.add_result(h, a, *_sim_match(rng, lh * np.exp(e), la * np.exp(-e)))
    order = tab.order()
    title = np.bincount(order[:, 0], minlength=6) / n
    hex_stats = _league_stats(s, played, hexa)
    now_pts = {t: first_stats[t]["pts"] + hex_stats[t]["pts"] for t in hexa}
    now_gd = {t: first_stats[t]["gd"] + hex_stats[t]["gd"] for t in hexa}
    table_now = sorted(hexa, key=lambda t: (-now_pts[t], -now_gd[t]))
    out = []
    for t in hexa:
        i = tab.ix[t]
        rating, rr = _rating_stats(gr, t)
        h = hex_stats[t]
        out.append({**_team(s, t), "p_title": round(float(title[i]), 4), "projected_points": round(float(tab.pts[:, i].mean()), 1),
                    "stats": {"position": table_now.index(t) + 1, "points": now_pts[t], "first_stage_points": first_stats[t]["pts"],
                              "hex_points": h["pts"], "hex_played": h["p"], "won": h["w"], "drawn": h["d"], "lost": h["l"],
                              "gd": now_gd[t], "form": h["form"], "rating": rating, "rating_rank": rr,
                              "first_stage": first_stats[t]}})
    out.sort(key=lambda x: -x["p_title"])
    fav = out[0]
    x = fav["stats"]
    second = table_now[1] if table_now[0] == fav["team_id"] else table_now[0]
    gap = now_pts[fav["team_id"]] - now_pts[second]
    fs_ = x["first_stage"]
    reasons = [
        f"{_ordinal(x['position'])} in the title hexagonal on {x['points']} points"
        + (f", {gap} clear" if x["position"] == 1 and gap > 0 else f", {-gap} behind" if gap < 0 else ""),
        f"Carried {x['first_stage_points']} points from the first stage ({fs_['w']}W {fs_['d']}D {fs_['l']}L, goal difference {fs_['gd']:+d})",
        f"Hexagonal so far: {x['hex_points']} points from {x['hex_played']} matches" + (f", form {' '.join(x['form'])}" if x["form"] else ""),
        f"Our model rates them {x['rating']:+.2f} goals a match better than an average LigaPro side",
        f"{len(sched) + len(missing)} hexagonal matches left; projected to finish on about {round(fav['projected_points'])} points",
    ]
    left_n = {t: 0 for t in hexa}
    for h, a, _ in fixtures:
        left_n[h] += 1; left_n[a] += 1
    clinched = _clinched(now_pts, left_n)
    if clinched:
        reasons[-1] = f"Title clinched: no club can reach {now_pts[clinched]} points with {len(fixtures)} hexagonal matches left"
    reasons_es = [
        f"{_o(x['position'])} en el hexagonal por el título con {x['points']} puntos"
        + (f", {gap} de ventaja" if x["position"] == 1 and gap > 0 else f", a {-gap}" if gap < 0 else ""),
        f"Arrastró {x['first_stage_points']} puntos de la primera fase ({fs_['w']}G {fs_['d']}E {fs_['l']}P, diferencia de goles {fs_['gd']:+d})",
        f"Hexagonal hasta ahora: {x['hex_points']} puntos en {x['hex_played']} partidos" + (f", forma {_form_es(x['form'])}" if x["form"] else ""),
        f"Nuestro modelo los considera {x['rating']:+.2f} goles por partido mejores que un equipo promedio de la LigaPro",
        f"Quedan {len(fixtures)} partidos del hexagonal; proyectados para terminar con unos {round(fav['projected_points'])} puntos",
    ]
    if clinched:
        reasons_es[-1] = f"Título asegurado: ningún club puede alcanzar {now_pts[clinched]} puntos con {len(fixtures)} partidos del hexagonal por jugar"
    es = {"title_es": f"Campeón de la {s.league.name}", "event_es": "Hexagonal por el título", "reasons_es": reasons_es,
          "method_es": f"{_n(n)} simulaciones de los partidos restantes del hexagonal. Los puntos de la primera fase se arrastran; el líder tras diez fechas es campeón."}
    payload = {**es, "title": f"{s.league.name} champion", "event": "Title hexagonal", "season_label": str(s.season),
               "status": "clinched" if clinched else "simulated", "champion_id": clinched, "remaining_games": len(sched) + len(missing), "teams": out, "reasons": reasons,
               "method": f"{n:,} simulations of the remaining hexagonal matches. First-stage points carry over; the leader after ten rounds is champion."}
    _save(engine, "soccer", "ecuador", s.season, payload, n)
    return {"competition": "ecuador", "favorite": fav["name"], "p": fav["p_title"]}


SOCCER_RUNNERS = {
    "epl": simulate_soccer_league, "laliga": simulate_soccer_league, "bundesliga": simulate_soccer_league,
    "seriea": simulate_soccer_league, "ligue1": simulate_soccer_league,
    "ucl": simulate_uefa, "uel": simulate_uefa,
    "libertadores": libertadores_or_sudamericana, "sudamericana": libertadores_or_sudamericana,
}


def run_all(engine: Engine, n: int = N_SIMS, only: list[str] | None = None) -> dict:
    """Every competition; one failing doesn't stop the rest."""
    jobs = {"nba": lambda: simulate_nba(engine, n), "nfl": lambda: simulate_nfl(engine, n),
            "ecuador": lambda: simulate_ecuador(engine, n)}
    for slug, fn in SOCCER_RUNNERS.items():
        jobs[slug] = (lambda f=fn, sl=slug: f(engine, sl) if f is libertadores_or_sudamericana else f(engine, sl, n))
    out = {}
    for k, fn in jobs.items():
        if only and k not in only:
            continue
        try:
            out[k] = fn()
        except Exception as e:  # noqa: BLE001
            log.exception("futures %s failed", k)
            out[k] = {"error": str(e)[:300]}
    return out

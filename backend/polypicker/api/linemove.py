"""Line movement vs our picks, for the accuracy pages.

For every graded game with an opening and a closing price (bookmaker margin removed):

* **moved our way**: the price of our pick rose from open to close by at least ``FLAT`` (the market came round to
  our view, i.e. betting our pick at the opening price beat the closing line); **against** when it fell; **flat**
  otherwise. Hit rate of our pick in each group.
* **favourites**: how often the opening favourite and the closing favourite won.
* **following the move**: on games where one side's price rose by ``BIG`` or more, how often that side won,
  against what its closing price implied. Above the closing price = late moves carried information the close
  didn't fully price in.

Moves larger than ``MAX_MOVE`` are dropped: those are almost always a price captured after kickoff, not a real
pre-game move. Sources: NBA ESPN lines (``nba_odds``), NFL ESPN open/close (``nfl_market_prices`` source 'espn'),
soccer football-data.co.uk Pinnacle open/close (top-five leagues only).
"""

from __future__ import annotations

from sqlalchemy import Connection

from polypicker.api.deps import q

FLAT = 0.01
BIG = 0.05
MAX_MOVE = 0.25
SIDES2 = ("home", "away")
SIDES3 = ("home", "draw", "away")


def _no_vig(h: int | None, a: int | None) -> float | None:
    from polypicker.etl.sources.espn_odds import no_vig_home
    return no_vig_home(h, a)


def _rows_nba(conn: Connection, kind: str) -> list[dict]:
    rows = q(conn, """
        WITH p AS (SELECT DISTINCT ON (game_id) game_id, probability FROM predictions
                   WHERE kind = :k AND market = 'moneyline' AND side = 'home' ORDER BY game_id, revision DESC)
        SELECT g.game_id, g.season, p.probability ph, o.p_home_open, o.p_home, g.home_score, g.away_score
        FROM p JOIN games g USING (game_id) JOIN nba_odds o USING (game_id)
        WHERE g.status = 'final' AND o.p_home_open IS NOT NULL AND o.p_home IS NOT NULL AND g.home_score <> g.away_score""", k=kind)
    return [{"season": r["season"], "model": {"home": r["ph"], "away": 1 - r["ph"]},
             "open": {"home": r["p_home_open"], "away": 1 - r["p_home_open"]}, "close": {"home": r["p_home"], "away": 1 - r["p_home"]},
             "winner": "home" if r["home_score"] > r["away_score"] else "away"} for r in rows]


def _rows_nfl(conn: Connection, kind: str) -> list[dict]:
    rows = q(conn, """
        WITH p AS (SELECT DISTINCT ON (game_id) game_id, probability FROM nfl_predictions
                   WHERE kind = :k AND market = 'moneyline' AND side = 'home' ORDER BY game_id, revision DESC)
        SELECT g.game_id, g.season, p.probability ph, x.home_ml_open, x.away_ml_open, x.home_ml, x.away_ml, g.home_score, g.away_score
        FROM p JOIN nfl_games g USING (game_id) JOIN nfl_market_prices x ON x.game_id = g.game_id AND x.source = 'espn'
        WHERE g.status = 'final' AND g.home_score <> g.away_score""", k=kind)
    out = []
    for r in rows:
        po, pc = _no_vig(r["home_ml_open"], r["away_ml_open"]), _no_vig(r["home_ml"], r["away_ml"])
        if po is None or pc is None:
            continue
        out.append({"season": r["season"], "model": {"home": r["ph"], "away": 1 - r["ph"]}, "open": {"home": po, "away": 1 - po},
                    "close": {"home": pc, "away": 1 - pc}, "winner": "home" if r["home_score"] > r["away_score"] else "away"})
    return out


def _rows_soccer(conn: Connection, kind: str, competition: str) -> list[dict]:
    rows = q(conn, """
        WITH p AS (SELECT DISTINCT ON (game_id, side) game_id, side, probability FROM soccer_predictions
                   WHERE kind = :k AND market = 'match_result' ORDER BY game_id, side, revision DESC)
        SELECT g.game_id, g.season, g.result, f.p_home_open, f.p_draw_open, f.p_away_open, f.p_home, f.p_draw, f.p_away,
               max(p.probability) FILTER (WHERE p.side = 'home') mh, max(p.probability) FILTER (WHERE p.side = 'draw') md,
               max(p.probability) FILTER (WHERE p.side = 'away') ma
        FROM p JOIN soccer_games g USING (game_id) JOIN soccer_fdcuk_matches f USING (game_id)
        WHERE g.competition = :c AND g.status = 'final' AND g.result IS NOT NULL AND f.p_home_open IS NOT NULL AND f.p_home IS NOT NULL
        GROUP BY g.game_id, g.season, g.result, f.p_home_open, f.p_draw_open, f.p_away_open, f.p_home, f.p_draw, f.p_away""",
             k=kind, c=competition)
    return [{"season": r["season"], "model": {"home": r["mh"] or 0, "draw": r["md"] or 0, "away": r["ma"] or 0},
             "open": {"home": r["p_home_open"], "draw": r["p_draw_open"], "away": r["p_away_open"]},
             "close": {"home": r["p_home"], "draw": r["p_draw"], "away": r["p_away"]},
             "winner": {"H": "home", "D": "draw", "A": "away"}[r["result"]]} for r in rows]


def _rate(hits: int, n: int) -> float | None:
    return round(hits / n, 4) if n else None


def summarise(rows: list[dict]) -> dict:
    """The line-movement record for a list of graded games (see module docstring)."""
    groups = {"toward": [0, 0, 0.0], "flat": [0, 0, 0.0], "against": [0, 0, 0.0]}   # n, hits, sum of closing p
    fav = {"open": [0, 0], "close": [0, 0], "model": [0, 0]}
    follow = [0, 0, 0.0]
    moves, dropped = [], 0
    for r in rows:
        sides = list(r["close"])
        if any(r["open"][s] is None or r["close"][s] is None for s in sides):
            continue
        mv = {s: r["close"][s] - r["open"][s] for s in sides}
        if max(abs(v) for v in mv.values()) > MAX_MOVE:
            dropped += 1
            continue
        pick = max(r["model"], key=r["model"].get)
        g = "toward" if mv[pick] >= FLAT else "against" if mv[pick] <= -FLAT else "flat"
        groups[g][0] += 1
        groups[g][1] += pick == r["winner"]
        groups[g][2] += r["close"][pick]
        moves.append(mv[pick])
        for k, src in (("open", r["open"]), ("close", r["close"]), ("model", r["model"])):
            fav[k][0] += 1
            fav[k][1] += max(src, key=src.get) == r["winner"]
        mover = max(mv, key=mv.get)
        if mv[mover] >= BIG:
            follow[0] += 1
            follow[1] += mover == r["winner"]
            follow[2] += r["close"][mover]
    n = sum(v[0] for v in groups.values())
    moved = groups["toward"][0] + groups["against"][0]
    return {
        "n": n, "dropped_in_play": dropped,
        "moved_our_way": _rate(groups["toward"][0], moved), "moved_n": moved,
        "avg_move_pts": round(100 * sum(moves) / len(moves), 2) if moves else None,
        "groups": {k: {"n": v[0], "hit_rate": _rate(v[1], v[0]), "expected": round(v[2] / v[0], 4) if v[0] else None}
                   for k, v in groups.items()},
        "favorites": {k: {"n": v[0], "accuracy": _rate(v[1], v[0])} for k, v in fav.items()},
        "follow_big_moves": {"n": follow[0], "hit_rate": _rate(follow[1], follow[0]),
                             "expected": round(follow[2] / follow[0], 4) if follow[0] else None, "threshold_pts": round(BIG * 100)},
    }


def _market_rows(conn: Connection, sport: str, competition: str | None) -> list[dict]:
    """Every final game with open + close prices, our picks or not (for the market-only history)."""
    if sport == "nba":
        rows = q(conn, """SELECT g.season, o.p_home_open po, o.p_home pc, g.home_score > g.away_score hw FROM games g JOIN nba_odds o USING (game_id)
                          WHERE g.status = 'final' AND o.p_home_open IS NOT NULL AND o.p_home IS NOT NULL AND g.home_score <> g.away_score""")
        return [{"season": r["season"], "open": {"home": r["po"], "away": 1 - r["po"]}, "close": {"home": r["pc"], "away": 1 - r["pc"]},
                 "winner": "home" if r["hw"] else "away"} for r in rows]
    if sport == "nfl":
        out = []
        for r in q(conn, """SELECT g.season, x.home_ml_open, x.away_ml_open, x.home_ml, x.away_ml, g.home_score > g.away_score hw
                            FROM nfl_games g JOIN nfl_market_prices x ON x.game_id = g.game_id AND x.source = 'espn'
                            WHERE g.status = 'final' AND g.home_score <> g.away_score"""):
            po, pc = _no_vig(r["home_ml_open"], r["away_ml_open"]), _no_vig(r["home_ml"], r["away_ml"])
            if po is not None and pc is not None:
                out.append({"season": r["season"], "open": {"home": po, "away": 1 - po}, "close": {"home": pc, "away": 1 - pc},
                            "winner": "home" if r["hw"] else "away"})
        return out
    rows = q(conn, """SELECT g.season, g.result, f.p_home_open, f.p_draw_open, f.p_away_open, f.p_home, f.p_draw, f.p_away
                      FROM soccer_games g JOIN soccer_fdcuk_matches f USING (game_id)
                      WHERE g.competition = :c AND g.status = 'final' AND g.result IS NOT NULL
                        AND f.p_home_open IS NOT NULL AND f.p_home IS NOT NULL""", c=competition or "")
    return [{"season": r["season"], "open": {"home": r["p_home_open"], "draw": r["p_draw_open"], "away": r["p_away_open"]},
             "close": {"home": r["p_home"], "draw": r["p_draw"], "away": r["p_away"]},
             "winner": {"H": "home", "D": "draw", "A": "away"}[r["result"]]} for r in rows]


def summarise_market(rows: list[dict]) -> dict:
    """Market-only: opening vs closing favourite, and whether big late moves beat their closing price."""
    fav = {"open": [0, 0], "close": [0, 0]}
    follow = [0, 0, 0.0]
    dropped = 0
    for r in rows:
        sides = list(r["close"])
        if any(r["open"][s] is None or r["close"][s] is None for s in sides):
            continue
        mv = {s: r["close"][s] - r["open"][s] for s in sides}
        if max(abs(v) for v in mv.values()) > MAX_MOVE:
            dropped += 1
            continue
        for k in fav:
            fav[k][0] += 1
            fav[k][1] += max(r[k], key=r[k].get) == r["winner"]
        mover = max(mv, key=mv.get)
        if mv[mover] >= BIG:
            follow[0] += 1
            follow[1] += mover == r["winner"]
            follow[2] += r["close"][mover]
    return {"n": fav["open"][0], "dropped_in_play": dropped,
            "favorites": {k: {"n": v[0], "accuracy": _rate(v[1], v[0])} for k, v in fav.items()},
            "follow_big_moves": {"n": follow[0], "hit_rate": _rate(follow[1], follow[0]),
                                 "expected": round(follow[2] / follow[0], 4) if follow[0] else None, "threshold_pts": round(BIG * 100)},
            "seasons": sorted({r["season"] for r in rows})}


def line_movement(conn: Connection, sport: str, kind: str, competition: str | None = None) -> dict:
    rows = (_rows_nba(conn, kind) if sport == "nba" else _rows_nfl(conn, kind) if sport == "nfl"
            else _rows_soccer(conn, kind, competition or ""))
    seasons = sorted({r["season"] for r in rows})
    return {"sport": sport, "kind": kind, "competition": competition, "seasons": seasons, **summarise(rows),
            "market_history": summarise_market(_market_rows(conn, sport, competition)),
            "source": {"nba": "ESPN sportsbook lines (open + close)", "nfl": "ESPN sportsbook lines (open + close, 2023+)",
                       "soccer": "Pinnacle open + close (football-data.co.uk)"}[sport]}

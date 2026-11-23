"""Value finder and closing-line value (CLV) for NBA winners and player props.

* **Value**: our probability vs the best price on offer. Expected value per 1 unit staked is
  ``EV = p * decimal_odds - 1``; a bet is flagged when EV clears a threshold. Stake guidance is
  quarter-Kelly: ``0.25 * (p*d - 1) / (d - 1)`` of bankroll.
* **CLV**: did the market move toward our side between when we flagged the bet and tip-off?
  Measured as the change in the no-vig probability of our side at the same book (open/flag -> close),
  or, for props whose line moved, whether the line moved our way. Beating the close consistently is
  the standard evidence of a real edge; profit over a few hundred bets is mostly noise.

Backtests are honest about timing: winners are re-forecast from the *opening* line only (no
Polymarket, no closing information) and priced at opening odds; props use the point-in-time backtest
forecasts (which never see betting markets) priced at opening odds.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import numpy as np
import polars as pl
from sqlalchemy import Engine, text

from polypicker.domain.markets import ThresholdKind
from polypicker.ml.distributions import count_threshold

log = logging.getLogger(__name__)

PROP_STATS = ("points", "rebounds", "assists")
EV_BUCKETS = (0.0, 0.03, 0.05, 0.10)
KELLY_FRACTION = 0.25
MIN_PUBLISH_EV = 0.03          # live picks need at least +3% expected value
# Props: the stand-alone model is overconfident vs the market (2026-27 backtest: log loss 0.703 vs market
# 0.687). Blending 30% model / 70% market no-vig beats both (0.6865) and turned a -3.5% ROI into +3.6%
# on 1,268 bets at EV >= 2% (stable across both halves of the sample). Not yet statistically proven.
PROP_MODEL_WEIGHT = 0.3


def blend_prop(p_model_over: float, p_market_over: float | None, w: float = PROP_MODEL_WEIGHT) -> float:
    """Shrink the model's P(over | no push) toward the book's no-vig probability."""
    return p_model_over if p_market_over is None else w * p_model_over + (1 - w) * p_market_over
MAX_PUBLISH_EV = 0.40          # bigger "edges" are almost always stale/mismatched lines, not value
PREFERRED_BOOKS = ["DraftKings", "ESPN BET", "FanDuel", "BetMGM", "MGM", "Caesars Sportsbook (New Jersey)"]


# ---------------------------------------------------------------- price math

def american_to_decimal(ml) -> float | None:
    if ml is None:
        return None
    ml = float(ml)
    if ml == 0:
        return None
    return 1 + ml / 100 if ml > 0 else 1 + 100 / abs(ml)


def no_vig(d_a: float | None, d_b: float | None) -> float | None:
    """Probability of side A from a two-way price with the margin removed."""
    if not d_a or not d_b:
        return None
    a, b = 1 / d_a, 1 / d_b
    return a / (a + b)


def ev(p: float, dec: float, push: float = 0.0) -> float:
    """Expected profit per unit: win (dec-1) with p, stake back on push, lose 1 otherwise."""
    return p * (dec - 1) - (1 - p - push)


def kelly(p: float, dec: float, push: float = 0.0, fraction: float = KELLY_FRACTION) -> float:
    b = dec - 1
    if b <= 0:
        return 0.0
    q = 1 - p - push
    f = (b * p - q) / b
    return max(0.0, fraction * f)


def settle(side_wins: bool | None, dec: float) -> float:
    """Profit in units for a 1-unit bet (None = push)."""
    if side_wins is None:
        return 0.0
    return dec - 1 if side_wins else -1.0


def prop_probs(mean: float, dispersion: float, line: float) -> tuple[float, float, float]:
    """(P(over), P(under), P(push)) for an over/under line under the model's negative binomial."""
    tp = count_threshold(mean, dispersion, ThresholdKind.OVER, line)
    p_over, p_push = tp.p_hit, tp.p_push or 0.0
    return p_over, max(0.0, 1 - p_over - p_push), p_push


# ---------------------------------------------------------------- reports

def summarize(bets: pl.DataFrame) -> list[dict]:
    """bets: ev, profit, kelly, clv_prob (nullable), beat_close (nullable bool), result."""
    out = []
    for lo in EV_BUCKETS:
        b = bets.filter(pl.col("ev") >= lo)
        if b.is_empty():
            out.append({"min_ev": lo, "bets": 0})
            continue
        decided = b.filter(pl.col("result") != "push")
        stake_k = b["kelly"].sum()
        clv = b.filter(pl.col("clv_prob").is_not_null())["clv_prob"]
        beat = b.filter(pl.col("beat_close").is_not_null())["beat_close"]
        out.append({
            "min_ev": lo, "bets": b.height,
            "win_rate": round(float((decided["result"] == "win").mean()), 4) if decided.height else None,
            "roi_flat": round(float(b["profit"].sum() / b.height), 4),
            "units_flat": round(float(b["profit"].sum()), 2),
            "roi_kelly": round(float((b["profit"] * b["kelly"]).sum() / stake_k), 4) if stake_k > 0 else None,
            "avg_ev": round(float(b["ev"].mean()), 4),
            "avg_clv_prob": round(float(clv.mean()), 4) if clv.len() else None,
            "beat_close_rate": round(float(beat.cast(pl.Float64).mean()), 4) if beat.len() else None,
        })
    return out


# ---------------------------------------------------------------- backtests

def _books(engine: Engine) -> pl.DataFrame:
    from polypicker.ml.data import _read
    return _read(engine, """
        SELECT b.game_id, b.provider, b.home_ml_open, b.away_ml_open, b.home_ml, b.away_ml,
               b.spread_home_open, b.total_open, b.spread_home, b.total, b.is_closing
        FROM nba_odds_books b""", schema_overrides={"spread_home_open": pl.Float64, "total_open": pl.Float64,
                                                     "spread_home": pl.Float64, "total": pl.Float64})


def backtest_winners(engine: Engine, season: int) -> dict:
    """Re-forecast ``season`` from opening lines with a model trained only on earlier seasons, flag value
    at the best opening price across books, then settle and measure CLV at that book's closing price."""
    from polypicker.ml.game_model import MarketAwareGameModel
    from polypicker.ml.pipeline import _season_bounds, build_features
    fs = build_features(engine)
    vs, ve = _season_bounds(fs.games, season - 1)
    gm = MarketAwareGameModel.train(fs.game_design, vs - timedelta(days=1), vs, ve)
    books = _books(engine).filter(pl.col("is_closing"))
    rank = {n: k for k, n in enumerate(PREFERRED_BOOKS)}
    pref = (books.filter(pl.col("spread_home_open").is_not_null() & pl.col("total_open").is_not_null())
            .with_columns(pl.col("provider").replace_strict(rank, default=len(rank)).alias("_r"))
            .sort("_r").unique("game_id", keep="first")
            .select("game_id", (-pl.col("spread_home_open")).alias("open_margin_home"), "total_open"))
    rows = (fs.game_design.filter((pl.col("season") == season) & pl.col("y_points").is_not_null())
            .join(pref, on="game_id", how="inner")
            .with_columns(pl.when(pl.col("is_home")).then(pl.col("open_margin_home")).otherwise(-pl.col("open_margin_home"))
                          .alias("mkt_margin"), (pl.col("total_open") / 2).alias("mkt_total_half")))
    preds = {g["game_id"]: g["p_home_win"] for g in gm.predict_games(rows)}
    res = dict(fs.games.select("game_id", (pl.col("home_score") > pl.col("away_score")).alias("hw")).iter_rows())
    bets = []
    for gid, grp in books.filter(pl.col("game_id").is_in(list(preds))).group_by("game_id"):
        gid = gid[0]
        p_home = preds[gid]
        for side, p, col_o, col_c, oth_o, oth_c in (("home", p_home, "home_ml_open", "home_ml", "away_ml_open", "away_ml"),
                                                    ("away", 1 - p_home, "away_ml_open", "away_ml", "home_ml_open", "home_ml")):
            best = None
            for r in grp.iter_rows(named=True):
                d = american_to_decimal(r[col_o])
                if d and (best is None or d > best[0]):
                    best = (d, r)
            if best is None:
                continue
            d, r = best
            e = ev(p, d)
            if e < EV_BUCKETS[0]:
                continue
            p_open = no_vig(american_to_decimal(r[col_o]), american_to_decimal(r[oth_o]))
            p_close = no_vig(american_to_decimal(r[col_c]), american_to_decimal(r[oth_c]))
            clv = (p_close - p_open) if (p_open is not None and p_close is not None) else None
            won = res[gid] if side == "home" else not res[gid]
            bets.append({"game_id": gid, "side": side, "p": p, "dec": d, "book": r["provider"], "ev": e,
                         "kelly": kelly(p, d), "result": "win" if won else "loss", "profit": settle(won, d),
                         "clv_prob": clv, "beat_close": None if clv is None else clv > 0})
    df = pl.DataFrame(bets, infer_schema_length=None) if bets else pl.DataFrame(
        schema={"ev": pl.Float64, "profit": pl.Float64, "kelly": pl.Float64, "clv_prob": pl.Float64,
                "beat_close": pl.Boolean, "result": pl.String})
    return {"market": "moneyline", "season": season, "games": len(preds), "priced_at": "best opening price across books",
            "forecast": "model trained on earlier seasons, fed the opening line only", "buckets": summarize(df)}


def backtest_props(engine: Engine, season: int) -> dict:
    """Point-in-time backtest prop forecasts (never market-informed) vs the book's opening over/under."""
    from polypicker.ml.data import _read
    proj = _read(engine, """
        WITH latest AS (SELECT game_id, max(revision) r FROM predictions WHERE kind = 'backtest' GROUP BY 1)
        SELECT p.game_id, p.subject_id AS player_id, replace(p.market, 'player_', '') AS stat,
               (p.details->'distribution'->>'mean')::float AS mean,
               (p.details->'distribution'->>'dispersion')::float AS dispersion
        FROM predictions p JOIN latest l ON l.game_id = p.game_id AND l.r = p.revision JOIN games g ON g.game_id = p.game_id
        WHERE p.kind = 'backtest' AND p.market IN ('player_points', 'player_rebounds', 'player_assists')
          AND p.threshold_kind IS NULL AND p.projection IS NOT NULL AND g.season = :s""".replace(":s", str(int(season))))
    odds = _read(engine, "SELECT * FROM nba_prop_odds WHERE is_closing")
    box = _read(engine, """SELECT game_id, player_id, points, rebounds, assists FROM player_game_stats
                           WHERE participation = 'played'""")
    df = proj.join(odds, on=["game_id", "player_id", "stat"]).join(box, on=["game_id", "player_id"])
    bets = []
    for r in df.iter_rows(named=True):
        if not r["line_open"] or not r["over_open"] or not r["under_open"] or r["mean"] is None:
            continue
        po, pu, pp = prop_probs(r["mean"], r["dispersion"], r["line_open"])
        q = blend_prop(po / (po + pu), no_vig(r["over_open"], r["under_open"]))
        po, pu = q * (1 - pp), (1 - q) * (1 - pp)
        actual = r[r["stat"]]
        for side, p, d in (("over", po, r["over_open"]), ("under", pu, r["under_open"])):
            e = ev(p, d, pp)
            if e < EV_BUCKETS[0]:
                continue
            won = None if actual == r["line_open"] else (actual > r["line_open"]) == (side == "over")
            clv, beat = None, None
            if r["line"] is not None and r["over"] and r["under"]:
                if r["line"] == r["line_open"]:
                    p0 = no_vig(r["over_open"], r["under_open"])
                    p1 = no_vig(r["over"], r["under"])
                    if p0 is not None and p1 is not None:
                        clv = (p1 - p0) if side == "over" else (p0 - p1)
                        beat = clv > 0
                else:
                    beat = (r["line"] > r["line_open"]) == (side == "over")
            bets.append({"game_id": r["game_id"], "player_id": r["player_id"], "stat": r["stat"], "side": side,
                         "line": r["line_open"], "p": p, "dec": d, "ev": e, "kelly": kelly(p, d, pp),
                         "result": "push" if won is None else ("win" if won else "loss"), "profit": settle(won, d),
                         "clv_prob": clv, "beat_close": beat})
    b = pl.DataFrame(bets, infer_schema_length=None) if bets else pl.DataFrame(
        schema={"ev": pl.Float64, "profit": pl.Float64, "kelly": pl.Float64, "clv_prob": pl.Float64,
                "beat_close": pl.Boolean, "result": pl.String, "stat": pl.String})
    by_stat = {s: summarize(b.filter(pl.col("stat") == s)) for s in PROP_STATS} if b.height else {}
    return {"market": "props", "season": season, "props_priced": df.height, "priced_at": "book's opening over/under",
            "forecast": f"point-in-time backtest forecasts blended {int(PROP_MODEL_WEIGHT * 100)}% model / "
                        f"{100 - int(PROP_MODEL_WEIGHT * 100)}% opening no-vig price", "buckets": summarize(b), "by_stat": by_stat}


# ---------------------------------------------------------------- live value picks

def find_value(engine: Engine, now: datetime | None = None) -> dict:
    """Snapshot value bets for upcoming games from the latest live forecasts and current prices.
    The first time a bet qualifies its price is frozen (for CLV); later runs only refresh last_*."""
    now = now or datetime.now(timezone.utc)
    from polypicker.ml.data import _read
    upcoming = _read(engine, f"""
        SELECT g.game_id FROM games g
        WHERE g.status = 'scheduled' AND g.tipoff_utc > '{now.isoformat()}' AND g.tipoff_utc < '{(now + timedelta(days=2)).isoformat()}'""")
    ids = upcoming["game_id"].to_list() if upcoming.height else []
    if not ids:
        return {"games": 0, "picks": 0}
    idl = ",".join(str(int(i)) for i in ids)
    preds = _read(engine, f"""
        WITH latest AS (SELECT game_id, max(revision) r FROM predictions WHERE kind = 'live' AND game_id IN ({idl}) GROUP BY 1)
        SELECT p.game_id, p.market, p.side, p.subject_id, p.probability, p.revision,
               coalesce((p.details->>'uses_market')::boolean, false) AS uses_market,
               (p.details->'distribution'->>'mean')::float AS mean,
               (p.details->'distribution'->>'dispersion')::float AS dispersion
        FROM predictions p JOIN latest l ON l.game_id = p.game_id AND l.r = p.revision
        WHERE p.kind = 'live' AND ((p.market = 'moneyline')
              OR (p.market IN ('player_points','player_rebounds','player_assists') AND p.threshold_kind IS NULL
                  AND p.projection IS NOT NULL))""")
    books = _read(engine, f"SELECT * FROM nba_odds_books WHERE game_id IN ({idl}) AND NOT is_closing")
    pm = _read(engine, f"SELECT game_id, p_home_live FROM polymarket_nba WHERE game_id IN ({idl}) AND NOT closed")
    props = _read(engine, f"SELECT * FROM nba_prop_odds WHERE game_id IN ({idl}) AND NOT is_closing")
    cands = []
    # winners: best current price across books + Polymarket (price p -> decimal 1/p, before trading fees)
    pmd = dict(pm.iter_rows()) if pm.height else {}
    # winners only where the forecast already used a betting line: the data-only fallback model disagrees
    # with the market far more often and has lost those disagreements (3 of 17 right this season).
    ml_rows = preds.filter(pl.col("market") == "moneyline")
    gids_with_market = set(ml_rows.filter(pl.col("side") == "home").filter(pl.col("uses_market"))["game_id"].to_list())
    for r in ml_rows.filter(pl.col("game_id").is_in(list(gids_with_market))).iter_rows(named=True):
        gid, side, p = r["game_id"], r["side"], r["probability"]
        offers = []
        for b in books.filter(pl.col("game_id") == gid).iter_rows(named=True) if books.height else []:
            dh, da = american_to_decimal(b["home_ml"]), american_to_decimal(b["away_ml"])
            d = dh if side == "home" else da
            if d:
                fair = no_vig(dh, da) if side == "home" else no_vig(da, dh)
                offers.append((d, b["provider"], fair))
        ph = pmd.get(gid)
        if ph:
            price = ph if side == "home" else 1 - ph
            if 0.01 < price < 0.99:
                offers.append((1 / price, "Polymarket", price))
        if not offers:
            continue
        d, book, fair = max(offers, key=lambda o: o[0])
        cands.append({"game_id": gid, "market": "moneyline", "player_id": None, "side": side, "line": None,
                      "our_p": p, "push_p": 0.0, "price_dec": d, "book": book, "fair_p": fair, "revision": r["revision"]})
    # props
    pr = preds.filter(pl.col("market") != "moneyline").with_columns(pl.col("market").str.replace("player_", "").alias("stat"))
    if props.height and pr.height:
        j = pr.join(props, left_on=["game_id", "subject_id", "stat"], right_on=["game_id", "player_id", "stat"])
        for r in j.iter_rows(named=True):
            if not r["line"] or not r["over"] or not r["under"] or r["mean"] is None:
                continue
            po, pu, pp = prop_probs(r["mean"], r["dispersion"], r["line"])
            fair_over = no_vig(r["over"], r["under"])
            q = blend_prop(po / (po + pu), fair_over)
            po, pu = q * (1 - pp), (1 - q) * (1 - pp)
            for side, p, d in (("over", po, r["over"]), ("under", pu, r["under"])):
                cands.append({"game_id": r["game_id"], "market": f"player_{r['stat']}", "player_id": r["subject_id"],
                              "side": side, "line": r["line"], "our_p": p, "push_p": pp, "price_dec": d,
                              "book": r["provider"], "fair_p": fair_over if side == "over" else 1 - fair_over,
                              "revision": r["revision"]})
    picks = []
    for c in cands:
        e = ev(c["our_p"], c["price_dec"], c["push_p"])
        if MIN_PUBLISH_EV <= e <= MAX_PUBLISH_EV:
            picks.append({**c, "ev": e, "kelly": kelly(c["our_p"], c["price_dec"], c["push_p"])})
    written = _store_picks(engine, picks, now)
    return {"games": len(ids), "candidates": len(cands), "picks": len(picks), **written}


def _store_picks(engine: Engine, picks: list[dict], now: datetime) -> dict:
    new = upd = 0
    with engine.begin() as conn:
        for p in picks:
            key = {"g": p["game_id"], "m": p["market"], "pl": p["player_id"] or 0, "s": p["side"]}
            row = conn.execute(text("""SELECT id FROM value_picks WHERE game_id = :g AND market = :m
                                       AND coalesce(player_id, 0) = :pl AND side = :s"""), key).first()
            vals = {"our_p": p["our_p"], "push_p": p["push_p"], "price": p["price_dec"], "book": p["book"], "fair": p["fair_p"],
                    "ev": p["ev"], "kelly": p["kelly"], "now": now, "line": p["line"], "rev": p["revision"]}
            if row:
                conn.execute(text("""UPDATE value_picks SET last_our_p = :our_p, last_price_dec = :price, last_book = :book,
                                     last_ev = :ev, last_seen_at = :now WHERE id = :id"""), {**vals, "id": row.id})
                upd += 1
            else:
                conn.execute(text("""INSERT INTO value_picks (game_id, market, player_id, side, line, our_p, push_p,
                                         price_dec, book, fair_p, ev, kelly, prediction_revision, flagged_at,
                                         last_our_p, last_price_dec, last_book, last_ev, last_seen_at)
                                     VALUES (:g, :m, nullif(:pl, 0), :s, :line, :our_p, :push_p, :price, :book, :fair, :ev,
                                             :kelly, :rev, :now, :our_p, :price, :book, :ev, :now)"""), {**key, **vals})
                new += 1
    return {"new_picks": new, "refreshed_picks": upd}


def grade_value(engine: Engine) -> dict:
    """Settle picks whose game is final. CLV = closing no-vig probability of our side (same book) minus the
    no-vig probability when we flagged it; for props whose line moved, CLV is whether it moved our way."""
    graded = 0
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT v.*, g.home_score, g.away_score FROM value_picks v JOIN games g USING (game_id)
            WHERE v.graded_at IS NULL AND g.status = 'final'""")).mappings().all()
        for v in rows:
            close_dec = close_line = close_fair = None
            beat = won = None
            if v["market"] == "moneyline":
                hw = v["home_score"] > v["away_score"]
                won = hw if v["side"] == "home" else not hw
                if v["book"] == "Polymarket":
                    ph = conn.execute(text("SELECT p_home_pregame FROM polymarket_nba WHERE game_id = :g"),
                                      {"g": v["game_id"]}).scalar()
                    if ph:
                        close_fair = ph if v["side"] == "home" else 1 - ph
                        close_dec = 1 / close_fair
                else:
                    b = conn.execute(text("SELECT home_ml, away_ml FROM nba_odds_books WHERE game_id = :g AND provider = :p"),
                                     {"g": v["game_id"], "p": v["book"]}).first()
                    if b:
                        dh, da = american_to_decimal(b.home_ml), american_to_decimal(b.away_ml)
                        close_dec = dh if v["side"] == "home" else da
                        close_fair = no_vig(dh, da) if v["side"] == "home" else no_vig(da, dh)
            else:
                stat = v["market"].replace("player_", "")
                actual = conn.execute(text(f"""SELECT {stat} FROM player_game_stats WHERE game_id = :g AND player_id = :p
                                               AND participation = 'played'"""), {"g": v["game_id"], "p": v["player_id"]}).scalar()
                if actual is None:  # player didn't play: books void the bet
                    conn.execute(text("UPDATE value_picks SET result = 'void', profit = 0, graded_at = now() WHERE id = :id"),
                                 {"id": v["id"]})
                    graded += 1
                    continue
                won = None if actual == v["line"] else (actual > v["line"]) == (v["side"] == "over")
                po = conn.execute(text("SELECT line, over, under FROM nba_prop_odds WHERE game_id = :g AND player_id = :p AND stat = :s"),
                                  {"g": v["game_id"], "p": v["player_id"], "s": stat}).first()
                if po and po.line is not None:
                    close_line = po.line
                    close_dec = po.over if v["side"] == "over" else po.under
                    if po.line == v["line"]:
                        f = no_vig(po.over, po.under)
                        close_fair = None if f is None else (f if v["side"] == "over" else 1 - f)
                    else:
                        beat = (po.line > v["line"]) == (v["side"] == "over")
            clv = (close_fair - v["fair_p"]) if (close_fair is not None and v["fair_p"] is not None) else None
            if clv is not None:
                beat = clv > 0
            result = "push" if won is None else ("win" if won else "loss")
            conn.execute(text("""UPDATE value_picks SET close_price_dec = :cd, close_line = :cl, clv_prob = :clv,
                                 beat_close = :beat, result = :res, profit = :pr, graded_at = now() WHERE id = :id"""),
                         {"cd": close_dec, "cl": close_line, "clv": clv, "beat": beat, "res": result,
                          "pr": settle(won, v["price_dec"]), "id": v["id"]})
            graded += 1
    return {"value_picks_graded": graded}

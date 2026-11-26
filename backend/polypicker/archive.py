"""Season archiving: keep the hosted database at about one season of player-prop forecasts.

Player props are ~95 % of the prediction rows. Once a season is over (an older season, every game final, every
prop graded), its player-prop rows are

1. exported, with their grades, to ``ARCHIVE_DIR/{sport}-{season}-player-predictions.parquet`` (nothing is lost);
2. summarised into ``archived_prediction_stats``: per (kind, market) the exact sums behind every accuracy number
   the API shows (graded-state counts, Brier / log-loss / hit sums, calibration buckets, MAE), computed with the
   same filters as the performance endpoints (latest revision per game and kind);
3. deleted (with their outcome rows), in the same transaction as step 2.

The performance endpoints merge the stored sums back in *before* rounding (``merge``), so all-time and per-season
accuracy is unchanged. Game-level forecasts (winner, spread, total, team points) are never archived: game pages,
line movement, value and winner accuracy keep working for every season. Archived games' pages no longer list
player props.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Connection, Engine, text

log = logging.getLogger(__name__)

# sport -> (predictions, outcomes, games)
SPORTS = {"nba": ("predictions", "prediction_outcomes", "games"),
          "nfl": ("nfl_predictions", "nfl_prediction_outcomes", "nfl_games")}

DDL = """
CREATE TABLE IF NOT EXISTS archived_prediction_stats (
    sport varchar(16) NOT NULL,
    kind varchar(16) NOT NULL,
    season integer NOT NULL,
    market varchar(32) NOT NULL,
    stats jsonb NOT NULL,
    rows_archived integer NOT NULL,
    archive_file text,
    archived_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sport, kind, season, market)
);
"""


def current_season(sport: str, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    if sport == "nba":   # 2026-27 season = 2026; a new season starts in October
        return now.year if now.month >= 10 else now.year - 1
    return now.year if now.month >= 8 else now.year - 1   # NFL: from August


def _latest_base(pred: str, out: str, games: str) -> str:
    return f"""FROM {pred} p
        JOIN (SELECT game_id, kind, max(revision) r FROM {pred} GROUP BY 1, 2) lr
          ON lr.game_id = p.game_id AND lr.kind = p.kind AND lr.r = p.revision
        JOIN {games} g ON g.game_id = p.game_id JOIN {out} o ON o.prediction_id = p.id
        WHERE g.season = :s AND p.market LIKE 'player\\_%'"""


def season_stats(conn: Connection, sport: str, season: int) -> dict[tuple[str, str], dict]:
    """{(kind, market): stats} with the raw sums behind each performance-endpoint metric."""
    pred, out, games = SPORTS[sport]
    base = _latest_base(pred, out, games)
    prob_filter = "AND o.graded_state = 'final' AND p.probability IS NOT NULL AND o.outcome IN ('hit','miss')"
    res: dict[tuple[str, str], dict] = {}

    def st(r) -> dict:
        return res.setdefault((r.kind, r.market), {"states": {}, "prob": None, "mae": None, "calib": {}})
    for r in conn.execute(text(f"SELECT p.kind, p.market, o.graded_state, count(*) n {base} GROUP BY 1, 2, 3"), {"s": season}):
        st(r)["states"][r.graded_state] = r.n
    for r in conn.execute(text(f"""SELECT p.kind, p.market, count(*) n, sum(o.brier) brier, sum(o.log_loss) ll,
            sum(CASE WHEN o.outcome = 'hit' THEN 1 ELSE 0 END) hits, sum(p.probability) p, count(o.brier) n_brier,
            count(o.log_loss) n_ll {base} {prob_filter} GROUP BY 1, 2"""), {"s": season}):
        st(r)["prob"] = {"n": r.n, "brier": r.brier, "ll": r.ll, "hits": r.hits, "p": r.p,
                         "n_brier": r.n_brier, "n_ll": r.n_ll}
    for r in conn.execute(text(f"""SELECT p.kind, p.market, count(*) n, sum(o.abs_error) s {base}
            AND o.graded_state = 'final' AND p.threshold_kind IS NULL AND o.abs_error IS NOT NULL GROUP BY 1, 2"""), {"s": season}):
        st(r)["mae"] = {"n": r.n, "sum": r.s}
    for r in conn.execute(text(f"""SELECT p.kind, p.market, width_bucket(p.probability, 0, 1, 10) b, count(*) n,
            sum(p.probability) p, sum(CASE WHEN o.outcome = 'hit' THEN 1 ELSE 0 END) hits {base} {prob_filter}
            GROUP BY 1, 2, 3"""), {"s": season}):
        st(r)["calib"][str(r.b)] = {"n": r.n, "p": r.p, "hits": r.hits}
    return res


def ready(conn: Connection, sport: str, season: int) -> tuple[bool, str]:
    pred, out, games = SPORTS[sport]
    if season >= current_season(sport):
        return False, "season not over (current or future season)"
    unfinished = conn.execute(text(f"SELECT count(*) FROM {games} WHERE season = :s AND status NOT IN ('final', 'canceled')"),
                              {"s": season}).scalar_one()
    if unfinished:
        return False, f"{unfinished} games not final"
    pending = conn.execute(text(f"""SELECT count(*) FROM {pred} p JOIN {games} g ON g.game_id = p.game_id
            LEFT JOIN {out} o ON o.prediction_id = p.id
            WHERE g.season = :s AND p.market LIKE 'player\\_%' AND (o.prediction_id IS NULL OR o.graded_state = 'pending')"""),
                           {"s": season}).scalar_one()
    if pending:
        return False, f"{pending} player-prop rows not graded yet"
    return True, "ok"


def _export(conn: Connection, sport: str, season: int, path: Path) -> int:
    import polars as pl
    pred, out, games = SPORTS[sport]
    rows = conn.execute(text(f"""SELECT p.*, o.graded_state, o.outcome, o.observed_value, o.brier, o.log_loss, o.abs_error,
                                        o.graded_at FROM {pred} p JOIN {games} g ON g.game_id = p.game_id
                                 LEFT JOIN {out} o ON o.prediction_id = p.id
                                 WHERE g.season = :s AND p.market LIKE 'player\\_%' ORDER BY p.id"""), {"s": season})
    cols = list(rows.keys())
    data = [dict(zip(cols, r)) for r in rows]
    for d in data:   # jsonb -> text keeps the parquet schema simple
        if d.get("details") is not None:
            import json
            d["details"] = json.dumps(d["details"])
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(data, infer_schema_length=None).write_parquet(path, compression="zstd")
    return len(data)


def archive_season(engine: Engine, sport: str, season: int, out_dir: Path, dry_run: bool = False) -> dict:
    with engine.connect() as conn:
        ok, why = ready(conn, sport, season)
        pred, out, games = SPORTS[sport]
        n = conn.execute(text(f"""SELECT count(*) FROM {pred} p JOIN {games} g ON g.game_id = p.game_id
                                  WHERE g.season = :s AND p.market LIKE 'player\\_%'"""), {"s": season}).scalar_one()
    if not n:
        return {"sport": sport, "season": season, "rows": 0, "archived": False, "reason": "nothing to archive"}
    if not ok or dry_run:
        return {"sport": sport, "season": season, "rows": n, "archived": False, "reason": why if not ok else "dry run"}
    path = out_dir / f"{sport}-{season}-player-predictions.parquet"
    with engine.connect() as conn:
        written = _export(conn, sport, season, path)
    if written != n:
        raise RuntimeError(f"export wrote {written} rows, expected {n}; nothing deleted")
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL polypicker.compacting = 'on'"))
        stats = season_stats(conn, sport, season)
        for (kind, market), s in stats.items():
            rows_archived = sum(s["states"].values())
            conn.execute(text("""INSERT INTO archived_prediction_stats (sport, kind, season, market, stats, rows_archived, archive_file)
                                 VALUES (:sp, :k, :s, :m, CAST(:st AS jsonb), :n, :f)
                                 ON CONFLICT (sport, kind, season, market) DO UPDATE SET stats = EXCLUDED.stats,
                                   rows_archived = EXCLUDED.rows_archived, archive_file = EXCLUDED.archive_file,
                                   archived_at = now()"""),
                         {"sp": sport, "k": kind, "s": season, "m": market, "st": _json(s), "n": rows_archived, "f": path.name})
        ids = f"""SELECT p.id FROM {pred} p JOIN {games} g ON g.game_id = p.game_id
                  WHERE g.season = :s AND p.market LIKE 'player\\_%'"""
        o = conn.execute(text(f"DELETE FROM {out} WHERE prediction_id IN ({ids})"), {"s": season}).rowcount
        d = conn.execute(text(f"DELETE FROM {pred} WHERE id IN ({ids})"), {"s": season}).rowcount
    log.info("archived %s %s: %d prediction rows -> %s", sport, season, d, path)
    return {"sport": sport, "season": season, "rows": d, "outcome_rows": o, "archived": True, "file": str(path)}


def archive_ready(engine: Engine, out_dir: Path, dry_run: bool = False) -> list[dict]:
    """Archive every finished season that still has player-prop rows (run by run-all; a no-op most days)."""
    res = []
    for sport, (pred, _, games) in SPORTS.items():
        with engine.connect() as conn:
            seasons = [s for s, in conn.execute(text(f"""SELECT DISTINCT g.season FROM {pred} p JOIN {games} g
                       ON g.game_id = p.game_id WHERE p.market LIKE 'player\\_%' AND g.season < :c ORDER BY 1"""),
                                                 {"c": current_season(sport)})]
        res += [archive_season(engine, sport, s, out_dir, dry_run) for s in seasons]
    return res


def _json(o) -> str:
    import json
    return json.dumps(o, default=float)


# ---------------------------------------------------------------- read side (performance endpoints)

def merge(conn: Connection, sport: str, kind: str | None, season: int | None, markets: list[str] | None,
          states: list[dict], prob: list[dict], mae: list[dict], calib: list[dict]) -> tuple[list, list, list, list]:
    """Fold archived seasons' sums into the raw (unrounded) performance query rows. Same row shapes in and out."""
    where, params = ["sport = :sp"], {"sp": sport}
    if kind:
        where.append("kind = :k"); params["k"] = kind
    if season:
        where.append("season = :s"); params["s"] = season
    if markets:
        where.append("market = ANY(:m)"); params["m"] = markets
    if conn.execute(text("SELECT to_regclass('archived_prediction_stats')")).scalar() is None:
        return states, prob, mae, calib   # `polypicker db init` not run since this feature shipped
    arch = conn.execute(text(f"SELECT market, stats FROM archived_prediction_stats WHERE {' AND '.join(where)}"),
                        params).all()
    if not arch:
        return states, prob, mae, calib

    f = lambda v: float(v) if v is not None else None   # noqa: E731  (numeric -> float; Decimal + float fails)
    st = {(r["market"], r["graded_state"]): r["n"] for r in states}
    pr = {r["market"]: {"n": r["n"], "brier": (f(r["brier"]) or 0) * r["n"], "ll": (f(r["log_loss"]) or 0) * r["n"],
                        "hits": f(r["hit_rate"]) * r["n"], "p": (f(r.get("mean_p")) or 0) * r["n"],
                        "n_brier": r["n"] if r["brier"] is not None else 0, "n_ll": r["n"] if r["log_loss"] is not None else 0}
          for r in prob}
    ma = {r["market"]: {"n": r["n"], "sum": f(r["mae"]) * r["n"]} for r in mae}
    ca = {(r["market"], r["bucket"]): {"n": r["n"], "p": f(r["mean_predicted"]) * r["n"], "hits": f(r["observed_rate"]) * r["n"]}
          for r in calib}
    for market, s in arch:
        for g, n in s["states"].items():
            st[(market, g)] = st.get((market, g), 0) + n
        if s.get("prob"):
            a = pr.setdefault(market, {"n": 0, "brier": 0.0, "ll": 0.0, "hits": 0.0, "p": 0.0, "n_brier": 0, "n_ll": 0})
            for k in a:
                a[k] += s["prob"].get(k) or 0
        if s.get("mae"):
            a = ma.setdefault(market, {"n": 0, "sum": 0.0})
            a["n"] += s["mae"]["n"]; a["sum"] += s["mae"]["sum"] or 0
        for b, c in (s.get("calib") or {}).items():
            a = ca.setdefault((market, int(b)), {"n": 0, "p": 0.0, "hits": 0.0})
            a["n"] += c["n"]; a["p"] += c["p"]; a["hits"] += c["hits"]

    states = [{"market": m_, "graded_state": g, "n": n} for (m_, g), n in sorted(st.items())]
    prob = [{"market": m_, "n": a["n"], "brier": a["brier"] / a["n_brier"] if a["n_brier"] else None,
             "log_loss": a["ll"] / a["n_ll"] if a["n_ll"] else None, "hit_rate": a["hits"] / a["n"],
             "mean_p": a["p"] / a["n"]} for m_, a in sorted(pr.items()) if a["n"]]
    mae = [{"market": m_, "n": a["n"], "mae": a["sum"] / a["n"]} for m_, a in sorted(ma.items()) if a["n"]]
    calib = [{"market": m_, "bucket": b, "n": a["n"], "mean_predicted": a["p"] / a["n"], "observed_rate": a["hits"] / a["n"]}
             for (m_, b), a in sorted(ca.items()) if a["n"]]
    return states, prob, mae, calib

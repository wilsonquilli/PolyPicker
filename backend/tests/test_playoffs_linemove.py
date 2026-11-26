"""Knockout / playoff awareness in the futures sims, the line-movement record and plain-language reasons."""

from datetime import datetime, timezone

import numpy as np

from polypicker import futures as F
from polypicker.api import linemove, reasons

T = datetime(2027, 3, 1, tzinfo=timezone.utc)


def _gr(teams):
    return F.GoalRatings(list(teams), base=0.2, hfa=0.25, att=np.zeros(len(teams)), dfn=np.zeros(len(teams)))


def _leg(h, a, hs=None, as_=None, status="final", hp=None, ap=None, stage="round-of-16"):
    return {"home_team_id": h, "away_team_id": a, "home_score": hs, "away_score": as_, "status": status, "kickoff_utc": T,
            "home_shootout": hp, "away_shootout": ap, "neutral": False, "stage": stage}


def test_tie_decided_on_aggregate_and_by_shootout():
    gr, rng, n = _gr([10, 20]), np.random.default_rng(1), 500
    shock = np.zeros((n, 2))
    won = {"stage": "round-of-16", "teams": (10, 20), "legs": [_leg(10, 20, 1, 2), _leg(20, 10, 0, 3)]}   # 10 wins 4-2
    assert (F._tie_outcome(rng, gr, shock, won, n) == 0).all()
    pens = {"stage": "round-of-16", "teams": (10, 20), "legs": [_leg(10, 20, 1, 1), _leg(20, 10, 2, 2, hp=5, ap=4)]}
    assert (F._tie_outcome(rng, gr, shock, pens, n) == 1).all()                       # 20 won the shootout
    half = {"stage": "round-of-16", "teams": (10, 20), "legs": [_leg(10, 20, 4, 0), _leg(20, 10, status="scheduled")]}
    p10 = (F._tie_outcome(rng, gr, shock, half, n) == 0).mean()
    assert 0.9 < p10 < 1.0                                                            # 4-0 up after leg one


def test_real_ties_take_their_bracket_slot():
    n = 4
    a, b, c, d = (np.full(n, i) for i in range(4))
    calls = []
    out = F._resolve_round([{"_idx": [2, 3], "teams": (2, 3)}], [(a, b), (c, d)],
                           lambda x, y: calls.append("sim") or x, lambda tie: np.full(n, 3))
    assert (out[0] == 0).all() and (out[1] == 3).all() and calls == ["sim"]           # real tie went to slot 2


def test_real_winner_overrides_only_that_pairing():
    w = np.array([0, 1, 2])
    a, b = np.array([0, 0, 2]), np.array([1, 1, 3])
    assert F._real_winner(w, a, b, [(1, 0, 1)]).tolist() == [1, 1, 2]


def test_as_of_view_hides_future_results_and_undrawn_rounds():
    lp = {**_leg(1, 2, 1, 0, stage="league-phase"), "kickoff_utc": datetime(2027, 1, 20, tzinfo=timezone.utc), "game_id": 1}
    po = {**_leg(3, 4, 2, 2, stage="knockout-round-playoffs"), "kickoff_utc": datetime(2027, 2, 10, tzinfo=timezone.utc), "game_id": 2}
    r16 = {**_leg(1, 3, 0, 1, stage="round-of-16"), "kickoff_utc": datetime(2027, 3, 3, tzinfo=timezone.utc), "game_id": 3}
    view = F._as_of_view([lp, po, r16], datetime(2027, 2, 5, tzinfo=timezone.utc))
    assert [g["game_id"] for g in view] == [1, 2] and view[1]["home_score"] is None and view[1]["status"] == "scheduled"


def _row(model_home, open_home, close_home, winner):
    return {"season": 2026, "model": {"home": model_home, "away": 1 - model_home}, "open": {"home": open_home, "away": 1 - open_home},
            "close": {"home": close_home, "away": 1 - close_home}, "winner": winner}


def test_line_movement_groups_and_in_play_filter():
    rows = [_row(0.6, 0.55, 0.62, "home"), _row(0.6, 0.62, 0.55, "away"), _row(0.6, 0.6, 0.6, "home"),
            _row(0.6, 0.5, 0.9, "home")]                                              # last: a 40-pt "move" = in-play price
    s = linemove.summarise(rows)
    assert s["n"] == 3 and s["dropped_in_play"] == 1
    assert s["groups"]["toward"]["n"] == 1 and s["groups"]["toward"]["hit_rate"] == 1.0
    assert s["groups"]["against"]["hit_rate"] == 0.0 and s["moved_our_way"] == 0.5
    assert s["follow_big_moves"]["n"] == 2 and s["follow_big_moves"]["hit_rate"] == 1.0


def test_reasons_backing_the_pick_come_first():
    out = [reasons._r("home_field", "home", "home"), reasons._r("form_cold", "away", "home", w=1, n=5),
           reasons._r("missing_players", "home", "away", names=["X"], n=1), reasons._r("snow", None, None)]
    got = [r["code"] for r in reasons._finish(out, "away")]
    assert got[0] == "missing_players" and got[1] == "snow" and len(got) <= reasons.MAX

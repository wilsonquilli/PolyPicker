"""Value-finder math: expected value, Kelly, settlement, no-vig, prop probabilities, summaries."""

import polars as pl

from polypicker.ml.value import american_to_decimal, ev, kelly, no_vig, prop_probs, settle, summarize


def test_ev_and_kelly():
    assert abs(ev(0.55, 2.0) - 0.10) < 1e-12           # 55% at even money = +10%
    assert abs(ev(0.5, 1.909)) < 0.05 and ev(0.5, 1.909) < 0  # coin flip at -110 loses
    assert abs(ev(0.5, 2.0, push=0.1) - (0.5 - 0.4)) < 1e-12  # push returns the stake
    assert kelly(0.45, 2.0) == 0.0                        # negative edge -> no bet
    assert abs(kelly(0.55, 2.0) - 0.25 * 0.10) < 1e-12   # quarter Kelly of a 10% edge at evens


def test_settle_and_prices():
    assert settle(True, 2.5) == 1.5 and settle(False, 2.5) == -1.0 and settle(None, 2.5) == 0.0
    assert american_to_decimal(-150) == 1 + 100 / 150 and american_to_decimal(120) == 2.2
    assert abs(no_vig(1.909, 1.909) - 0.5) < 1e-12 and no_vig(None, 2.0) is None


def test_prop_probs_sum_to_one_and_push_on_integer_lines():
    po, pu, pp = prop_probs(20.0, 15.0, 19.5)
    assert pp == 0.0 and abs(po + pu - 1) < 1e-9 and 0.4 < po < 0.65
    po, pu, pp = prop_probs(20.0, 15.0, 20.0)
    assert pp > 0 and abs(po + pu + pp - 1) < 1e-9


def test_summarize_buckets():
    bets = pl.DataFrame({"ev": [0.01, 0.04, 0.12], "profit": [1.0, -1.0, 1.5], "kelly": [0.01, 0.02, 0.05],
                         "clv_prob": [0.01, -0.02, None], "beat_close": [True, False, None],
                         "result": ["win", "loss", "win"]})
    s = {b["min_ev"]: b for b in summarize(bets)}
    assert s[0.0]["bets"] == 3 and s[0.0]["units_flat"] == 1.5 and s[0.03]["bets"] == 2 and s[0.10]["bets"] == 1
    assert s[0.0]["beat_close_rate"] == 0.5 and s[0.10]["avg_clv_prob"] is None

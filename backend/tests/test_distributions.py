import numpy as np
import pytest
from scipy import stats

from polypicker.domain.markets import ThresholdKind
from polypicker.ml.distributions import (count_threshold, dispersion_at, fit_nb_dispersion, normal_over,
                                         spread_cover, win_probability)


def test_at_least_k_equals_over_k_minus_half():
    a = count_threshold(22.0, 5.0, ThresholdKind.AT_LEAST, 20)
    b = count_threshold(22.0, 5.0, ThresholdKind.OVER, 19.5)
    assert a.p_hit == pytest.approx(b.p_hit)
    assert a.p_push == 0 and b.p_push == 0


def test_integer_over_line_splits_off_push():
    over20 = count_threshold(22.0, 5.0, ThresholdKind.OVER, 20)
    at21 = count_threshold(22.0, 5.0, ThresholdKind.AT_LEAST, 21)
    at20 = count_threshold(22.0, 5.0, ThresholdKind.AT_LEAST, 20)
    assert over20.p_hit == pytest.approx(at21.p_hit)
    assert over20.p_push == pytest.approx(at20.p_hit - at21.p_hit)
    assert over20.p_hit + over20.p_push + over20.p_miss == pytest.approx(1.0)


def test_thresholds_are_monotonic():
    ps = [count_threshold(18.0, 4.0, ThresholdKind.AT_LEAST, k).p_hit for k in range(0, 50, 5)]
    assert ps[0] == pytest.approx(1.0)
    assert all(x >= y for x, y in zip(ps, ps[1:]))


def test_win_probability_symmetry():
    assert win_probability(0.0, 13.0) == pytest.approx(0.5)
    assert win_probability(5.0, 13.0) == pytest.approx(1 - win_probability(-5.0, 13.0))


def test_spread_at_projection_is_coin_flip_and_sides_are_complementary():
    home = spread_cover(4.0, 14.0, -4.5)
    away = spread_cover(-4.0, 14.0, 4.5)
    assert home.p_hit + away.p_hit == pytest.approx(1.0)
    push = spread_cover(4.0, 14.0, -4.0)
    assert push.p_push > 0 and push.p_hit + push.p_push + push.p_miss == pytest.approx(1.0)


def test_total_over_half_point():
    assert normal_over(220.0, 18.0, 219.5).p_hit == pytest.approx(stats.norm.sf(219.5, 220.0, 18.0))


def test_dispersion_fit_recovers_constant_nb():
    rng = np.random.default_rng(0)
    mu = rng.uniform(3, 30, 20_000)
    r_true = 6.0
    y = rng.negative_binomial(r_true, r_true / (r_true + mu))
    r0, gamma = fit_nb_dispersion(y, mu)
    assert dispersion_at(15.0, r0, gamma) == pytest.approx(r_true, rel=0.2)

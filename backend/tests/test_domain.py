from datetime import datetime, timezone

import pytest

from polypicker.domain.games import (GameStatus, SeasonType, derive_status, et_naive_to_utc, season_from_game_id,
                                     season_label, season_type_from_game_id)
from polypicker.domain.markets import (Market, Outcome, ThresholdKind, can_push, label, round_probability_pair,
                                       settle_spread, settle_threshold)


@pytest.mark.parametrize("gid,season,stype", [
    (22300001, 2023, SeasonType.REGULAR), (12600012, 2026, SeasonType.PRESEASON),
    (42500404, 2025, SeasonType.PLAYOFFS), (52400003, 2024, SeasonType.PLAY_IN),
    (62400001, 2024, SeasonType.CUP_FINAL), (32500041, 2025, SeasonType.ALL_STAR),
    (29701095, 1997, SeasonType.REGULAR),
])
def test_game_id_parsing(gid, season, stype):
    assert season_from_game_id(gid) == season
    assert season_type_from_game_id(gid) is stype


def test_season_label_is_not_calendar_year():
    assert season_label(2023) == "2023-24"
    assert season_label(2099) == "2099-00"


def test_eastern_to_utc_handles_dst():
    assert et_naive_to_utc(datetime(2026, 1, 10, 19, 0)).hour == 0      # EST: UTC-5
    assert et_naive_to_utc(datetime(2026, 4, 10, 19, 0)).hour == 23     # EDT: UTC-4


def test_status_never_guesses_postponed():
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    past, future = datetime(2026, 11, 19, tzinfo=timezone.utc), datetime(2026, 11, 21, tzinfo=timezone.utc)
    assert derive_status(True, past, now) is GameStatus.FINAL
    assert derive_status(False, future, now) is GameStatus.SCHEDULED
    assert derive_status(False, past, now) is GameStatus.UNKNOWN
    assert derive_status(False, past, now, explicit=GameStatus.POSTPONED) is GameStatus.POSTPONED


class TestThresholdSemantics:
    """FR-03.7: '20+' vs 'over 20.5' vs 'over 20' (push)."""

    def test_at_least_includes_the_threshold(self):
        assert settle_threshold(20, ThresholdKind.AT_LEAST, 20) is Outcome.HIT
        assert settle_threshold(19, ThresholdKind.AT_LEAST, 20) is Outcome.MISS

    def test_half_point_over_never_pushes(self):
        assert settle_threshold(21, ThresholdKind.OVER, 20.5) is Outcome.HIT
        assert settle_threshold(20, ThresholdKind.OVER, 20.5) is Outcome.MISS
        assert not can_push(ThresholdKind.OVER, 20.5)

    def test_integer_over_pushes_on_exact(self):
        assert settle_threshold(20, ThresholdKind.OVER, 20) is Outcome.PUSH
        assert can_push(ThresholdKind.OVER, 20)
        assert not can_push(ThresholdKind.AT_LEAST, 20)

    def test_spread(self):
        assert settle_spread(6, -5.5) is Outcome.HIT     # favorite wins by 6, -5.5 covers
        assert settle_spread(5, -5.5) is Outcome.MISS
        assert settle_spread(5, -5) is Outcome.PUSH
        assert settle_spread(-3, 3.5) is Outcome.HIT     # underdog loses by 3, +3.5 covers

    def test_labels_are_explicit(self):
        assert label(Market.PLAYER_POINTS, ThresholdKind.AT_LEAST, 20) == "20+ points"
        assert "21+" in label(Market.PLAYER_POINTS, ThresholdKind.OVER, 20.5, "over")
        assert "push" in label(Market.PLAYER_POINTS, ThresholdKind.OVER, 20, "over")
        assert "overtime" in label(Market.MONEYLINE, ThresholdKind.AT_LEAST, None, "home")


@pytest.mark.parametrize("p", [0.0, 0.12345, 0.5, 0.66666, 0.99951, 1.0])
def test_win_percentages_sum_to_100(p):
    h, a = round_probability_pair(p)
    assert h + a == pytest.approx(100.0, abs=1e-9)

"""v3 game model routing: games with a full betting line use the market model, others the v2 model."""

import polars as pl

from polypicker.ml.game_model import MarketAwareGameModel
from polypicker.ml.pipeline import add_market_features


class _Stub:
    def __init__(self, tag):
        self.tag = tag

    def predict_games(self, rows):
        p = 0.6 if self.tag == "base" else 0.7
        return [{"game_id": g, "who": self.tag, "p_home_win": p, "mu_margin": 3.0, "mu_total": 220.0}
                for g in rows["game_id"].unique(maintain_order=True).to_list()]


def _rows():
    return add_market_features(pl.DataFrame({
        "game_id": [1, 1, 2, 2, 3, 3],
        "is_home": [True, False, True, False, True, False],
        "mkt_margin_home": [4.0, 4.0, None, None, 2.0, 2.0],
        "book_total": [220.0, 220.0, 230.0, 230.0, None, None],
    }))


def test_market_features_are_team_perspective():
    r = _rows()
    g1 = r.filter(pl.col("game_id") == 1)
    assert g1["mkt_margin"].to_list() == [4.0, -4.0] and g1["mkt_total_half"].to_list() == [110.0, 110.0]


def test_routing_by_market_availability():
    m = MarketAwareGameModel(base=_Stub("base"), market=_Stub("market"))
    out = {g["game_id"]: (g["who"], g["uses_market"]) for g in m.predict_games(_rows())}
    assert out == {1: ("market", True), 2: ("base", False), 3: ("base", False)}  # 3 lacks a total


def test_no_market_model_falls_back():
    m = MarketAwareGameModel(base=_Stub("base"), market=None)
    assert {g["who"] for g in m.predict_games(_rows())} == {"base"}

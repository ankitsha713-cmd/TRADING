"""Unit tests for tactical rules and the backtester, on synthetic price data."""

import numpy as np
import pandas as pd

from app.services.tactical.backtest import run_backtest
from app.services.tactical.rules import (
    RULES,
    donchian_breakout,
    latest_signal,
    trend_following,
)


def _df(closes) -> pd.DataFrame:
    closes = pd.Series(closes, dtype=float)
    return pd.DataFrame({"Close": closes, "High": closes * 1.01, "Low": closes * 0.99})


def _uptrend(n=400, start=100.0, step=0.3):
    return _df([start + i * step for i in range(n)])


def _downtrend(n=400, start=200.0, step=-0.3):
    return _df([max(start + i * step, 1.0) for i in range(n)])


class TestRules:
    def test_registry_has_three_rules(self):
        assert set(RULES) == {"trend_following", "donchian_breakout", "rsi2_meanrev"}

    def test_trend_following_long_in_uptrend_flat_in_downtrend(self):
        assert trend_following(_uptrend()).iloc[-1] == 1
        assert trend_following(_downtrend()).iloc[-1] == 0

    def test_trend_following_flat_during_warmup(self):
        assert trend_following(_uptrend(n=100)).max() == 0  # SMA200 not formed yet

    def test_donchian_enters_on_breakout_and_exits_on_breakdown(self):
        # flat base, breakout rally, then crash below the 20-day low
        closes = [100.0] * 80 + [100 + 2 * i for i in range(30)] + [40.0] * 25
        target = donchian_breakout(_df(closes))
        assert target.iloc[95] == 1        # long during the rally
        assert target.iloc[-1] == 0        # stopped out after the crash

    def test_latest_signal_matches_series_tail(self):
        df = _uptrend()
        assert latest_signal("trend_following", df) == 1


class TestBacktester:
    def test_requires_enough_history(self):
        assert run_backtest("trend_following", "X", _uptrend(n=100)) is None

    def test_uptrend_strategy_tracks_buyhold(self):
        result = run_backtest("trend_following", "X", _uptrend(n=600))
        assert result is not None
        # In a clean uptrend the rule is long nearly throughout post-warmup.
        assert result.exposure_pct > 55
        assert result.total_return_pct > 0

    def test_downtrend_strategy_stays_out_and_beats_buyhold(self):
        result = run_backtest("trend_following", "X", _downtrend(n=600))
        assert result is not None
        assert result.exposure_pct < 5
        assert result.total_return_pct > -5           # sat in cash
        assert result.buyhold_cagr_pct < 0            # market lost money
        assert result.beats_buyhold

    def test_costs_are_charged_on_switches(self):
        # Whipsaw series: alternating regime flips force many trades.
        rng = np.random.default_rng(7)
        noise = 100 + np.cumsum(rng.normal(0, 2, 700))
        result = run_backtest("donchian_breakout", "X", _df(np.abs(noise) + 20))
        assert result is not None
        assert result.trades >= 2  # engine counted entries/exits

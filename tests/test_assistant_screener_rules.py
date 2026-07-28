"""Unit tests for the anomaly screener's pure scoring rules."""

from app.services.screener_rules import (
    ATTENTION_HIGH,
    ATTENTION_LOW,
    MEGA_CAP_USD,
    SMALL_CAP_USD,
    CandidateMetrics,
    anomaly_score,
    describe,
)


def _candidate(**overrides) -> CandidateMetrics:
    base = {
        "symbol": "TEST", "market": "us",
        "revenue_growth": 0.50, "earnings_growth": 0.50, "profit_margins": 0.25,
        "return_3m": 0.25, "week52_change": 1.00,
        "watchers": None, "insider_net_shares": None,
    }
    base.update(overrides)
    return CandidateMetrics(**base)


class TestAnomalyScore:
    def test_perfect_fundamentals_and_momentum(self):
        # 50 fundamentals + 25 momentum, neutral attention, no insider data
        assert anomaly_score(_candidate()) == 75.0

    def test_under_followed_bonus(self):
        crowded = anomaly_score(_candidate(watchers=ATTENTION_HIGH + 1))
        hidden = anomaly_score(_candidate(watchers=ATTENTION_LOW - 1))
        assert hidden - crowded == 25.0  # +15 bonus vs -10 penalty

    def test_unknown_attention_is_neutral(self):
        assert anomaly_score(_candidate(watchers=None)) == 75.0

    def test_insider_buying_adds_ten(self):
        assert anomaly_score(_candidate(insider_net_shares=10_000)) == 85.0

    def test_insider_selling_adds_nothing(self):
        assert anomaly_score(_candidate(insider_net_shares=-5_000)) == 75.0

    def test_no_growth_data_is_unscoreable(self):
        m = _candidate(revenue_growth=None, earnings_growth=None)
        assert anomaly_score(m) is None

    def test_negative_growth_scores_zero_not_negative(self):
        m = _candidate(revenue_growth=-0.30, earnings_growth=None,
                       profit_margins=None, return_3m=None, week52_change=None)
        assert anomaly_score(m) == 0.0

    def test_growth_is_capped(self):
        # 500% growth scores the same as 50% — no runaway outliers
        assert anomaly_score(_candidate(revenue_growth=5.0)) == anomaly_score(_candidate())

    def test_size_tilt_prefers_small_caps(self):
        small = anomaly_score(_candidate(market_cap=SMALL_CAP_USD / 2))
        mega = anomaly_score(_candidate(market_cap=MEGA_CAP_USD * 10))
        mid = anomaly_score(_candidate(market_cap=50_000_000_000))
        assert small - mid == 10.0
        assert mid - mega == 10.0


class TestDescribe:
    def test_mentions_key_factors(self):
        text = describe(_candidate(insider_net_shares=1000, watchers=800))
        assert "revenue +50%" in text
        assert "insiders buying" in text
        assert "only 800 watchers" in text

    def test_handles_missing_everything(self):
        text = describe(CandidateMetrics(symbol="X", market="us"))
        assert text == "insufficient data"

"""Unit tests for review-date parsing, category sizing, and volatility scaling."""

import pytest

from app.services.broker_rules import (
    REVIEW_DEFAULT_DAYS,
    REVIEW_MAX_DAYS,
    REVIEW_MIN_DAYS,
    buy_quantity,
    parse_review_days,
)
from app.services.volatility import default_stop_pct, event_threshold_pct


class TestParseReviewDays:
    def test_parses_pm_output(self):
        text = "**Time Horizon**: 3-6 months\n\n**Next Review**: 10 days"
        assert parse_review_days(text) == 10

    def test_clamps_low_and_high(self):
        assert parse_review_days("**Next Review**: 1 day") == REVIEW_MIN_DAYS
        assert parse_review_days("**Next Review**: 45 days") == REVIEW_MAX_DAYS

    def test_default_when_absent(self):
        assert parse_review_days("**Rating**: Hold") == REVIEW_DEFAULT_DAYS
        assert parse_review_days(None) == REVIEW_DEFAULT_DAYS


class TestCategorySizing:
    def test_core_buy_is_double_satellite_buy(self):
        core = buy_quantity("Buy", 10_000, 10_000, 100.0, 1.0, category="core")
        satellite = buy_quantity("Buy", 10_000, 10_000, 100.0, 1.0, category="satellite")
        assert core == pytest.approx(10.0)      # 10% of equity
        assert satellite == pytest.approx(5.0)  # 5% of equity

    def test_unknown_category_sizes_like_satellite(self):
        unknown = buy_quantity("Buy", 10_000, 10_000, 100.0, 1.0, category="whatever")
        assert unknown == pytest.approx(5.0)


class TestReviewPriority:
    def test_actionability_order(self):
        from app.services.pipeline import review_priority

        order = ["Buy", "Overweight", "Hold", "Underweight", "Sell", None]
        priorities = [review_priority(r) for r in order]
        assert priorities == sorted(priorities), "priority must decrease down the rating scale"
        assert review_priority("Buy") < review_priority("Hold")
        assert review_priority("Hold") < review_priority("Underweight")
        assert review_priority("Underweight") < review_priority("Sell")
        assert review_priority(None) > review_priority("Sell")


class TestVolatilityScaling:
    def test_stable_megacap_gets_floor_stop(self):
        # MSFT-like: ~0.9% daily -> 2.25% raw -> floored at 5%
        assert default_stop_pct(0.9) == 5.0

    def test_volatile_name_gets_wider_stop(self):
        # NVDA-like: ~2.8% daily -> 7% stop
        assert default_stop_pct(2.8) == pytest.approx(7.0)

    def test_biotech_hits_ceiling(self):
        assert default_stop_pct(6.0) == 12.0

    def test_unknown_volatility_is_conservative(self):
        assert default_stop_pct(None) == 10.0

    def test_event_thresholds_scale_and_clamp(self):
        assert event_threshold_pct(0.5) == 3.0    # floor
        assert event_threshold_pct(2.0) == pytest.approx(6.0)
        assert event_threshold_pct(9.0) == 10.0   # ceiling
        assert event_threshold_pct(None) == 5.0

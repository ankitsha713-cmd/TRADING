"""Unit tests for the assistant's pure domain logic (no optional deps needed)."""

import pytest

from app.domain import Market, Tier, infer_asset_type, infer_market
from app.services.rotation import next_rotation_state


class TestInferMarket:
    @pytest.mark.parametrize(
        ("symbol", "expected"),
        [
            ("NVDA", Market.US),
            ("BRK.B", Market.US),
            ("RELIANCE.NS", Market.INDIA),
            ("reliance.ns", Market.INDIA),
            ("TATASTEEL.BO", Market.INDIA),
            ("BTC-USD", Market.CRYPTO),
            ("eth-usd", Market.CRYPTO),
        ],
    )
    def test_market_classification(self, symbol, expected):
        assert infer_market(symbol) is expected

    def test_asset_type_crypto_only_for_crypto(self):
        assert infer_asset_type("BTC-USD") == "crypto"
        assert infer_asset_type("NVDA") == "stock"
        assert infer_asset_type("RELIANCE.NS") == "stock"


class TestRotation:
    def test_hold_increments_counter_without_demoting_early(self):
        tier, holds = next_rotation_state(Tier.DAILY, 0, "Hold", demote_after=5)
        assert tier is Tier.DAILY
        assert holds == 1

    def test_demotes_to_weekly_at_threshold(self):
        tier, holds = next_rotation_state(Tier.DAILY, 4, "Hold", demote_after=5)
        assert tier is Tier.WEEKLY
        assert holds == 5

    @pytest.mark.parametrize("rating", ["Buy", "Overweight", "Underweight", "Sell"])
    def test_actionable_rating_resets_counter_but_keeps_tier(self, rating):
        # Scheduling is review-date driven now; the user's coverage choice
        # stays put — an actionable rating must not flip weekly to daily.
        tier, holds = next_rotation_state(Tier.WEEKLY, 7, rating, demote_after=5)
        assert tier is Tier.WEEKLY
        assert holds == 0

    def test_weekly_ticker_holding_stays_weekly(self):
        tier, holds = next_rotation_state(Tier.WEEKLY, 6, "Hold", demote_after=5)
        assert tier is Tier.WEEKLY
        assert holds == 7

    def test_paused_never_auto_rotates(self):
        tier, holds = next_rotation_state(Tier.PAUSED, 2, "Buy", demote_after=5)
        assert tier is Tier.PAUSED
        assert holds == 2

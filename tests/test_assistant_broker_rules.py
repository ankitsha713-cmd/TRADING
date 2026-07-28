"""Unit tests for the paper broker's pure trading rules."""

import pytest

from app.domain import Market
from app.services.broker_rules import (
    buy_quantity,
    currency_for_market,
    parse_level,
    sell_quantity,
)


class TestParseLevel:
    def test_parses_stop_loss_from_trader_markdown(self):
        text = "**Action**: Buy\n\n**Stop Loss**: 186.0\n\n**Position Sizing**: 5%"
        assert parse_level(text, "stop_loss") == 186.0

    def test_parses_dollar_and_commas(self):
        assert parse_level("**Stop Loss**: $1,234.56", "stop_loss") == 1234.56

    def test_parses_price_target(self):
        assert parse_level("**Price Target**: 240", "price_target") == 240.0
        assert parse_level("**Target**: 99.5", "price_target") == 99.5

    def test_missing_level_returns_none(self):
        assert parse_level("**Rating**: Hold, no levels given", "stop_loss") is None
        assert parse_level(None, "stop_loss") is None


class TestCurrency:
    def test_india_is_inr_everything_else_usd(self):
        assert currency_for_market(Market.INDIA) == "INR"
        assert currency_for_market(Market.US) == "USD"
        assert currency_for_market(Market.CRYPTO) == "USD"


class TestBuyQuantity:
    def test_core_buy_is_ten_percent_of_equity(self):
        # $10,000 equity, $10,000 cash, $100 stock -> $1,000 -> 10 shares
        assert buy_quantity("Buy", 10_000, 10_000, 100.0, 1.0, category="core") == pytest.approx(10.0)

    def test_core_overweight_is_half_position(self):
        assert buy_quantity("Overweight", 10_000, 10_000, 100.0, 1.0, category="core") == pytest.approx(5.0)

    def test_capped_by_available_cash(self):
        # Only $300 cash left: order shrinks to what cash covers
        assert buy_quantity("Buy", 10_000, 300, 100.0, 1.0, category="core") == pytest.approx(3.0)

    def test_dust_orders_are_skipped(self):
        assert buy_quantity("Buy", 10_000, 20, 100.0, 1.0, category="core") == 0.0

    def test_inr_conversion(self):
        # $1,000 core allocation at 83 INR/USD and a 2,490 INR stock
        quantity = buy_quantity("Buy", 10_000, 10_000, 2_490.0, 83.0, category="core")
        assert quantity == pytest.approx((1_000 * 83) / 2_490)

    def test_hold_and_unknown_ratings_buy_nothing(self):
        assert buy_quantity("Hold", 10_000, 10_000, 100.0, 1.0) == 0.0
        assert buy_quantity("Sell", 10_000, 10_000, 100.0, 1.0) == 0.0


class TestSellQuantity:
    def test_sell_exits_fully(self):
        assert sell_quantity("Sell", 8.0) == pytest.approx(8.0)

    def test_underweight_trims_half(self):
        assert sell_quantity("Underweight", 8.0) == pytest.approx(4.0)

    def test_other_ratings_sell_nothing(self):
        assert sell_quantity("Hold", 8.0) == 0.0
        assert sell_quantity("Buy", 8.0) == 0.0

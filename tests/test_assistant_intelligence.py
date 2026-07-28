"""Unit tests for the data-intelligence layer: earnings clamp, scoring
factors (analyst upside, dilution, insider cluster), Form 4 parsing, and
XBRL dilution extraction."""

from datetime import date, datetime

import pytest

from app.services.earnings import clamp_review_to_earnings
from app.services.edgar import dilution_metrics_from_facts, parse_form4_xml
from app.services.screener_rules import CandidateMetrics, anomaly_score, describe


class TestEarningsClamp:
    NOW = datetime(2026, 7, 7)

    def test_review_pulled_before_earnings(self):
        review = datetime(2026, 7, 21)  # model said 14 days
        clamped = clamp_review_to_earnings(review, self.NOW, date(2026, 7, 15))
        assert clamped == datetime(2026, 7, 14)  # day before the report

    def test_review_already_before_earnings_untouched(self):
        review = datetime(2026, 7, 10)
        assert clamp_review_to_earnings(review, self.NOW, date(2026, 7, 15)) == review

    def test_earnings_in_past_or_today_untouched(self):
        review = datetime(2026, 7, 21)
        assert clamp_review_to_earnings(review, self.NOW, date(2026, 7, 7)) == review

    def test_no_earnings_date_untouched(self):
        review = datetime(2026, 7, 21)
        assert clamp_review_to_earnings(review, self.NOW, None) == review


def _candidate(**overrides) -> CandidateMetrics:
    base = {
        "symbol": "TEST", "market": "us",
        "revenue_growth": 0.50, "earnings_growth": 0.50, "profit_margins": 0.25,
        "return_3m": 0.25, "week52_change": 1.00,
    }
    base.update(overrides)
    return CandidateMetrics(**base)


class TestNewScoringFactors:
    BASE = 75.0  # perfect fundamentals+momentum, neutral everything else

    def test_analyst_upside_caps_at_ten(self):
        assert anomaly_score(_candidate(analyst_upside_pct=30)) == self.BASE + 10
        assert anomaly_score(_candidate(analyst_upside_pct=90)) == self.BASE + 10
        assert anomaly_score(_candidate(analyst_upside_pct=15)) == self.BASE + 5

    def test_negative_analyst_target_penalized(self):
        assert anomaly_score(_candidate(analyst_upside_pct=-10)) == self.BASE - 5

    def test_dilution_penalties(self):
        assert anomaly_score(_candidate(dilution_yoy_pct=15)) == self.BASE - 10
        assert anomaly_score(_candidate(dilution_yoy_pct=30)) == self.BASE - 20
        assert anomaly_score(_candidate(dilution_yoy_pct=5)) == self.BASE

    def test_short_runway_penalty(self):
        assert anomaly_score(_candidate(cash_runway_quarters=2.5)) == self.BASE - 15
        assert anomaly_score(_candidate(cash_runway_quarters=8)) == self.BASE

    def test_insider_cluster_bonus(self):
        assert anomaly_score(_candidate(insider_cluster=True)) == self.BASE + 8

    def test_describe_mentions_new_factors(self):
        text = describe(_candidate(
            analyst_upside_pct=42, dilution_yoy_pct=28,
            cash_runway_quarters=2, insider_cluster=True,
        ))
        assert "analysts see +42%" in text
        assert "dilution +28%/yr" in text
        assert "2q cash left" in text
        assert "insider cluster buy" in text


FORM4_BUY = """<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1500</value></transactionShares>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>400</value></transactionShares>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>9999</value></transactionShares>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


class TestForm4Parsing:
    def test_net_counts_only_open_market_trades(self):
        owner, net = parse_form4_xml(FORM4_BUY)
        assert owner == "DOE JANE"
        # 1500 bought - 400 sold; the 9999-share option exercise (code M) ignored
        assert net == pytest.approx(1100)


def _fact_entries(pairs):
    return [{"end": end, "val": val} for end, val in pairs]


class TestDilutionExtraction:
    def test_share_growth_cash_and_runway(self):
        facts = {"facts": {
            "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": _fact_entries([
                ("2025-06-30", 100_000_000), ("2026-06-30", 130_000_000),
            ])}}},
            "us-gaap": {
                "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": _fact_entries([
                    ("2026-06-30", 60_000_000),
                ])}},
                "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
                    {"start": "2025-07-01", "end": "2026-06-30", "val": -80_000_000},
                ]}},
            },
        }}
        metrics = dilution_metrics_from_facts(facts)
        assert metrics.shares_yoy_pct == pytest.approx(30.0)
        assert metrics.cash_usd == 60_000_000
        assert metrics.quarterly_burn_usd == pytest.approx(20_000_000)
        assert metrics.runway_quarters == pytest.approx(3.0)

    def test_profitable_company_has_no_runway_number(self):
        facts = {"facts": {
            "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": _fact_entries([
                ("2025-06-30", 100), ("2026-06-30", 101),
            ])}}},
            "us-gaap": {
                "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": _fact_entries([
                    ("2026-06-30", 5_000_000),
                ])}},
                "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
                    {"start": "2025-07-01", "end": "2026-06-30", "val": 40_000_000},
                ]}},
            },
        }}
        metrics = dilution_metrics_from_facts(facts)
        assert metrics.shares_yoy_pct == pytest.approx(1.0)
        assert metrics.runway_quarters is None
        assert metrics.quarterly_burn_usd is None

    def test_empty_facts_yield_nones(self):
        metrics = dilution_metrics_from_facts({"facts": {}})
        assert metrics.shares_yoy_pct is None
        assert metrics.cash_usd is None

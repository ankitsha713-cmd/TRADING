"""Targeted tests for the autonomous execution layer."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("GA_DRY_RUN", "1")

from autonomous.guardrails import Guardrails, GuardrailLimits, Proposal
from autonomous.engine import AutonomousEngine, parse_confidence
from autonomous import journal
from autonomous import strategies as strat


# ---------- guardrails ----------

def limits(**kw) -> GuardrailLimits:
    base = dict(
        max_position_pct=0.20,
        max_order_usd=2500.0,
        max_daily_loss_pct=0.03,
        max_open_positions=8,
        allowlist=("AAPL", "NVDA"),
        require_llm_confidence=True,
        live_mode=False,
    )
    base.update(kw)
    return GuardrailLimits(**base)


def test_buy_within_limits_approved():
    v, notional = Guardrails(limits()).check(
        Proposal("AAPL", "BUY", 0.8), equity=10000, cash=5000, positions={}
    )
    assert v.approved
    assert notional == pytest.approx(2000.0)  # 20% of equity


def test_buy_capped_by_cash():
    v, notional = Guardrails(limits()).check(
        Proposal("AAPL", "BUY", 0.8), equity=100000, cash=1000, positions={}
    )
    assert v.approved
    assert notional == pytest.approx(1000.0)


def test_off_list_ticker_vetoed():
    v, _ = Guardrails(limits()).check(
        Proposal("DOGE", "BUY", 0.9), equity=10000, cash=5000, positions={}
    )
    assert not v.approved
    assert any("allowlist" in r for r in v.reasons)


def test_low_confidence_vetoed():
    v, _ = Guardrails(limits()).check(
        Proposal("AAPL", "BUY", 0.4), equity=10000, cash=5000, positions={}
    )
    assert not v.approved


def test_daily_loss_halt():
    v, _ = Guardrails(limits()).check(
        Proposal("AAPL", "BUY", 0.9), equity=10000, cash=5000, positions={},
        realized_pnl_today_pct=-0.05,
    )
    assert not v.approved
    assert any("daily loss" in r for r in v.reasons)


def test_sell_without_position_vetoed():
    v, _ = Guardrails(limits()).check(
        Proposal("AAPL", "SELL", 0.8), equity=10000, cash=5000, positions={}
    )
    assert not v.approved


def test_sell_with_position_approved_full_notional():
    v, notional = Guardrails(limits()).check(
        Proposal("AAPL", "SELL", 0.8), equity=10000, cash=5000,
        positions={"AAPL": {"market_value": "3300.0"}},
    )
    assert v.approved
    assert notional == pytest.approx(3300.0)


def test_max_open_positions_veto():
    v, _ = Guardrails(limits(max_open_positions=1)).check(
        Proposal("NVDA", "BUY", 0.9), equity=10000, cash=5000,
        positions={"AAPL": {"market_value": "1000"}},
    )
    assert not v.approved


def test_hold_short_circuits():
    v, notional = Guardrails(limits()).check(
        Proposal("AAPL", "HOLD", 0.1), equity=10000, cash=0, positions={}
    )
    assert v.approved and notional == 0.0


def test_live_mode_requires_confirmation():
    with pytest.raises(RuntimeError):
        limits(live_mode=True)


# ---------- strategies ----------

def test_to_action_buy_threshold():
    action, conf = strat.to_action(0.42)
    assert action == "BUY" and 0.6 <= conf <= 0.95


def test_to_action_hold_in_deadzone():
    action, _ = strat.to_action(0.1)
    assert action == "HOLD"


def test_to_action_sell_negative():
    action, conf = strat.to_action(-0.6)
    assert action == "SELL" and conf >= 0.6


def test_evaluate_returns_bounded_score():
    # uses live yfinance; tolerant if network unavailable
    score, signals = strat.evaluate("AAPL")
    assert -1.0 <= score <= 1.0
    assert len(signals) == len(strat.STRATEGIES)


def test_strategy_credits_cover_registry():
    names = {fn.__name__ for fn in strat.STRATEGIES}
    assert {"trend_following", "turtle_breakout", "minervini_vcp", "dual_momentum", "graham_value"} <= names

def test_parse_confidence_percent():
    assert parse_confidence("BUY with 75% confidence") == pytest.approx(0.75)


def test_parse_confidence_decimal():
    assert parse_confidence("confidence 0.62") == pytest.approx(0.62)


def test_parse_confidence_default():
    assert parse_confidence("no numbers here") == pytest.approx(0.5)


# ---------- engine dry-run pipeline ----------

def test_engine_dry_run_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("GA_DRY_RUN", "1")
    monkeypatch.setenv("GA_JOURNAL_DB", str(tmp_path / "j.db"))
    import importlib
    import autonomous.journal as j
    importlib.reload(j)
    from autonomous.engine import AutonomousEngine as AE
    import autonomous.engine as eng
    importlib.reload(eng)

    engine = eng.AutonomousEngine()
    result = engine.run_ticker("NVDA", "2026-09-23")
    assert result["action"] == "BUY"
    assert result["approved"] is True
    assert result["order_status"] == "filled"
    # simulated broker state updated
    assert "NVDA" in engine.broker.positions
    assert engine.broker.cash < 100000.0
    # journal recorded the run
    runs = j.recent_runs(5)
    assert runs and runs[0]["ticker"] == "NVDA" and runs[0]["approved"] == 1

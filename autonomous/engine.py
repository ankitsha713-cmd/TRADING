"""AutonomousEngine: agent decision → guardrail check → broker order → journal → report."""
from __future__ import annotations

import os
import re
from typing import Any

from autonomous.broker import OrderResult
from autonomous import strategies as strat

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

from autonomous.broker import BrokerClient
from autonomous.guardrails import Guardrails, GuardrailLimits, Proposal
from autonomous import journal
from autonomous.reporting import render_run_report, print_cli_report, push_discord


def parse_confidence(text: str) -> float:
    """Best-effort confidence extraction from the final trade decision text."""
    m = re.search(r"(\d{1,3})\s*%", text or "")
    if m:
        return min(float(m.group(1)) / 100.0, 1.0)
    m = re.search(r"0\.\d{1,2}", text or "")
    if m:
        return float(m.group(0))
    return 0.5


class _DryRunBroker:
    """Simulated broker for credential-free verification (GA_DRY_RUN=1)."""

    def __init__(self):
        self.equity = 100000.0
        self.cash = 100000.0
        self.positions: dict[str, dict] = {}
        self._n = 0

    def get_account(self):
        return {"equity": str(self.equity), "cash": str(self.cash)}

    def get_positions(self):
        return list(self.positions.values())

    def submit_market_order(self, symbol, qty, side):
        self._n += 1
        px = self._price(symbol)
        cost = px * qty
        self.cash -= cost
        self.positions[symbol] = {"symbol": symbol, "market_value": cost}
        return OrderResult(symbol, side, qty, f"dry-run-{self._n}", "filled")

    def close_position(self, symbol):
        self._n += 1
        pos = self.positions.pop(symbol, {"market_value": 0})
        self.cash += float(pos.get("market_value", 0))
        return OrderResult(symbol, "sell", 0, f"dry-run-{self._n}", "filled")

    @staticmethod
    def _price(symbol):
        try:
            import yfinance
            px = yfinance.Ticker(symbol).fast_info.get("last_price")
            if px and float(px) > 0:
                return float(px)
        except Exception:
            pass
        return 100.0


class _DryRunGraph:
    """Stub decision source so the pipeline can be verified without LLM keys."""

    def propagate(self, ticker, trade_date):
        state = {
            "final_trade_decision": (
                "All analyst reports are constructive; risk team sees no red flags. "
                "Final decision: BUY with 75% confidence, moderate position size."
            )
        }
        return state, "BUY"


class _StrategyGraph:
    """Decision source driven purely by the strategy board (no LLM, no graph).
    Used when GA_DECISION_SOURCE=strategies. Blends quant + top-trader signals
    with the agent decision when GA_DECISION_SOURCE=blend.
    """

    def propagate(self, ticker, trade_date):
        score, signals = strat.evaluate(ticker)
        action, conf = strat.to_action(score)
        rationale = strat.render_signals_md(ticker, signals, score, action)
        return {"final_trade_decision": rationale, "strategy_score": score}, action


def _decision_source() -> str:
    return os.getenv("GA_DECISION_SOURCE", "agents")  # agents | strategies | blend


class AutonomousEngine:
    def __init__(self, config_overrides: dict[str, Any] | None = None, paper: bool | None = None):
        self.dry_run = os.getenv("GA_DRY_RUN", "0") == "1"
        source = _decision_source()
        if self.dry_run:
            self.graph = _DryRunGraph()
            self.broker = _DryRunBroker()
            self.guardrails = Guardrails()
            return
        if source == "strategies":
            self.graph = _StrategyGraph()
        else:
            config = DEFAULT_CONFIG.copy()
            if config_overrides:
                config.update(config_overrides)
            self.graph = TradingAgentsGraph(debug=False, config=config)
        if paper is None:
            paper = not GuardrailLimits().live_mode
        self.broker = _DryRunBroker() if self.dry_run else BrokerClient(paper=paper)
        self.guardrails = Guardrails()

    def run_ticker(self, ticker: str, trade_date: str) -> dict:
        error = None
        order = None
        report_md = ""
        try:
            final_state, decision = self.graph.propagate(ticker, trade_date)
            # blend mode: combine agent decision with strategy board
            if _decision_source() == "blend" and not self.dry_run:
                s_score, _sig = strat.evaluate(ticker)
                s_action, s_conf = strat.to_action(s_score)
                decision = action if (action := str(decision).strip().upper()) == s_action else (
                    s_action if s_conf >= parse_confidence(final_state.get("final_trade_decision", "")) else action
                )
            action = str(decision).strip().upper()
            if action not in {"BUY", "SELL", "HOLD"}:
                action = "HOLD"
            conf = parse_confidence(final_state.get("final_trade_decision", ""))

            proposal = Proposal(symbol=ticker, action=action, confidence=conf)
            acct = self.broker.get_account()
            equity = float(acct.get("equity", 0) or 0)
            cash = float(acct.get("cash", 0) or 0)
            positions = {p["symbol"]: p for p in self.broker.get_positions()}

            verdict, notional = self.guardrails.check(proposal, equity, cash, positions)

            order_status = None
            if verdict.approved and action != "HOLD":
                if action == "BUY":
                    qty = round(notional / self._last_price(ticker), 4)
                    if qty > 0:
                        order = self.broker.submit_market_order(ticker, qty, "buy")
                        order_status = order.status
                elif action == "SELL":
                    order = self.broker.close_position(ticker)
                    order_status = order.status
            elif not verdict.approved:
                order_status = "rejected_by_guardrails"

            report_md = render_run_report(
                ticker, action, conf, verdict.approved, verdict.reasons, notional, order_status, error
            )

            journal.log_run(
                ticker=ticker,
                trade_date=trade_date,
                action=action,
                confidence=conf,
                approved=verdict.approved,
                verdict_reasons=verdict.reasons,
                notional=notional,
                order_id=order.order_id if order else None,
                order_status=order_status,
                error=error,
                report_md=report_md,
            )
            print_cli_report(report_md)
            push_discord(report_md)
            return {"action": action, "approved": verdict.approved, "order_status": order_status}

        except Exception as e:  # never let one ticker kill the loop
            error = f"{type(e).__name__}: {e}"
            report_md = render_run_report(ticker, "ERROR", 0.0, False, [], 0.0, None, error)
            journal.log_run(ticker=ticker, trade_date=trade_date, action="ERROR",
                            confidence=None, approved=False, verdict_reasons=[error],
                            notional=0.0, error=error, report_md=report_md)
            print_cli_report(report_md)
            push_discord(report_md)
            return {"action": "ERROR", "approved": False, "error": error}

    def _last_price(self, ticker: str) -> float:
        try:
            import yfinance
            px = yfinance.Ticker(ticker).fast_info.get("last_price") or 100.0
        except Exception:
            px = 100.0
        return float(px) if px and float(px) > 0 else 100.0

    @staticmethod
    def _uses_real_graph() -> bool:
        return os.getenv("GA_DRY_RUN", "0") != "1" and _decision_source() == "agents"

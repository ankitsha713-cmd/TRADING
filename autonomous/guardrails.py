"""Hard risk guardrails.

These are enforced in code — not by the LLM — so a hallucinated "go all in on
TSLA with 10x leverage" gets vetoed deterministically. The Portfolio Manager
agent's decision is the *proposal*; this module is the circuit breaker.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class GuardrailLimits:
    max_position_pct: float = float(os.getenv("GA_MAX_POSITION_PCT", "0.20"))   # per-symbol, of equity
    max_order_usd: float = float(os.getenv("GA_MAX_ORDER_USD", "2500"))          # single order cap
    max_daily_loss_pct: float = float(os.getenv("GA_MAX_DAILY_LOSS_PCT", "0.03"))  # halt below this
    max_open_positions: int = int(os.getenv("GA_MAX_OPEN_POSITIONS", "8"))
    allowlist: tuple[str, ...] = tuple(
        s.strip().upper()
        for s in os.getenv("GA_TICKER_ALLOWLIST", "AAPL,MSFT,NVDA,GOOGL,SPY").split(",")
        if s.strip()
    )
    require_llm_confidence: bool = os.getenv("GA_REQUIRE_CONFIDENCE", "1") == "1"
    live_mode: bool = os.getenv("GA_LIVE_MODE", "0") == "1"  # double opt-in for real money

    def __post_init__(self):
        if self.live_mode and os.getenv("GA_LIVE_MODE_CONFIRM") != "YES_I_UNDERSTAND_REAL_MONEY":
            raise RuntimeError(
                "GA_LIVE_MODE=1 requires GA_LIVE_MODE_CONFIRM=YES_I_UNDERSTAND_REAL_MONEY. "
                "Refusing to run live without explicit acknowledgment."
            )


@dataclass
class Proposal:
    """Normalized decision from the agent graph + SignalProcessor."""
    symbol: str
    action: str          # BUY | SELL | HOLD
    confidence: float    # 0..1, parsed or defaulted
    rationale: str = ""


@dataclass
class Verdict:
    approved: bool
    reasons: list[str]


class Guardrails:
    def __init__(self, limits: GuardrailLimits | None = None):
        self.limits = limits or GuardrailLimits()

    def check(
        self,
        proposal: Proposal,
        equity: float,
        cash: float,
        positions: dict[str, dict],
        orders_today_usd: float = 0.0,
        realized_pnl_today_pct: float = 0.0,
    ) -> tuple[Verdict, float]:
        """Returns (verdict, approved_notional). VETO is cheap and always safe."""
        L = self.limits
        reasons: list[str] = []
        if proposal.action == "HOLD":
            return Verdict(True, ["hold — nothing to do"]), 0.0

        sym = proposal.symbol.upper()
        if sym not in L.allowlist:
            reasons.append(f"veto: {sym} not in allowlist {list(L.allowlist)}")
        if L.require_llm_confidence and proposal.confidence < 0.6:
            reasons.append(f"veto: confidence {proposal.confidence:.2f} < 0.60")
        if realized_pnl_today_pct <= -L.max_daily_loss_pct:
            reasons.append(f"veto: daily loss {realized_pnl_today_pct:.2%} breached -{L.max_daily_loss_pct:.0%} — trading halted for today")
        if len(positions) >= L.max_open_positions and proposal.action == "BUY" and sym not in positions:
            reasons.append(f"veto: max open positions ({L.max_open_positions}) reached")
        if orders_today_usd >= L.max_order_usd * 10:
            reasons.append(f"veto: daily deployed capital cap hit (${orders_today_usd:.0f})")

        notional = 0.0
        if proposal.action == "BUY":
            notional = min(equity * L.max_position_pct, L.max_order_usd, max(cash, 0.0))
            if notional <= 0:
                reasons.append("veto: no available cash")
        elif proposal.action == "SELL":
            pos = positions.get(sym)
            if not pos:
                reasons.append(f"veto: SELL {sym} but no position held")
            else:
                notional = float(pos.get("market_value", 0.0))

        approved = len(reasons) == 0
        return Verdict(approved, reasons), (notional if approved else 0.0)

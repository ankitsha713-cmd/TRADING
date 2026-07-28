"""Vectorized daily backtester for the tactical rules.

Deliberately simple and auditable rather than feature-rich: positions are
evaluated on closes, executed at the SAME close (optimistic by half a day),
and charged a per-side cost. No leverage, long-only, whole-equity per ticker.
The comparison that matters is rule-vs-buy-and-hold on the same bars with
the same costs — a rule only earns live paper money by winning that fight.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS = 252
COST_PER_SIDE_BPS = 5  # commission+slippage proxy per entry/exit


@dataclass(frozen=True)
class BacktestResult:
    rule: str
    symbol: str
    years: float
    total_return_pct: float
    cagr_pct: float
    sharpe: float
    max_drawdown_pct: float
    trades: int
    exposure_pct: float          # fraction of days in the market
    buyhold_cagr_pct: float
    buyhold_sharpe: float

    @property
    def beats_buyhold(self) -> bool:
        return self.sharpe > self.buyhold_sharpe


def _metrics(daily_returns: pd.Series) -> tuple[float, float, float, float]:
    """(total_return_pct, cagr_pct, sharpe, max_dd_pct) from daily returns."""
    equity = (1 + daily_returns).cumprod()
    total = float(equity.iloc[-1]) - 1
    years = len(daily_returns) / TRADING_DAYS
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else 0.0
    vol = float(daily_returns.std())
    sharpe = float(daily_returns.mean() / vol * np.sqrt(TRADING_DAYS)) if vol > 0 else 0.0
    drawdown = equity / equity.cummax() - 1
    max_dd = float(drawdown.min())
    return total * 100, cagr * 100, sharpe, max_dd * 100


def run_backtest(rule_name: str, symbol: str, df: pd.DataFrame) -> BacktestResult | None:
    """Backtest one rule on one ticker's daily OHLC history."""
    from app.services.tactical.rules import RULES

    if len(df) < 260:  # need at least ~1y past the SMA200 warmup
        return None
    target = RULES[rule_name](df)
    returns = df["Close"].pct_change().fillna(0.0)

    held = target.shift(1).fillna(0)          # signal on close, earn from next bar
    switches = target.diff().abs().fillna(target.iloc[0])
    costs = switches * (COST_PER_SIDE_BPS / 10_000)
    strategy_returns = held * returns - costs

    total, cagr, sharpe, max_dd = _metrics(strategy_returns)
    bh_total, bh_cagr, bh_sharpe, _ = _metrics(returns)
    return BacktestResult(
        rule=rule_name,
        symbol=symbol,
        years=round(len(df) / TRADING_DAYS, 1),
        total_return_pct=round(total, 1),
        cagr_pct=round(cagr, 2),
        sharpe=round(sharpe, 2),
        max_drawdown_pct=round(max_dd, 1),
        trades=int((switches > 0).sum()),
        exposure_pct=round(float(held.mean()) * 100, 1),
        buyhold_cagr_pct=round(bh_cagr, 2),
        buyhold_sharpe=round(bh_sharpe, 2),
    )

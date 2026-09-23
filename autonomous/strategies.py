"""Strategy engine: native quant strategies + strategies inspired by top traders.

Each strategy returns a signal in [-1, 1] (bearish..bullish) plus a rationale.
The aggregator combines them into a Proposal for the guardrails.

Strategies are computed from free yfinance data — no paid vendor required.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None


@dataclass
class Signal:
    name: str
    score: float          # -1..1
    rationale: str


# ---------------------------------------------------------------- helpers

def _history(symbol: str, period: str = "1y") -> pd.DataFrame | None:
    if yf is None:
        return None
    try:
        df = yf.Ticker(symbol).history(period=period, interval="1d")
        return df if len(df) > 60 else None
    except Exception:
        return None


def _rsi(series: pd.Series, window: int = 14) -> float:
    delta = series.diff().dropna()
    gains = delta.clip(lower=0).rolling(window).mean()
    losses = (-delta.clip(upper=0)).rolling(window).mean()
    if losses.iloc[-1] == 0:
        return 50.0
    rs = gains.iloc[-1] / losses.iloc[-1]
    return 100 - 100 / (1 + rs)


def _sma(series: pd.Series, window: int) -> float:
    return float(series.rolling(window).mean().iloc[-1])


# ---------------------------------------------------------------- native strategies

def trend_following(symbol: str) -> Signal:
    """Classic momentum: price above rising 50/200-day SMAs = long."""
    df = _history(symbol)
    if df is None:
        return Signal("trend", 0.0, "no data")
    close = df["Close"]
    sma50, sma200 = _sma(close, 50), _sma(close, 200)
    above = close.iloc[-1] > sma50 and sma50 > sma200
    below = close.iloc[-1] < sma50 and sma50 < sma200
    score = 1.0 if above else (-1.0 if below else 0.0)
    return Signal("trend", score, f"px={'above' if above else 'below' if below else 'mid'} 50SMA({sma50:.0f})/200SMA({sma200:.0f})")


def mean_reversion(symbol: str) -> Signal:
    """RSI extremes: oversold buy, overbought sell."""
    df = _history(symbol)
    if df is None:
        return Signal("mean_rev", 0.0, "no data")
    rsi = _rsi(df["Close"])
    if rsi < 30:
        return Signal("mean_rev", 0.8, f"RSI {rsi:.0f} oversold")
    if rsi > 70:
        return Signal("mean_rev", -0.8, f"RSI {rsi:.0f} overbought")
    return Signal("mean_rev", 0.0, f"RSI {rsi:.0f} neutral")


def volatility_breakout(symbol: str) -> Signal:
    """Bollinger squeeze breakout: price outside 2σ band after low volatility."""
    df = _history(symbol)
    if df is None:
        return Signal("vol_breakout", 0.0, "no data")
    close = df["Close"]
    sma = close.rolling(20).mean()
    std = close.rolling(20).std()
    px, upper, lower = close.iloc[-1], sma.iloc[-1] + 2 * std.iloc[-1], sma.iloc[-1] - 2 * std.iloc[-1]
    squeeze = std.iloc[-1] < std.mean() * 0.8
    if px > upper:
        return Signal("vol_breakout", 0.9, f"broke upper band {upper:.0f}" + (" from squeeze" if squeeze else ""))
    if px < lower:
        return Signal("vol_breakout", -0.9, f"broke lower band {lower:.0f}" + (" from squeeze" if squeeze else ""))
    return Signal("vol_breakout", 0.0, f"inside bands [{lower:.0f}, {upper:.0f}]")


def quality_growth(symbol: str) -> Signal:
    """Fundamental screen: earnings growth + margins from yfinance info."""
    if yf is None:
        return Signal("quality", 0.0, "no data")
    try:
        info = yf.Ticker(symbol).info
        growth = info.get("earningsQuarterlyGrowth") or 0.0
        margin = info.get("profitMargins") or 0.0
        score = 0.0
        if growth > 0.20:
            score += 0.6
        elif growth > 0:
            score += 0.3
        elif growth < 0:
            score -= 0.4
        if margin > 0.20:
            score += 0.4
        elif margin < 0.05:
            score -= 0.3
        return Signal("quality", max(-1, min(1, score)), f"earnGrowth={growth:.0%} margin={margin:.0%}")
    except Exception:
        return Signal("quality", 0.0, "info unavailable")


# ---------------------------------------------------------------- top-trader inspired

def turtle_breakout(symbol: str) -> Signal:
    """Richard Dennis / Turtles: buy 55-day high breakout, sell 20-day low breakdown."""
    df = _history(symbol, period="2y")
    if df is None:
        return Signal("turtle", 0.0, "no data")
    close = df["Close"]
    hi55 = close.iloc[-56:-1].max()
    lo20 = close.iloc[-21:-1].min()
    px = close.iloc[-1]
    if px >= hi55:
        return Signal("turtle", 1.0, f"55-day high breakout ({px:.0f} >= {hi55:.0f})")
    if px <= lo20:
        return Signal("turtle", -1.0, f"20-day low breakdown ({px:.0f} <= {lo20:.0f})")
    return Signal("turtle", 0.0, "no breakout")


def minervini_vcp(symbol: str) -> Signal:
    """Mark Minervini: stage-2 uptrend — price > 150SMA > 200SMA, 50SMA rising."""
    df = _history(symbol)
    if df is None:
        return Signal("minervini", 0.0, "no data")
    close = df["Close"]
    px, s50, s150, s200 = close.iloc[-1], _sma(close, 50), _sma(close, 150), _sma(close, 200)
    rising50 = s50 > float(close.rolling(50).mean().iloc[-21])
    ok = px > s150 > s200 and rising50 and px > s50
    near_high = px >= 0.75 * close.iloc[-260:].max() if len(close) > 260 else False
    if ok and near_high:
        return Signal("minervini", 0.9, "stage-2 template met, near 52wk high")
    if ok:
        return Signal("minervini", 0.5, "stage-2 template met")
    return Signal("minervini", 0.0, "template not met")


def dual_momentum(symbol: str) -> Signal:
    """Gary Antonacci (used by many macro traders): absolute + relative momentum vs SPY."""
    df, spy = _history(symbol, period="2y"), _history("SPY", period="2y")
    if df is None or spy is None:
        return Signal("dual_momentum", 0.0, "no data")
    ret_asset = df["Close"].iloc[-1] / df["Close"].iloc[-126] - 1
    ret_spy = spy["Close"].iloc[-1] / spy["Close"].iloc[-126] - 1
    score = 0.0
    if ret_asset > 0:
        score += 0.5           # absolute momentum
    if ret_asset > ret_spy:
        score += 0.5           # relative momentum
    elif ret_asset < 0:
        score -= 0.5
    return Signal("dual_momentum", score, f"6m {ret_asset:+.0%} vs SPY {ret_spy:+.0%}")


def graham_value(symbol: str) -> Signal:
    """Ben Graham: margin of safety on earnings yield (1/PE) vs 'risk-free' ~4%."""
    if yf is None:
        return Signal("graham", 0.0, "no data")
    try:
        info = yf.Ticker(symbol).info
        pe = info.get("trailingPE")
        if not pe or pe <= 0:
            return Signal("graham", 0.0, "no PE")
        earn_yield = 1.0 / pe
        score = (earn_yield - 0.04) * 10
        return Signal("graham", max(-1, min(1, score)), f"PE {pe:.0f}, earnings yield {earn_yield:.1%}")
    except Exception:
        return Signal("graham", 0.0, "info unavailable")


# ---------------------------------------------------------------- registry + aggregator

STRATEGIES: list[Callable[[str], Signal]] = [
    trend_following,
    mean_reversion,
    volatility_breakout,
    quality_growth,
    turtle_breakout,
    minervini_vcp,
    dual_momentum,
    graham_value,
]

# trader provenance, for reports
STRATEGY_CREDITS = {
    "trend": "classic trend-following (Donchian/CTA style)",
    "mean_rev": "mean reversion (RSI extremes)",
    "vol_breakout": "volatility breakout (Bollinger squeeze)",
    "quality": "quality/growth screen (fundamental)",
    "turtle": "Turtle Traders — Richard Dennis 55d/20d breakout",
    "minervini": "Mark Minervini — SEPA stage-2 template",
    "dual_momentum": "Gary Antonacci — dual momentum",
    "graham": "Benjamin Graham — margin of safety",
}


def evaluate(symbol: str) -> tuple[float, list[Signal]]:
    """Run all strategies; return (blended score -1..1, individual signals)."""
    signals = [s for s in (fn(symbol) for fn in STRATEGIES) if s.score != 0.0 or s.rationale != "no data"]
    if not signals:
        return 0.0, signals
    # equal weight; require mild net conviction
    return float(np.mean([s.score for s in signals])), signals


def to_action(score: float, threshold: float = 0.25) -> tuple[str, float]:
    """Map blended score to BUY/SELL/HOLD + pseudo-confidence for the guardrails."""
    if score >= threshold:
        return "BUY", min(0.5 + score * 0.5, 0.95)
    if score <= -threshold:
        return "SELL", min(0.5 + abs(score) * 0.5, 0.95)
    return "HOLD", 0.5


def render_signals_md(symbol: str, signals: list[Signal], score: float, action: str) -> str:
    lines = [f"## {symbol} strategy board → **{action}** (blended score {score:+.2f})", ""]
    for s in signals:
        emoji = "🟢" if s.score > 0 else "🔴" if s.score < 0 else "⚪"
        credit = STRATEGY_CREDITS.get(s.name, "")
        lines.append(f"- {emoji} **{s.name}** ({credit}): {s.score:+.1f} — {s.rationale}")
    return "\n".join(lines)

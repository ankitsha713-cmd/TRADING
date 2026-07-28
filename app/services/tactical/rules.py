"""Tactical trading rules — pure functions from OHLC data to target positions.

Each rule maps a daily price DataFrame (columns: Close, High, Low) to a
series of target positions (1 = long, 0 = flat) evaluated on CLOSES. No
LLM, no I/O — this is the fast layer. Rules are classics with decades of
literature behind them, chosen for transparency over exotic edge:

- trend_following: long above a rising long-term trend (50/200 SMA regime)
- donchian_breakout: 55-day-high entry / 20-day-low exit (Turtle-style)
- rsi2_meanrev: buy panic dips inside an uptrend (Connors RSI-2)

Only rules that beat buy-and-hold risk-adjusted in OUR backtest get to trade
paper money; the backtest verdict lives in the repo, not in marketing copy.
"""

import numpy as np
import pandas as pd

RULES: dict[str, "callable"] = {}


def _register(fn):
    RULES[fn.__name__] = fn
    return fn


@_register
def trend_following(df: pd.DataFrame) -> pd.Series:
    """Long while price > SMA200 and SMA50 > SMA200; flat otherwise."""
    close = df["Close"]
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    target = ((close > sma200) & (sma50 > sma200)).astype(int)
    target[sma200.isna()] = 0
    return target


@_register
def donchian_breakout(df: pd.DataFrame, entry: int = 55, exit_: int = 20) -> pd.Series:
    """Enter on a close above the prior 55-day high; exit on a close below
    the prior 20-day low. Stateful by nature, so a small loop."""
    close = df["Close"].to_numpy()
    upper = df["Close"].rolling(entry).max().shift(1).to_numpy()
    lower = df["Close"].rolling(exit_).min().shift(1).to_numpy()
    target = np.zeros(len(close), dtype=int)
    holding = False
    for i in range(len(close)):
        if not holding and not np.isnan(upper[i]) and close[i] > upper[i]:
            holding = True
        elif holding and not np.isnan(lower[i]) and close[i] < lower[i]:
            holding = False
        target[i] = 1 if holding else 0
    return pd.Series(target, index=df.index)


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


@_register
def rsi2_meanrev(df: pd.DataFrame) -> pd.Series:
    """Connors RSI-2: buy RSI(2) < 10 while price > SMA200; exit RSI(2) > 70."""
    close = df["Close"]
    rsi2 = _rsi(close, 2).to_numpy()
    above_trend = (close > close.rolling(200).mean()).to_numpy()
    target = np.zeros(len(close), dtype=int)
    holding = False
    for i in range(len(close)):
        if not holding and above_trend[i] and rsi2[i] < 10:
            holding = True
        elif holding and (rsi2[i] > 70 or not above_trend[i]):
            holding = False
        target[i] = 1 if holding else 0
    return pd.Series(target, index=df.index)


def latest_signal(rule_name: str, df: pd.DataFrame) -> int:
    """Today's target position (0/1) for one rule on one ticker."""
    rule = RULES[rule_name]
    series = rule(df)
    return int(series.iloc[-1]) if len(series) else 0

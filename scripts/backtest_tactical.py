"""Backtest the tactical rule library on the assistant's core US universe.

Run from the repo root:  python scripts/backtest_tactical.py [--years 10]

This script is the honesty gate for the tactical layer: only rules that beat
buy-and-hold risk-adjusted (Sharpe) on a majority of the universe are worth
letting near the paper book. Results print per rule x ticker plus a verdict.
"""

import argparse
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

UNIVERSE = ["NVDA", "MSFT", "AMZN", "LLY", "JPM", "TSM", "SPY", "GLD", "SLV"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=10)
    args = parser.parse_args()

    import yfinance as yf

    from app.services.tactical.backtest import run_backtest
    from app.services.tactical.rules import RULES

    print(f"Universe: {', '.join(UNIVERSE)} | lookback: {args.years}y | cost: 5bps/side\n")
    frames = {}
    for symbol in UNIVERSE:
        df = yf.Ticker(symbol).history(period=f"{args.years}y")
        if not df.empty:
            frames[symbol] = df

    verdicts = {}
    for rule in RULES:
        results = []
        for symbol, df in frames.items():
            result = run_backtest(rule, symbol, df)
            if result:
                results.append(result)
        wins = sum(1 for r in results if r.beats_buyhold)
        avg_sharpe = sum(r.sharpe for r in results) / len(results)
        avg_bh = sum(r.buyhold_sharpe for r in results) / len(results)
        avg_dd = sum(r.max_drawdown_pct for r in results) / len(results)
        verdicts[rule] = (wins, len(results), avg_sharpe, avg_bh)

        print(f"=== {rule} ===")
        print(f"{'sym':<6} {'CAGR%':>7} {'Sharpe':>7} {'MaxDD%':>7} {'trades':>7} "
              f"{'exp%':>6} {'BH-CAGR%':>9} {'BH-Sharpe':>9} beats?")
        for r in sorted(results, key=lambda x: x.symbol):
            print(f"{r.symbol:<6} {r.cagr_pct:>7.1f} {r.sharpe:>7.2f} "
                  f"{r.max_drawdown_pct:>7.1f} {r.trades:>7} {r.exposure_pct:>6.1f} "
                  f"{r.buyhold_cagr_pct:>9.1f} {r.buyhold_sharpe:>9.2f} "
                  f"{'YES' if r.beats_buyhold else 'no'}")
        print(f"--> beats buy-and-hold on {wins}/{len(results)} tickers | "
              f"avg Sharpe {avg_sharpe:.2f} vs BH {avg_bh:.2f} | avg MaxDD {avg_dd:.1f}%\n")

    print("=== VERDICT ===")
    for rule, (wins, n, sharpe, bh) in sorted(
        verdicts.items(), key=lambda kv: -kv[1][2]
    ):
        survives = wins >= n / 2 and sharpe > bh
        print(f"{rule:<20} {'SURVIVES' if survives else 'fails'} "
              f"({wins}/{n} wins, Sharpe {sharpe:.2f} vs {bh:.2f})")


if __name__ == "__main__":
    main()

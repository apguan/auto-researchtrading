#!/usr/bin/env python3
"""Run a backtest of strategy.Strategy on recent N hours of HL data.

Used to answer: "is the live bot's loss from a real strategy issue or
from a bug in the bot?" If the strategy is profitable on the same bars
the bot saw, the loss is from a bot bug. If the strategy itself loses,
the strategy is the problem.

Usage:
    uv run python scripts/backtest_recent.py                      # 24h, 15m bars
    uv run python scripts/backtest_recent.py --hours 6 --interval 15m
    uv run python scripts/backtest_recent.py --hours 72 --interval 1h
    uv run python scripts/backtest_recent.py --symbols BTC,ETH,SOL

NOTE: the strategy needs ~LOOKBACK_BARS=500 of warmup history. We pull
that much regardless of `--hours`, then evaluate metrics over the most
recent N hours of bars only.
"""

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from hyperliquid.info import Info  # type: ignore

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import constants  # noqa: E402  — module reference so we can monkey-patch INITIAL_CAPITAL
from constants import HYPERLIQUID_API_URL  # noqa: E402
from live_trading_bot.exchange.hyperliquid import fetch_candles_paginated  # noqa: E402


# Symbols the live bot trades — keep in sync with the prod side_by_side service
# (BTC, ETH, HYPE, SOL, TAO, XRP, ZEC as of 2026-04-08). NEAR is NOT in the
# prod symbol set; passing it just adds noise to the comparison.
DEFAULT_SYMBOLS = ["BTC", "ETH", "HYPE", "SOL", "TAO", "XRP", "ZEC"]


async def _resolve_capital(explicit: Optional[float], wallet_arg: Optional[str]) -> float:
    """Return the base capital for the backtest.

    Priority: --capital flag > live wallet equity from Hyperliquid > raise.
    """
    if explicit is not None:
        return explicit

    wallet = wallet_arg or os.environ.get("HYPERLIQUID_MAIN_WALLET", "")
    if not wallet:
        raise SystemExit(
            "No --capital provided and HYPERLIQUID_MAIN_WALLET is not set. "
            "Pass --capital <usd> or --wallet <addr> explicitly."
        )

    info = Info(HYPERLIQUID_API_URL, skip_ws=True)
    user_state = info.user_state(wallet)
    margin_summary = user_state.get("marginSummary", {})
    equity = float(margin_summary.get("accountValue", 0))
    if equity <= 0:
        raise SystemExit(
            f"Could not read live equity for wallet {wallet}. "
            "Pass --capital explicitly."
        )
    return equity


async def fetch_symbol_bars(
    info: Info, symbol: str, interval: str, total_bars: int
) -> pd.DataFrame:
    """Fetch the most recent `total_bars` bars for a symbol."""
    candles = await fetch_candles_paginated(
        info=info,
        symbol=symbol,
        interval=interval,
        start_time=None,
        end_time=None,
        limit=total_bars,
    )
    if not candles:
        return pd.DataFrame()
    rows = []
    for c in candles:
        rows.append(
            {
                "timestamp": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "funding_rate": 0.0,
            }
        )
    df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    return df


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=24.0,
                        help="Hours of recent data to evaluate metrics over (after warmup)")
    parser.add_argument("--interval", type=str, default="15m",
                        choices=["1m", "5m", "15m", "1h"])
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--capital", type=float, default=None,
                        help="Backtest base capital in USD. If omitted, the script "
                             "fetches the live Hyperliquid wallet's current equity. "
                             "Pass an explicit value to bypass the API fetch.")
    parser.add_argument("--wallet", type=str, default=None,
                        help="Hyperliquid wallet address for capital auto-detect. "
                             "Falls back to HYPERLIQUID_MAIN_WALLET env var.")
    parser.add_argument("--warmup-bars", type=int, default=None,
                        help="Bars of warmup before the evaluation window. "
                             "Defaults to LOOKBACK_BARS for the interval.")
    args = parser.parse_args()

    # Imports that depend on constants. We resolve LOOKBACK_BARS here (not at
    # module load) so that if INITIAL_CAPITAL gets monkey-patched below, the
    # downstream backtest engine sees the patched value.
    from prepare import LOOKBACK_BARS, run_backtest  # noqa: E402
    from strategy import Strategy  # noqa: E402

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    interval_minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}[args.interval]
    eval_bars = int((args.hours * 60) / interval_minutes)
    warmup = args.warmup_bars if args.warmup_bars is not None else LOOKBACK_BARS
    total_bars = warmup + eval_bars

    # Resolve the backtest's base capital BEFORE importing/calling run_backtest.
    # Auto-detect from the live wallet unless --capital was passed explicitly.
    capital = await _resolve_capital(args.capital, args.wallet)
    constants.INITIAL_CAPITAL = capital
    # The prepare module already imported INITIAL_CAPITAL at module load time
    # via `from constants import ...`, so patch its module-level binding too.
    import prepare as _prepare_mod
    _prepare_mod.INITIAL_CAPITAL = capital

    print(f"Strategy backtest")
    print(f"  Symbols:        {symbols}")
    print(f"  Interval:       {args.interval}")
    print(f"  Warmup bars:    {warmup}")
    print(f"  Eval window:    last {args.hours}h ({eval_bars} bars)")
    print(f"  Total bars/sym: {total_bars}")
    print(f"  Base capital:   ${capital:,.2f}  ({'auto-detected' if args.capital is None else 'manual'})")
    print()

    info = Info(HYPERLIQUID_API_URL, skip_ws=True)
    print("Downloading bars from Hyperliquid...")
    t0 = time.time()
    data = {}
    for sym in symbols:
        df = await fetch_symbol_bars(info, sym, args.interval, total_bars)
        if len(df) > 0:
            data[sym] = df
            print(f"  {sym}: {len(df)} bars "
                  f"({datetime.fromtimestamp(df.iloc[0]['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} → "
                  f"{datetime.fromtimestamp(df.iloc[-1]['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')})")
    print(f"Download took {time.time() - t0:.1f}s")
    print()

    if not data:
        print("ERROR: no bars downloaded")
        sys.exit(1)

    # Run backtest on the FULL window (warmup + eval). The backtest engine
    # walks forward bar-by-bar — the strategy gets warmup history naturally
    # because history_buffers fills up before reaching the eval window.
    print("Running backtest...")
    strategy = Strategy()
    t0 = time.time()
    result = run_backtest(strategy, data)
    print(f"Backtest took {time.time() - t0:.1f}s")
    print()

    # Print full-window metrics
    print(f"=== Full window ({total_bars} bars/symbol) ===")
    print(f"  Starting capital: ${capital:,.2f}")
    print(f"  Sharpe:           {result.sharpe:.4f}")
    print(f"  Total return:     {result.total_return_pct:.4f}%")
    print(f"  Max drawdown:     {result.max_drawdown_pct:.4f}%")
    print(f"  Num trades:       {result.num_trades}")
    print(f"  Win rate:         {result.win_rate_pct:.2f}%")
    print(f"  Profit factor:    {result.profit_factor:.3f}")
    print(f"  Annual turnover:  {result.annual_turnover:.2f}")
    print()

    # Print eval-window metrics by slicing equity curve. The backtest is now
    # running natively at `capital` (not $100k + linear scaling), so the
    # dollar P&L is the answer the live wallet would see if it were following
    # the strategy perfectly with its current equity.
    if result.equity_curve and len(result.equity_curve) > eval_bars:
        eval_start_equity = result.equity_curve[-eval_bars - 1]
        eval_end_equity = result.equity_curve[-1]
        eval_return_pct = ((eval_end_equity - eval_start_equity) / eval_start_equity) * 100
        eval_pnl_dollars = eval_end_equity - eval_start_equity

        print(f"=== Last {args.hours}h only ({eval_bars} bars) ===")
        print(f"  Return:        {eval_return_pct:+.4f}%")
        print(f"  Equity change: ${eval_pnl_dollars:+.2f}  (on ${capital:,.2f})")
        print(f"  Total trades over full window: {result.num_trades}")
        # Note: trade_log entries don't carry per-trade timestamps in
        # prepare.run_backtest, so we can't slice trade count to the eval
        # window without modifying that engine. The dollar P&L above is the
        # answer that matters for "what would the strategy have made".

    print()
    print(f"Final equity: ${result.equity_curve[-1]:,.2f}" if result.equity_curve else "No equity curve")


if __name__ == "__main__":
    asyncio.run(main())

"""
Stress test: Run strategy under varying slippage/fee regimes to test execution robustness.
Also tests regime subsample performance.
"""
import time
import numpy as np
import pandas as pd
from prepare import load_data, run_backtest, compute_score, SLIPPAGE_BPS, TAKER_FEE, MAKER_FEE
import prepare

from strategy import Strategy

print("=" * 70)
print("STRESS TEST 1: Slippage Sensitivity")
print("=" * 70)

# Test with varying slippage levels
for slip_mult, label in [(1, "1x (baseline)"), (2, "2x"), (5, "5x"), (10, "10x")]:
    # Temporarily override slippage in prepare module
    orig_slip = prepare.SLIPPAGE_BPS
    orig_taker = prepare.TAKER_FEE
    prepare.SLIPPAGE_BPS = orig_slip * slip_mult
    prepare.TAKER_FEE = orig_taker * slip_mult

    for split in ["val", "test"]:
        s = Strategy()
        data = load_data(split)
        result = run_backtest(s, data)
        score = compute_score(result)
        print(f"  Slippage {label:15s} | {split:5s} | score={score:8.3f} sharpe={result.sharpe:8.3f} dd={result.max_drawdown_pct:.3f}% return={result.total_return_pct:7.1f}%")

    prepare.SLIPPAGE_BPS = orig_slip
    prepare.TAKER_FEE = orig_taker

print()
print("=" * 70)
print("STRESS TEST 2: Regime Subsample Analysis")
print("=" * 70)

# Load full val + test data and analyze by quarters
for split in ["val", "test"]:
    data = load_data(split)
    # Get timestamps
    btc_df = data["BTC"]
    ts_min = pd.Timestamp(btc_df["timestamp"].min(), unit="ms", tz="UTC")
    ts_max = pd.Timestamp(btc_df["timestamp"].max(), unit="ms", tz="UTC")

    # Split into quarters
    quarters = pd.date_range(ts_min, ts_max, freq="QE")
    quarter_starts = [ts_min] + list(quarters)
    quarter_ends = list(quarters) + [ts_max]

    for q_start, q_end in zip(quarter_starts, quarter_ends):
        q_start_ms = int(q_start.timestamp() * 1000)
        q_end_ms = int(q_end.timestamp() * 1000)

        q_data = {}
        for sym, df in data.items():
            mask = (df["timestamp"] >= q_start_ms) & (df["timestamp"] < q_end_ms)
            q_df = df[mask].reset_index(drop=True)
            if len(q_df) > 100:
                q_data[sym] = q_df

        if not q_data:
            continue

        s = Strategy()
        result = run_backtest(s, q_data)
        score = compute_score(result)
        bars = sum(len(df) for df in q_data.values())
        label = f"{q_start.strftime('%b%y')}-{q_end.strftime('%b%y')}"
        print(f"  {split:5s} | {label:15s} | {bars:5d} bars | score={score:8.3f} sharpe={result.sharpe:8.3f} dd={result.max_drawdown_pct:.3f}% return={result.total_return_pct:7.1f}%")

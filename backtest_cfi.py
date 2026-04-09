"""
Run backtest on CFI perp data. Usage: uv run backtest_cfi.py
Loads CFI-anchored perpetual prices instead of spot-referenced prices.
Uses the same strategy and evaluation harness.
"""

import os
import time
import signal as sig

import pandas as pd
from prepare import run_backtest, compute_score, TIME_BUDGET, SYMBOLS
from prepare import TRAIN_START, TRAIN_END, VAL_START, VAL_END, TEST_START, TEST_END

# Generate CFI data if not present
CFI_DATA_DIR = os.path.join(os.path.expanduser("~"), ".cache", "autotrader", "cfi_data")

if not os.path.exists(os.path.join(CFI_DATA_DIR, "BTC_1h_cfi.parquet")):
    print("Generating CFI perp data...")
    from prepare_cfi import generate_all_cfi_data
    generate_all_cfi_data()
    print()


def load_cfi_data(split: str = "val") -> dict:
    """Load CFI perp OHLCV data for the given split."""
    splits = {
        "train": (TRAIN_START, TRAIN_END),
        "val": (VAL_START, VAL_END),
        "test": (TEST_START, TEST_END),
    }
    assert split in splits, f"split must be one of {list(splits.keys())}"
    start_str, end_str = splits[split]
    start_ms = int(pd.Timestamp(start_str, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(end_str, tz="UTC").timestamp() * 1000)

    result = {}
    for symbol in SYMBOLS:
        filepath = os.path.join(CFI_DATA_DIR, f"{symbol}_1h_cfi.parquet")
        if not os.path.exists(filepath):
            continue
        df = pd.read_parquet(filepath)
        # Drop CFI metadata columns — backtest harness expects standard OHLCV
        df = df.drop(columns=["cfi_index", "k_fixed_hr"], errors="ignore")
        mask = (df["timestamp"] >= start_ms) & (df["timestamp"] < end_ms)
        split_df = df[mask].reset_index(drop=True)
        if len(split_df) > 0:
            result[symbol] = split_df
    return result


# Timeout guard
def timeout_handler(signum, frame):
    print("TIMEOUT: backtest exceeded time budget")
    exit(1)

sig.signal(sig.SIGALRM, timeout_handler)
sig.alarm(TIME_BUDGET + 30)

t_start = time.time()

from strategy import Strategy

print("=== CFI PERP BACKTESTING ===")
print()

for split_name in ["val", "test", "train"]:
    strategy = Strategy()
    data = load_cfi_data(split_name)

    print(f"--- {split_name.upper()} SET ---")
    print(f"Loaded {sum(len(df) for df in data.values())} bars across {len(data)} symbols")

    result = run_backtest(strategy, data)
    score = compute_score(result)

    print(f"score:              {score:.6f}")
    print(f"sharpe:             {result.sharpe:.6f}")
    print(f"total_return_pct:   {result.total_return_pct:.6f}")
    print(f"max_drawdown_pct:   {result.max_drawdown_pct:.6f}")
    print(f"num_trades:         {result.num_trades}")
    print(f"win_rate_pct:       {result.win_rate_pct:.6f}")
    print(f"profit_factor:      {result.profit_factor:.6f}")
    print()

t_end = time.time()
print(f"Total time: {t_end - t_start:.1f}s")

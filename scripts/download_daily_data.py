#!/usr/bin/env python3
"""Download the latest 6 months of 1h candles + funding from Hyperliquid.

Writes parquet files to ~/.cache/autotrader/data/{symbol}_1h.parquet
in the same format as prepare.py — compatible with load_data().

Usage:
    python scripts/download_daily_data.py
    python scripts/download_daily_data.py --months 3
"""

import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from constants import BENCHMARK_SYMBOLS, HL_INFO_URL

CACHE_DIR = Path.home() / ".cache" / "autotrader" / "data"

HL_CANDLE_CHUNK_MS = 30 * 24 * 3600 * 1000
HL_FUNDING_CHUNK_MS = 30 * 24 * 3600 * 1000


def _download_hl_candles(
    symbol: str, interval: str, start_ms: int, end_ms: int
) -> pd.DataFrame:
    all_rows = []
    current = start_ms
    while current < end_ms:
        body = {
            "type": "candleSnapshot",
            "req": {
                "coin": symbol,
                "interval": interval,
                "startTime": current,
                "endTime": min(current + HL_CANDLE_CHUNK_MS, end_ms),
            },
        }
        try:
            resp = requests.post(HL_INFO_URL, json=body, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                current += HL_CANDLE_CHUNK_MS
                continue
            for row in data:
                all_rows.append(
                    {
                        "timestamp": int(row["t"]),
                        "open": float(row["o"]),
                        "high": float(row["h"]),
                        "low": float(row["l"]),
                        "close": float(row["c"]),
                        "volume": float(row["v"]),
                    }
                )
            current = int(data[-1]["t"]) + 3600 * 1000
        except Exception as exc:
            print(f"  WARNING: candle fetch error for {symbol}: {exc}", file=sys.stderr)
            current += HL_CANDLE_CHUNK_MS
        time.sleep(0.2)

    if not all_rows:
        return pd.DataFrame()
    df = (
        pd.DataFrame(all_rows)
        .sort_values("timestamp")
        .drop_duplicates("timestamp")
        .reset_index(drop=True)
    )
    return df


def _download_hl_funding(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    all_rows = []
    current = start_ms
    while current < end_ms:
        body = {
            "type": "fundingHistory",
            "coin": symbol,
            "startTime": current,
            "endTime": min(current + HL_FUNDING_CHUNK_MS, end_ms),
        }
        try:
            resp = requests.post(HL_INFO_URL, json=body, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break
            for row in data:
                all_rows.append(
                    {
                        "timestamp": int(row["time"]),
                        "funding_rate": float(row["fundingRate"]),
                    }
                )
            current = int(data[-1]["time"]) + 1
        except Exception as exc:
            print(f"  WARNING: funding fetch error for {symbol}: {exc}", file=sys.stderr)
            break
        time.sleep(0.2)

    if not all_rows:
        return pd.DataFrame(all_rows, columns=pd.Index(["timestamp", "funding_rate"]))
    return pd.DataFrame(all_rows)


def main():
    months = 6
    if "--months" in sys.argv:
        idx = sys.argv.index("--months")
        months = int(sys.argv[idx + 1])

    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=months * 30)).timestamp() * 1000)

    start_str = now - timedelta(days=months * 30)
    print(f"Downloading {months} months of data: {start_str.strftime('%Y-%m-%d')} → {now.strftime('%Y-%m-%d')}")
    print(f"Symbols: {BENCHMARK_SYMBOLS}")
    print(f"Cache: {CACHE_DIR}")
    print()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    for symbol in BENCHMARK_SYMBOLS:
        filepath = CACHE_DIR / f"{symbol}_1h.parquet"
        print(f"  {symbol}: downloading candles...")
        candles = _download_hl_candles(symbol, "1h", start_ms, end_ms)
        print(f"  {symbol}: {len(candles)} candles")

        print(f"  {symbol}: downloading funding rates...")
        funding = _download_hl_funding(symbol, start_ms, end_ms)

        if candles.empty:
            print(f"  {symbol}: NO DATA, skipping")
            continue

        if not funding.empty:
            funding = (
                funding.drop_duplicates(subset=["timestamp"])
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
            candles = pd.merge_asof(candles, funding, on="timestamp", direction="backward")

        if "funding_rate" not in candles.columns:
            candles["funding_rate"] = 0.0
        candles["funding_rate"] = candles["funding_rate"].fillna(0.0)

        candles.to_parquet(filepath, index=False)
        print(f"  {symbol}: saved {len(candles)} bars to {filepath}")

    print(f"\nDone. Data cached to {CACHE_DIR}")


if __name__ == "__main__":
    main()

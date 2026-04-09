"""Shared pure math functions for strategy signal generation.

Imported by strategy.py.
"""

from typing import Optional

import numpy as np
import pandas as pd


def ema(values: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1)
    result = np.empty_like(values, dtype=float)
    result[0] = values[0]
    for i in range(1, len(values)):
        result[i] = alpha * values[i] + (1 - alpha) * result[i - 1]
    return result


def calc_rsi(closes: np.ndarray, period: int) -> float:
    """Returns 50.0 when insufficient data."""
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = float(np.mean(gains))
    avg_loss = float(np.mean(losses))
    rs = avg_gain / max(avg_loss, 1e-10)
    return float(100 - 100 / (1 + rs))


def calc_atr(history: pd.DataFrame, lookback: int) -> Optional[float]:
    """Returns None when history is too short."""
    if len(history) < lookback + 1:
        return None
    highs = np.asarray(history["high"].values, dtype=float)[-lookback:]
    lows = np.asarray(history["low"].values, dtype=float)[-lookback:]
    closes = np.asarray(history["close"].values, dtype=float)[-(lookback + 1):-1]
    tr = np.maximum(
        highs - lows,
        np.maximum(np.abs(highs - closes), np.abs(lows - closes)),
    )
    return float(np.mean(tr))


def calc_vol(closes: np.ndarray, lookback: int, target_vol: float) -> float:
    """NaN-safe: returns target_vol on zeros/negatives/non-finite."""
    if len(closes) < lookback:
        return target_vol
    valid = closes[-lookback:]
    if bool(np.any(valid <= 0)):
        return target_vol
    log_rets = np.diff(np.log(valid))
    vol = float(np.std(log_rets))
    return max(vol, 1e-6) if np.isfinite(vol) else target_vol


def calc_macd(closes: np.ndarray, fast: int, slow: int, signal: int) -> float:
    """Returns 0.0 when insufficient data."""
    if len(closes) < slow + signal + 5:
        return 0.0
    fast_ema = ema(closes[-(slow + signal + 5):], fast)
    slow_ema = ema(closes[-(slow + signal + 5):], slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    return float(macd_line[-1] - signal_line[-1])


def calc_bb_width_pctile(closes: np.ndarray, period: int) -> float:
    """Returns 50.0 when insufficient data. Uses valid[-2] to exclude
    the current bar from its own percentile."""
    if len(closes) < period * 3:
        return 50.0
    windows = np.lib.stride_tricks.sliding_window_view(closes, period)
    sma = windows.mean(axis=1)
    std = windows.std(axis=1)
    widths = np.where(sma > 0, (2 * std) / sma, 0.0)
    valid = widths[period:]
    if valid.size < 2:
        return 50.0
    current_width = float(valid[-2])
    pctile = float(100 * np.sum(valid[:-1] <= current_width) / int(valid[:-1].size))
    return pctile

"""
Single source of truth for trading system constants.

Imported by data_pipeline/, live_trading_bot/, and repo-root scripts.
All symbol lists, fees, API URLs, interval maps, and strategy defaults
live here — change once, propagate everywhere.

IMPORTANT: This file must have ZERO project imports to avoid circular deps.
Only stdlib allowed.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------
ALL_SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "HYPE"]

# Per-interval active symbol sets (each is a subset of ALL_SYMBOLS).
# Used by strategies, data pipeline, and benchmarks.
INTERVAL_SYMBOLS: dict[str, list[str]] = {
    "1h": ["BTC", "ETH", "SOL", "XRP", "HYPE"],
    "15m": ["BTC", "ETH", "SOL", "XRP", "HYPE"],
    "5m": ["BTC", "ETH", "SOL", "XRP","HYPE"],
    "1m": ["BTC", "ETH", "SOL", "XRP","HYPE"],
}

BENCHMARK_SYMBOLS = ["BTC", "ETH", "SOL"]

DEFAULT_SYMBOL_WEIGHT = 0.25


def make_equal_weights(
    symbols: list[str] | None = None,
) -> dict[str, float]:
    """Create equal-weight dict for given symbols."""
    if symbols is None:
        symbols = ALL_SYMBOLS
    return {s: DEFAULT_SYMBOL_WEIGHT for s in symbols}


# ---------------------------------------------------------------------------
# Fees & Slippage
# ---------------------------------------------------------------------------
MAKER_FEE = 0.0002  # 2 bps
TAKER_FEE = 0.0005  # 5 bps
SLIPPAGE_BPS = 25.0  # 25 bps (0.25%)


# ---------------------------------------------------------------------------
# Hyperliquid API
# ---------------------------------------------------------------------------
HYPERLIQUID_API_URL = "https://api.hyperliquid.xyz"
HYPERLIQUID_WS_URL = "wss://api.hyperliquid.xyz/ws"
HL_INFO_URL = "https://api.hyperliquid.xyz/info"


# ---------------------------------------------------------------------------
# Intervals & Lookback
# ---------------------------------------------------------------------------
INTERVAL_MINUTES: dict[str, int] = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}
VALID_INTERVALS = list(INTERVAL_MINUTES.keys())

# Used by live_trading_bot/config/settings.py (production lookback)
LOOKBACK_BARS: dict[str, int] = {
    "1m": 1000,
    "5m": 1000,
    "15m": 500,
    "1h": 500,
}

# Used by data_pipeline/backtest/backtest_interval.py (backtest lookback)
BACKTEST_LOOKBACK_BARS: dict[str, int] = {
    "1m": 1500,
    "5m": 1500,
    "15m": 500,
    "1h": 500,
}


# ---------------------------------------------------------------------------
# Capital
# ---------------------------------------------------------------------------
INITIAL_CAPITAL = 100_000.0
BACKTEST_CAPITAL = 100_000.0


# ---------------------------------------------------------------------------
# PARAM_COLUMNS — DB schema for tunable strategy params
# ---------------------------------------------------------------------------
PARAM_COLUMNS: list[str] = [
    "SHORT_WINDOW",
    "MED_WINDOW",
    "MED2_WINDOW",
    "LONG_WINDOW",
    "EMA_FAST",
    "EMA_SLOW",
    "RSI_PERIOD",
    "RSI_BULL",
    "RSI_BEAR",
    "RSI_OVERBOUGHT",
    "RSI_OVERSOLD",
    "MACD_FAST",
    "MACD_SLOW",
    "MACD_SIGNAL",
    "BB_PERIOD",
    "FUNDING_LOOKBACK",
    "FUNDING_BOOST",
    "BASE_POSITION_PCT",
    "VOL_LOOKBACK",
    "TARGET_VOL",
    "ATR_LOOKBACK",
    "ATR_STOP_MULT",
    "TAKE_PROFIT_PCT",
    "BASE_THRESHOLD",
    "BTC_OPPOSE_THRESHOLD",
    "PYRAMID_THRESHOLD",
    "PYRAMID_SIZE",
    "CORR_LOOKBACK",
    "HIGH_CORR_THRESHOLD",
    "DD_REDUCE_THRESHOLD",
    "DD_REDUCE_SCALE",
    "COOLDOWN_BARS",
    "MIN_VOTES",
    "THRESHOLD_MIN",
    "THRESHOLD_MAX",
    "BB_COMPRESS_PCTILE",
]


# ---------------------------------------------------------------------------
# Uniform Strategy Defaults — same value across ALL intervals
# ---------------------------------------------------------------------------
UNIFORM_DEFAULTS: dict[str, int | float] = {
    "RSI_BULL": 50,
    "RSI_BEAR": 50,
    "RSI_OVERBOUGHT": 69,
    "RSI_OVERSOLD": 31,
    "MIN_VOTES": 4,
    "BASE_THRESHOLD": 0.012,
    "TARGET_VOL": 0.015,
    "PYRAMID_THRESHOLD": 0.015,
    "PYRAMID_SIZE": 0.0,
    "BTC_OPPOSE_THRESHOLD": -99.0,
    "HIGH_CORR_THRESHOLD": 99.0,
    "DD_REDUCE_THRESHOLD": 99.0,
    "DD_REDUCE_SCALE": 0.5,
    "TAKE_PROFIT_PCT": 99.0,
}


# ---------------------------------------------------------------------------
# Per-Interval Strategy Defaults
# ---------------------------------------------------------------------------
# Each interval's dict merges its interval-specific params with UNIFORM_DEFAULTS.
# Later keys override earlier keys (e.g. 1m's ATR_STOP_MULT=6.5 beats uniform 5.5).
STRATEGY_DEFAULTS: dict[str, dict[str, int | float]] = {
    "15m": {
        "SHORT_WINDOW": 24,
        "MED_WINDOW": 48,
        "MED2_WINDOW": 96,
        "LONG_WINDOW": 144,
        "EMA_FAST": 28,
        "EMA_SLOW": 104,
        "RSI_PERIOD": 32,
        "BB_PERIOD": 28,
        "MACD_FAST": 56,
        "MACD_SLOW": 92,
        "MACD_SIGNAL": 36,
        "ATR_LOOKBACK": 96,
        "VOL_LOOKBACK": 144,
        "BASE_POSITION_PCT": 0.08,
        "COOLDOWN_BARS": 8,
        "ATR_STOP_MULT": 5.5,
        "FUNDING_LOOKBACK": 96,
        "CORR_LOOKBACK": 288,
        "BB_COMPRESS_PCTILE": 90,
        "THRESHOLD_MIN": 0.005,
        "THRESHOLD_MAX": 0.020,
        "FUNDING_BOOST": 0.0,
        **UNIFORM_DEFAULTS,
    },
    "1m": {
        "SHORT_WINDOW": 60,
        "MED_WINDOW": 240,
        "MED2_WINDOW": 480,
        "LONG_WINDOW": 720,
        "EMA_FAST": 60,
        "EMA_SLOW": 240,
        "RSI_PERIOD": 60,
        "BB_PERIOD": 60,
        "MACD_FAST": 120,
        "MACD_SLOW": 240,
        "MACD_SIGNAL": 60,
        "ATR_LOOKBACK": 120,
        "VOL_LOOKBACK": 240,
        "BASE_POSITION_PCT": 2.00,
        "COOLDOWN_BARS": 60,
        "ATR_STOP_MULT": 6.5,
        "FUNDING_LOOKBACK": 1440,
        "CORR_LOOKBACK": 360,
        "FUNDING_BOOST": 0.0,
        **UNIFORM_DEFAULTS,
    },
    "5m": {
        "SHORT_WINDOW": 72,
        "MED_WINDOW": 144,
        "MED2_WINDOW": 288,
        "LONG_WINDOW": 432,
        "EMA_FAST": 60,
        "EMA_SLOW": 240,
        "RSI_PERIOD": 60,
        "BB_PERIOD": 60,
        "MACD_FAST": 120,
        "MACD_SLOW": 240,
        "MACD_SIGNAL": 60,
        "ATR_LOOKBACK": 120,
        "VOL_LOOKBACK": 240,
        "BASE_POSITION_PCT": 0.50,
        "COOLDOWN_BARS": 12,
        "FUNDING_LOOKBACK": 288,
        "CORR_LOOKBACK": 360,
        "FUNDING_BOOST": 0.0,
        **UNIFORM_DEFAULTS,
    },
    "1h": {
        "SHORT_WINDOW": 6,
        "MED_WINDOW": 12,
        "MED2_WINDOW": 24,
        "LONG_WINDOW": 36,
        "EMA_FAST": 7,
        "EMA_SLOW": 26,
        "RSI_PERIOD": 8,
        "BB_PERIOD": 7,
        "MACD_FAST": 14,
        "MACD_SLOW": 23,
        "MACD_SIGNAL": 9,
        "ATR_LOOKBACK": 24,
        "ATR_STOP_MULT": 5.5,
        "VOL_LOOKBACK": 36,
        "BASE_POSITION_PCT": 0.088,
        "COOLDOWN_BARS": 2,
        "FUNDING_LOOKBACK": 24,
        "CORR_LOOKBACK": 72,
        "FUNDING_BOOST": 0.0,
        **UNIFORM_DEFAULTS,
    },
}

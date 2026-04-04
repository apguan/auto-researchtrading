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
    "5m": ["BTC", "ETH", "SOL", "XRP", "HYPE"],
    "1m": ["BTC", "ETH", "SOL", "XRP", "HYPE"],
}

BENCHMARK_SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "HYPE", "ALGO", "LIT", "MON", "NEAR", "TAO", "XPL", "ZEC"]

def make_equal_weights(
    symbols: list[str] | None = None,
) -> dict[str, float]:
    """Create equal-weight dict for given symbols (1/N each)."""
    if symbols is None:
        symbols = ALL_SYMBOLS
    w = 1.0 / len(symbols)
    return {s: w for s in symbols}


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
    "1h": 500,
}

# Used by data_pipeline/backtest/backtest_interval.py (backtest lookback)
BACKTEST_LOOKBACK_BARS: dict[str, int] = {
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
    "BASE_POSITION_PCT",
    "VOL_LOOKBACK",
    "TARGET_VOL",
    "ATR_LOOKBACK",
    "ATR_STOP_MULT",
    "TAKE_PROFIT_PCT",
    "BASE_THRESHOLD",
    "COOLDOWN_BARS",
    "MIN_VOTES",
    "OBV_MA_PERIOD",
]

INT_PARAMS: set[str] = {
    "SHORT_WINDOW", "MED_WINDOW", "MED2_WINDOW", "LONG_WINDOW",
    "EMA_FAST", "EMA_SLOW", "RSI_PERIOD",
    "MACD_FAST", "MACD_SLOW",
    "MACD_SIGNAL", "BB_PERIOD",
    "VOL_LOOKBACK", "ATR_LOOKBACK", "COOLDOWN_BARS", "MIN_VOTES",
    "OBV_MA_PERIOD",
}


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
    "TAKE_PROFIT_PCT": 99.0,
}


# ---------------------------------------------------------------------------
# Per-Interval Strategy Defaults
# ---------------------------------------------------------------------------
# Each interval's dict merges its interval-specific params with UNIFORM_DEFAULTS.
# Later keys override earlier keys (e.g. 1m's ATR_STOP_MULT=6.5 beats uniform 5.5).
STRATEGY_DEFAULTS: dict[str, dict[str, int | float]] = {
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
        "COOLDOWN_BARS": 0,
        "EXIT_CONVICTION_BARS": 2,
        "MIN_HOLD_BARS": 2,
        # Execution-layer config (NOT in PARAM_COLUMNS — not strategy tuning)
        "MOMENTUM_VETO_THRESHOLD": 0.005,
        "REENTRY_GRACE_BARS": 3,
        "OBV_MA_PERIOD": 20,
        **UNIFORM_DEFAULTS,
    },
}

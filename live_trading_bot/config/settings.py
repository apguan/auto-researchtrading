from dataclasses import dataclass, field
from typing import List, Dict
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from constants import ALL_SYMBOLS, INTERVAL_SYMBOLS, make_equal_weights, LOOKBACK_BARS
from constants import STRATEGY_DEFAULTS as _STRATEGY_DEFAULTS
from constants import PARAM_COLUMNS, INT_PARAMS as _INT_PARAMS
from symbol_utils import discover_usdc_perps

_HOUR_DEFAULTS = _STRATEGY_DEFAULTS["1h"]


def _load_active_db_params() -> dict[str, int | float | list[str]]:
    """Load the active 1h params from param_snapshots (is_active=TRUE).

    Returns a dict of param_name -> value, plus 'TRADING_PAIRS' from
    the snapshot's symbol column. Empty dict on any failure.
    """
    db_url = os.environ.get("SUPABASE_DB_URL", "")
    if not db_url:
        return {}

    try:
        import psycopg2

        with psycopg2.connect(db_url) as conn:
            with conn.cursor() as cur:
                select_cols = ", ".join(PARAM_COLUMNS)
                cur.execute(
                    f"SELECT symbol, {select_cols} "
                    "FROM param_snapshots WHERE is_active = TRUE AND period = '1h' "
                    "ORDER BY run_date DESC LIMIT 1"
                )
                row = cur.fetchone()
                if not row:
                    return {}

                symbol_str = row[0]
                params: dict[str, int | float | list[str]] = {}
                for name, val in zip(PARAM_COLUMNS, row[1:]):
                    if name in _INT_PARAMS:
                        params[name] = int(val)
                    else:
                        params[name] = float(val)

                if symbol_str and symbol_str != "ALL":
                    symbols = [s.strip() for s in symbol_str.split(",") if s.strip()]
                    if symbols:
                        params["TRADING_PAIRS"] = symbols

                from live_trading_bot.monitoring.logger import get_logger

                get_logger(__name__).info(
                    "Loaded strategy params from DB",
                    extra={"param_count": len(params), "symbols": symbol_str},
                )
                return params
    except Exception:
        from live_trading_bot.monitoring.logger import get_logger
        get_logger(__name__).warning("Failed to load params from DB — using defaults", exc_info=True)
        return {}


@dataclass
class Settings:
    TRADING_PAIRS: List[str] = field(default_factory=lambda: list(ALL_SYMBOLS))
    SYMBOL_WEIGHTS: Dict[str, float] = field(
        default_factory=lambda: make_equal_weights(ALL_SYMBOLS)
    )
    BAR_INTERVAL: str = "1h"
    LOOKBACK_BARS: int = 500

    BASE_POSITION_PCT: float = float(_HOUR_DEFAULTS["BASE_POSITION_PCT"])
    MAX_POSITION_PCT: float = 0.30
    MAX_LEVERAGE: float = 3.0

    DAILY_LOSS_LIMIT_PCT: float = 0.05
    VOLATILITY_CIRCUIT_BREAKER_PCT: float = 0.05
    VOLATILITY_LOOKBACK_MINUTES: int = 10

    COOLDOWN_BARS: int = int(_HOUR_DEFAULTS["COOLDOWN_BARS"])
    EXIT_CONVICTION_BARS: int = int(_HOUR_DEFAULTS["EXIT_CONVICTION_BARS"])
    MIN_HOLD_BARS: int = int(_HOUR_DEFAULTS["MIN_HOLD_BARS"])
    MIN_VOTES: int = 4

    SHORT_WINDOW: int = int(_HOUR_DEFAULTS["SHORT_WINDOW"])
    MED_WINDOW: int = int(_HOUR_DEFAULTS["MED_WINDOW"])
    MED2_WINDOW: int = int(_HOUR_DEFAULTS["MED2_WINDOW"])
    LONG_WINDOW: int = int(_HOUR_DEFAULTS["LONG_WINDOW"])
    EMA_FAST: int = int(_HOUR_DEFAULTS["EMA_FAST"])
    EMA_SLOW: int = int(_HOUR_DEFAULTS["EMA_SLOW"])
    RSI_PERIOD: int = int(_HOUR_DEFAULTS["RSI_PERIOD"])
    RSI_BULL: float = float(_HOUR_DEFAULTS["RSI_BULL"])
    RSI_BEAR: float = float(_HOUR_DEFAULTS["RSI_BEAR"])
    RSI_OVERBOUGHT: float = float(_HOUR_DEFAULTS["RSI_OVERBOUGHT"])
    RSI_OVERSOLD: float = float(_HOUR_DEFAULTS["RSI_OVERSOLD"])
    MACD_FAST: int = int(_HOUR_DEFAULTS["MACD_FAST"])
    MACD_SLOW: int = int(_HOUR_DEFAULTS["MACD_SLOW"])
    MACD_SIGNAL: int = int(_HOUR_DEFAULTS["MACD_SIGNAL"])
    BB_PERIOD: int = int(_HOUR_DEFAULTS["BB_PERIOD"])
    ATR_LOOKBACK: int = int(_HOUR_DEFAULTS["ATR_LOOKBACK"])
    ATR_STOP_MULT: float = float(_HOUR_DEFAULTS["ATR_STOP_MULT"])
    TAKE_PROFIT_PCT: float = float(_HOUR_DEFAULTS["TAKE_PROFIT_PCT"])
    TARGET_VOL: float = float(_HOUR_DEFAULTS["TARGET_VOL"])
    VOL_LOOKBACK: int = int(_HOUR_DEFAULTS["VOL_LOOKBACK"])
    BASE_THRESHOLD: float = float(_HOUR_DEFAULTS["BASE_THRESHOLD"])

    MOMENTUM_VETO_THRESHOLD: float = float(_HOUR_DEFAULTS["MOMENTUM_VETO_THRESHOLD"])
    REENTRY_GRACE_BARS: int = int(_HOUR_DEFAULTS["REENTRY_GRACE_BARS"])
    OBV_MA_PERIOD: int = int(_HOUR_DEFAULTS["OBV_MA_PERIOD"])

    HYPERLIQUID_API_URL: str = "https://api.hyperliquid.xyz"
    HYPERLIQUID_WS_URL: str = "wss://api.hyperliquid.xyz/ws"
    # Main wallet address for account state queries. Set this when using an API
    # wallet (which doesn't hold equity itself). If unset, defaults to the
    # address derived from the private key (correct when the PK is the main wallet's).
    HYPERLIQUID_MAIN_WALLET: str = ""
    # Vault/sub-account address. When set, orders are placed on behalf of this
    # vault and account state (positions, equity, fills) is queried from it.
    HYPERLIQUID_VAULT_ADDRESS: str = ""

    DB_PATH: str = "trading_bot.db"
    SUPABASE_DB_URL: str = ""
    LOG_PATH: str = "logs/bot.log"
    LOG_LEVEL: str = "INFO"

    ALERT_INTERVAL_HOURS: float = 1.0
    ALERT_ON_TRADE: bool = True
    ALERT_ON_ERROR: bool = True
    ALERT_ON_RISK_EVENT: bool = True
    ALERT_INSTANCE_NAME: str = ""

    DRY_RUN: bool = False
    DRY_RUN_INITIAL_CAPITAL: float = 10_000.0
    DRY_RUN_STATE_PATH: str = "/tmp/dry_run_state.json"

    ENTRY_SLIPPAGE_PCT: float = 0.02
    EXECUTION_COOLDOWN_MS: int = 5000

    EMERGENCY_EXIT_PCT: float = 0.10
    STOP_WIDENING_MULT: float = 1.5

    WATCHDOG_INTERVAL_SECONDS: int = 30
    WATCHDOG_HEARTBEAT_PATH: str = "/tmp/trading_bot_heartbeat"

    RECONNECT_DELAY_SECONDS: float = 1.0
    MAX_RECONNECT_DELAY_SECONDS: float = 60.0
    REQUEST_TIMEOUT_SECONDS: float = 30.0

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls()

        # Resolve BAR_INTERVAL first so TRADING_PAIRS can default from it
        if val := os.getenv("BAR_INTERVAL"):
            settings.BAR_INTERVAL = val

        if val := os.getenv("TRADING_PAIRS"):
            settings.TRADING_PAIRS = val.split(",")
        else:
            settings.TRADING_PAIRS = discover_usdc_perps()
        settings.SYMBOL_WEIGHTS = make_equal_weights(settings.TRADING_PAIRS)

        if val := os.getenv("MAX_LEVERAGE"):
            settings.MAX_LEVERAGE = float(val)
        else:
            from live_trading_bot.monitoring.logger import get_logger as _get_logger

            _logger = _get_logger(__name__)
            if settings.MAX_LEVERAGE == 3.0 and not settings.DRY_RUN:
                _logger.warning(
                    "MAX_LEVERAGE is using code default (3.0). "
                    "Set MAX_LEVERAGE env var explicitly. See .env.example."
                )

        if val := os.getenv("MAX_POSITION_PCT"):
            settings.MAX_POSITION_PCT = float(val)

        if val := os.getenv("DAILY_LOSS_LIMIT_PCT"):
            settings.DAILY_LOSS_LIMIT_PCT = float(val)

        if val := os.getenv("HYPERLIQUID_MAIN_WALLET"):
            settings.HYPERLIQUID_MAIN_WALLET = val

        if val := os.getenv("HYPERLIQUID_VAULT_ADDRESS"):
            settings.HYPERLIQUID_VAULT_ADDRESS = val

        if val := os.getenv("DRY_RUN"):
            settings.DRY_RUN = val.lower() in ("true", "1", "yes")

        if val := os.getenv("DB_PATH"):
            settings.DB_PATH = val

        if val := os.getenv("SUPABASE_DB_URL"):
            settings.SUPABASE_DB_URL = val

        if val := os.getenv("LOG_LEVEL"):
            settings.LOG_LEVEL = val.upper()

        if val := os.getenv("LOOKBACK_BARS"):
            settings.LOOKBACK_BARS = int(val)
        else:
            settings.LOOKBACK_BARS = LOOKBACK_BARS.get(settings.BAR_INTERVAL, 500)

        if val := os.getenv("DRY_RUN_INITIAL_CAPITAL"):
            settings.DRY_RUN_INITIAL_CAPITAL = float(val)

        if val := os.getenv("DRY_RUN_STATE_PATH"):
            settings.DRY_RUN_STATE_PATH = val

        if val := os.getenv("ENTRY_SLIPPAGE_PCT"):
            settings.ENTRY_SLIPPAGE_PCT = float(val)

        if val := os.getenv("EXECUTION_COOLDOWN_MS"):
            settings.EXECUTION_COOLDOWN_MS = int(val)

        if val := os.getenv("EXIT_CONVICTION_BARS"):
            settings.EXIT_CONVICTION_BARS = int(val)

        if val := os.getenv("MIN_HOLD_BARS"):
            settings.MIN_HOLD_BARS = int(val)

        if val := os.getenv("EMERGENCY_EXIT_PCT"):
            settings.EMERGENCY_EXIT_PCT = float(val)

        if val := os.getenv("STOP_WIDENING_MULT"):
            settings.STOP_WIDENING_MULT = float(val)

        if val := os.getenv("WATCHDOG_INTERVAL_SECONDS"):
            settings.WATCHDOG_INTERVAL_SECONDS = int(val)

        if val := os.getenv("WATCHDOG_HEARTBEAT_PATH"):
            settings.WATCHDOG_HEARTBEAT_PATH = val

        if val := os.getenv("ALERT_ON_TRADE"):
            settings.ALERT_ON_TRADE = val.lower() in ("true", "1", "yes")

        if val := os.getenv("ALERT_ON_ERROR"):
            settings.ALERT_ON_ERROR = val.lower() in ("true", "1", "yes")

        if val := os.getenv("ALERT_ON_RISK_EVENT"):
            settings.ALERT_ON_RISK_EVENT = val.lower() in ("true", "1", "yes")

        if val := os.getenv("ALERT_INTERVAL_HOURS"):
            settings.ALERT_INTERVAL_HOURS = float(val)

        if val := os.getenv("ALERT_INSTANCE_NAME"):
            settings.ALERT_INSTANCE_NAME = val

        if val := os.getenv("MOMENTUM_VETO_THRESHOLD"):
            settings.MOMENTUM_VETO_THRESHOLD = float(val)

        if val := os.getenv("REENTRY_GRACE_BARS"):
            settings.REENTRY_GRACE_BARS = int(val)

        if val := os.getenv("OBV_MA_PERIOD"):
            settings.OBV_MA_PERIOD = int(val)

        _apply_db_params(settings)

        return settings


_settings: Settings | None = None


def _apply_db_params(settings: Settings) -> None:
    db_params = _load_active_db_params()
    if not db_params:
        return

    trading_pairs = db_params.pop("TRADING_PAIRS", None)
    for name, val in db_params.items():
        if hasattr(settings, name):
            setattr(settings, name, val)

    if isinstance(trading_pairs, list):
        settings.TRADING_PAIRS = trading_pairs
        settings.SYMBOL_WEIGHTS = make_equal_weights(trading_pairs)


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    assert _settings is not None
    return _settings

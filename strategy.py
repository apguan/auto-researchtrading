"""
Exp322: RSI_BULL=38 RSI_BEAR=62 wider at new baseline.

Changes from exp302 (score 16.145):
1. RSI_BULL=38 (was 40), RSI_BEAR=62 (was 60) — much wider RSI entry zone.
   39/61 scored 16.142. Test 38/62.
"""

import numpy as np
from strategy_utils import ema, calc_rsi, calc_atr, calc_vol, calc_macd, calc_bb_width_pctile
from strategy_types import Signal
from constants import INTERVAL_SYMBOLS

ACTIVE_SYMBOLS = INTERVAL_SYMBOLS["1h"]  # fallback for _RUNTIME_SYMBOLS init; unused by on_bar

# Populated on first on_bar() call with the symbols actually in bar_data.
# Used by save_experiment_to_db() to persist the dynamic symbol set.
_RUNTIME_SYMBOLS: list[str] = list(ACTIVE_SYMBOLS)
_symbols_from_backtest = False

SHORT_WINDOW = 6
MED_WINDOW = 11
MED2_WINDOW = 24
LONG_WINDOW = 48
EMA_FAST = 3
EMA_SLOW = 27
RSI_PERIOD = 7
RSI_BULL = 38
RSI_BEAR = 62
RSI_OVERBOUGHT = 74
RSI_OVERSOLD = 26

MACD_FAST = 11
MACD_SLOW = 21
MACD_SIGNAL = 9

BB_PERIOD = 6
OBV_MA_PERIOD = 26

BASE_POSITION_PCT = 0.060
VOL_LOOKBACK = 41
TARGET_VOL = 0.014
ATR_LOOKBACK = 24
ATR_STOP_MULT = 5.5
TAKE_PROFIT_PCT = 99.0
BASE_THRESHOLD = 0.012

COOLDOWN_BARS = 3
MIN_VOTES = 5  # out of 7
REGIME_THRESHOLD = 0.6  # fraction of symbols trending same direction to declare market regime


class Strategy:
    def __init__(self):
        self.entry_prices = {}
        self.peak_prices = {}
        self.atr_at_entry = {}
        self.exit_bar = {}
        self.bar_count = 0
        self._symbols_initialized = False

    def on_bar(self, bar_data, portfolio):
        global _RUNTIME_SYMBOLS
        signals = []
        equity = portfolio.equity if portfolio.equity > 0 else portfolio.cash
        self.bar_count += 1

        if not self._symbols_initialized:
            _RUNTIME_SYMBOLS[:] = sorted(bar_data.keys())
            global _symbols_from_backtest
            _symbols_from_backtest = True
            self._symbols_initialized = True
        else:
            new = [s for s in bar_data if s not in _RUNTIME_SYMBOLS]
            if new:
                _RUNTIME_SYMBOLS = sorted(_RUNTIME_SYMBOLS + new)

        bullish_count = 0
        bearish_count = 0
        regime_total = 0

        for _sym, _bd in bar_data.items():
            _closes = _bd.history["close"].values
            if len(_closes) < MED_WINDOW + 1:
                continue
            if _bd.close <= 0 or _closes[-MED_WINDOW] <= 0:
                continue
            _ret = (_closes[-1] - _closes[-MED_WINDOW]) / max(_closes[-MED_WINDOW], 1e-10)
            regime_total += 1
            if _ret > 0:
                bullish_count += 1
            else:
                bearish_count += 1

        market_bullish = regime_total > 0 and bullish_count / regime_total >= REGIME_THRESHOLD
        market_bearish = regime_total > 0 and bearish_count / regime_total >= REGIME_THRESHOLD

        for symbol, bd in bar_data.items():
            if (
                len(bd.history)
                < max(LONG_WINDOW, EMA_SLOW, MACD_SLOW + MACD_SIGNAL + 5, BB_PERIOD * 3)
                + 1
            ):
                continue

            closes = bd.history["close"].values
            mid = bd.close

            if mid <= 0 or np.any(closes <= 0):
                continue

            realized_vol = calc_vol(closes, VOL_LOOKBACK, TARGET_VOL)
            vol_ratio = realized_vol / TARGET_VOL
            dyn_threshold = BASE_THRESHOLD * (0.3 + vol_ratio * 0.7)
            dyn_threshold = max(0.005, min(0.020, dyn_threshold))

            ret_vshort = (closes[-1] - closes[-SHORT_WINDOW]) / max(closes[-SHORT_WINDOW], 1e-10)
            ret_short = (closes[-1] - closes[-MED_WINDOW]) / max(closes[-MED_WINDOW], 1e-10)
            mom_bull = ret_short > dyn_threshold
            mom_bear = ret_short < -dyn_threshold
            vshort_bull = ret_vshort > dyn_threshold * 0.7
            vshort_bear = ret_vshort < -dyn_threshold * 0.7

            ema_fast_arr = ema(closes[-(EMA_SLOW + 10) :], EMA_FAST)
            ema_slow_arr = ema(closes[-(EMA_SLOW + 10) :], EMA_SLOW)
            ema_bull = ema_fast_arr[-1] > ema_slow_arr[-1]
            ema_bear = ema_fast_arr[-1] < ema_slow_arr[-1]

            rsi = calc_rsi(closes, RSI_PERIOD)
            rsi_bull = rsi > RSI_BULL
            rsi_bear = rsi < RSI_BEAR

            macd_hist = calc_macd(closes, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
            macd_bull = macd_hist > 0
            macd_bear = macd_hist < 0

            # BB width: low percentile = compression = pending breakout
            bb_pctile = calc_bb_width_pctile(closes, BB_PERIOD)
            bb_compressed = bb_pctile < 90

            # OBV trend: On-Balance Volume vs its MA
            vol_data = bd.history["volume"].values
            vol_bull = False
            vol_bear = False
            if len(vol_data) > OBV_MA_PERIOD and len(closes) > OBV_MA_PERIOD:
                price_changes = np.diff(closes[-(OBV_MA_PERIOD + 1):])
                recent_vol = vol_data[-(OBV_MA_PERIOD):]
                signed_vol = np.where(
                    price_changes > 0, recent_vol,
                    np.where(price_changes < 0, -recent_vol, 0.0),
                )
                obv = np.cumsum(signed_vol)
                obv_ma = np.mean(obv[-OBV_MA_PERIOD:])
                vol_bull = obv[-1] > obv_ma
                vol_bear = obv[-1] < obv_ma

            bull_votes = sum(
                [mom_bull, vshort_bull, ema_bull, rsi_bull, macd_bull, bb_compressed, vol_bull]
            )
            bear_votes = sum(
                [mom_bear, vshort_bear, ema_bear, rsi_bear, macd_bear, bb_compressed, vol_bear]
            )

            bullish = bull_votes >= MIN_VOTES
            bearish = bear_votes >= MIN_VOTES

            in_cooldown = (
                self.bar_count - self.exit_bar.get(symbol, -999)
            ) < COOLDOWN_BARS

            vol_scale = 1.0
            weight = 1.0 / max(len(bar_data), 1)
            strength_scale = 1.0
            size = (
                equity
                * BASE_POSITION_PCT
                * weight
                * vol_scale
                * strength_scale
            )

            current_pos = portfolio.positions.get(symbol, 0.0)
            target = current_pos

            if current_pos == 0:
                if not in_cooldown:
                    if bullish and not market_bearish:
                        target = size
                    elif bearish and not market_bullish:
                        target = -size
            else:
                atr = calc_atr(bd.history, ATR_LOOKBACK)
                if atr is None:
                    atr = self.atr_at_entry.get(symbol, mid * 0.02)

                if symbol not in self.peak_prices:
                    self.peak_prices[symbol] = mid

                if current_pos > 0:
                    self.peak_prices[symbol] = max(self.peak_prices[symbol], mid)
                    stop = self.peak_prices[symbol] - ATR_STOP_MULT * atr
                    if mid < stop:
                        target = 0.0
                else:
                    self.peak_prices[symbol] = min(self.peak_prices[symbol], mid)
                    stop = self.peak_prices[symbol] + ATR_STOP_MULT * atr
                    if mid > stop:
                        target = 0.0

                if symbol in self.entry_prices:
                    entry = self.entry_prices[symbol]
                    pnl = (mid - entry) / entry
                    if current_pos < 0:
                        pnl = -pnl
                    if pnl > TAKE_PROFIT_PCT:
                        target = 0.0

                if current_pos > 0 and rsi > RSI_OVERBOUGHT:
                    target = 0.0
                elif current_pos < 0 and rsi < RSI_OVERSOLD:
                    target = 0.0

                if current_pos > 0 and bearish and not in_cooldown:
                    target = -size if not market_bullish else 0
                elif current_pos < 0 and bullish and not in_cooldown:
                    target = size if not market_bearish else 0

            if abs(target - current_pos) > 1.0:
                signals.append(Signal(symbol=symbol, target_position=target))
                if target != 0 and current_pos == 0:
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.atr_at_entry[symbol] = (
                        calc_atr(bd.history, ATR_LOOKBACK) or mid * 0.02
                    )
                elif target == 0:
                    self.entry_prices.pop(symbol, None)
                    self.peak_prices.pop(symbol, None)
                    self.atr_at_entry.pop(symbol, None)
                    self.exit_bar[symbol] = self.bar_count
                elif (target > 0 and current_pos < 0) or (
                    target < 0 and current_pos > 0
                ):
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.atr_at_entry[symbol] = (
                        calc_atr(bd.history, ATR_LOOKBACK) or mid * 0.02
                    )

        return signals


ACTIVE_PARAMS = (
    "SHORT_WINDOW", "MED_WINDOW", "MED2_WINDOW", "LONG_WINDOW",
    "EMA_FAST", "EMA_SLOW", "RSI_PERIOD", "RSI_BULL", "RSI_BEAR",
    "RSI_OVERBOUGHT", "RSI_OVERSOLD", "MACD_FAST", "MACD_SLOW",
    "MACD_SIGNAL", "BB_PERIOD",
    "BASE_POSITION_PCT", "VOL_LOOKBACK", "TARGET_VOL", "ATR_LOOKBACK",
    "ATR_STOP_MULT", "TAKE_PROFIT_PCT", "BASE_THRESHOLD",
    "COOLDOWN_BARS", "MIN_VOTES", "OBV_MA_PERIOD",
)

INT_PARAMS = {
    "SHORT_WINDOW", "MED_WINDOW", "MED2_WINDOW", "LONG_WINDOW",
    "EMA_FAST", "EMA_SLOW", "RSI_PERIOD", "RSI_BULL", "RSI_BEAR",
    "RSI_OVERBOUGHT", "RSI_OVERSOLD", "MACD_FAST", "MACD_SLOW",
    "MACD_SIGNAL", "BB_PERIOD",
    "VOL_LOOKBACK", "ATR_LOOKBACK", "COOLDOWN_BARS", "MIN_VOTES",
    "OBV_MA_PERIOD",
}


def _get_current_params() -> dict[str, int | float]:
    return {k: globals()[k] for k in ACTIVE_PARAMS}


def _get_runtime_symbols() -> list[str]:
    if _symbols_from_backtest:
        return _RUNTIME_SYMBOLS
    from pathlib import Path
    import glob
    cache_dir = Path.home() / ".cache" / "autotrader" / "data"
    parquets = sorted(glob.glob(str(cache_dir / "*_1h.parquet")))
    if parquets:
        return sorted(Path(p).stem.replace("_1h", "") for p in parquets)
    return _RUNTIME_SYMBOLS


def save_experiment_to_db(
    score: float,
    sharpe: float,
    total_return_pct: float,
    max_drawdown_pct: float,
    num_trades: int,
    win_rate_pct: float,
    profit_factor: float,
    description: str = "",
    status: str = "PASS",
    is_best: bool = True,
) -> bool:
    import os
    import sys
    import psycopg2
    from datetime import datetime, timezone

    db_url = os.environ.get("SUPABASE_DB_URL", "")
    if not db_url:
        return False

    params = _get_current_params()

    conn = None
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        param_cols = ", ".join(ACTIVE_PARAMS)
        all_cols = (
            "run_date, sweep_name, period, symbol, is_active, is_best, "
            "sharpe, total_return_pct, max_drawdown_pct, "
            "profit_factor, win_rate_pct, num_trades, ret_dd_ratio, "
            "score, status, description, "
            + param_cols
        )
        all_placeholders = ", ".join(["%s"] * (16 + len(ACTIVE_PARAMS)))

        ret_dd_ratio = (
            total_return_pct / max_drawdown_pct
            if max_drawdown_pct > 0 else 0.0
        )

        values = [
            datetime.now(timezone.utc).isoformat(),
            "autoresearch",
            "1h",
            ",".join(_get_runtime_symbols()),
            False,
            is_best,
            sharpe,
            total_return_pct,
            max_drawdown_pct,
            profit_factor,
            win_rate_pct,
            num_trades,
            ret_dd_ratio,
            score,
            status,
            description,
        ]
        for c in ACTIVE_PARAMS:
            values.append(float(params[c]))

        cur.execute(
            f"INSERT INTO param_snapshots ({all_cols}) VALUES ({all_placeholders})",
            values,
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"save_experiment_to_db error: {e}", file=sys.stderr)
        return False
    finally:
        if conn:
            conn.close()


def load_params_from_db() -> bool:
    import os
    import psycopg2

    db_url = os.environ.get("SUPABASE_DB_URL", "")
    if not db_url:
        return False

    try:
        with psycopg2.connect(db_url) as conn:
            with conn.cursor() as cur:
                select_cols = ", ".join(ACTIVE_PARAMS)
                cur.execute(
                    f"SELECT symbol, {select_cols} "
                    "FROM param_snapshots WHERE is_active = TRUE AND period = '1h' "
                    "ORDER BY run_date DESC LIMIT 1"
                )
                row = cur.fetchone()
                if not row:
                    return False

                symbol_str = row[0]
                for name, val in zip(ACTIVE_PARAMS, row[1:]):
                    if name in INT_PARAMS:
                        globals()[name] = int(val)
                    else:
                        globals()[name] = float(val)

                if symbol_str and symbol_str != "ALL":
                    loaded_symbols = [s.strip() for s in symbol_str.split(",") if s.strip()]
                    if loaded_symbols:
                        globals()["ACTIVE_SYMBOLS"] = loaded_symbols

                print(f"Loaded 1h params from DB (symbols: {symbol_str})")
                return True
    except Exception:
        return False


import os as _os
if _os.environ.get("LOAD_PARAMS_FROM_DB", "").lower() in ("1", "true", "yes"):
    load_params_from_db()

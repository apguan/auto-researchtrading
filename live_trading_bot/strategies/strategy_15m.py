"""15-minute resolution strategy — scaled from hourly base (1 hour = 4 bars)."""

import os
import sys
from pathlib import Path

import numpy as np
from prepare import Signal

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from constants import INTERVAL_SYMBOLS, make_equal_weights, PARAM_COLUMNS

ACTIVE_SYMBOLS = INTERVAL_SYMBOLS["15m"]
SYMBOL_WEIGHTS = make_equal_weights(ACTIVE_SYMBOLS)

SHORT_WINDOW = 24  # 6h
MED_WINDOW = 48  # 12h
MED2_WINDOW = 96  # 24h
LONG_WINDOW = 144  # 36h
EMA_FAST = 28  # 7h
EMA_SLOW = 104  # 26h
RSI_PERIOD = 32
RSI_BULL = 50
RSI_BEAR = 50
RSI_OVERBOUGHT = 69
RSI_OVERSOLD = 31

MACD_FAST = 56  # 14h
MACD_SLOW = 92  # 23h
MACD_SIGNAL = 36  # 9h

BB_PERIOD = 28  # 7h

FUNDING_LOOKBACK = 96  # 24h
FUNDING_BOOST = 0.0
BASE_POSITION_PCT = 0.08
VOL_LOOKBACK = 144  # 36h
TARGET_VOL = 0.015
ATR_LOOKBACK = 96  # 24h
ATR_STOP_MULT = 5.5
TAKE_PROFIT_PCT = 99.0
BASE_THRESHOLD = 0.012
BTC_OPPOSE_THRESHOLD = -99.0

PYRAMID_THRESHOLD = 0.015
PYRAMID_SIZE = 0.0
CORR_LOOKBACK = 288  # 72h
HIGH_CORR_THRESHOLD = 99.0

DD_REDUCE_THRESHOLD = 99.0
DD_REDUCE_SCALE = 0.5

COOLDOWN_BARS = 8
MIN_VOTES = 4

# Tunable thresholds (were hardcoded in on_bar)
THRESHOLD_MIN = 0.005
THRESHOLD_MAX = 0.020
BB_COMPRESS_PCTILE = 90

# Per-symbol params loaded from DB: {symbol: {param_name: value, ...}}
_SYMBOL_PARAMS: dict[str, dict] = {}


def _get_param(symbol: str, param_name: str):
    """Get a parameter value for a specific symbol, falling back to module default."""
    if symbol in _SYMBOL_PARAMS and param_name in _SYMBOL_PARAMS[symbol]:
        return _SYMBOL_PARAMS[symbol][param_name]
    return globals()[param_name]


def ema(values, span):
    alpha = 2.0 / (span + 1)
    result = np.empty_like(values, dtype=float)
    result[0] = values[0]
    for i in range(1, len(values)):
        result[i] = alpha * values[i] + (1 - alpha) * result[i - 1]
    return result


def calc_rsi(closes, period):
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-(period + 1) :])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    rs = avg_gain / max(avg_loss, 1e-10)
    return 100 - 100 / (1 + rs)


class Strategy:
    def __init__(self):
        self.entry_prices = {}
        self.peak_prices = {}
        self.atr_at_entry = {}
        self.btc_momentum = 0.0
        self.pyramided = {}
        self.peak_equity = 100000.0
        self.exit_bar = {}
        self.bar_count = 0

    def _calc_atr(self, history, lookback):
        if len(history) < lookback + 1:
            return None
        highs = history["high"].values[-lookback:]
        lows = history["low"].values[-lookback:]
        closes = history["close"].values[-(lookback + 1) : -1]
        tr = np.maximum(
            highs - lows, np.maximum(np.abs(highs - closes), np.abs(lows - closes))
        )
        return np.mean(tr)

    def _calc_vol(self, closes, lookback, target_vol=TARGET_VOL):
        if len(closes) < lookback:
            return target_vol
        log_rets = np.diff(np.log(closes[-lookback:]))
        return max(np.std(log_rets), 1e-6)

    def _calc_correlation(self, bar_data, corr_lookback=CORR_LOOKBACK):
        if "BTC" not in bar_data or "ETH" not in bar_data:
            return 0.5
        btc_h = bar_data["BTC"].history
        eth_h = bar_data["ETH"].history
        if len(btc_h) < corr_lookback or len(eth_h) < corr_lookback:
            return 0.5
        btc_rets = np.diff(np.log(btc_h["close"].values[-corr_lookback:]))
        eth_rets = np.diff(np.log(eth_h["close"].values[-corr_lookback:]))
        if len(btc_rets) < 10:
            return 0.5
        corr = np.corrcoef(btc_rets, eth_rets)[0, 1]
        return corr if not np.isnan(corr) else 0.5

    def _calc_macd(
        self, closes, macd_fast=MACD_FAST, macd_slow=MACD_SLOW, macd_signal=MACD_SIGNAL
    ):
        if len(closes) < macd_slow + macd_signal + 5:
            return 0.0
        fast_ema = ema(closes[-(macd_slow + macd_signal + 5) :], macd_fast)
        slow_ema = ema(closes[-(macd_slow + macd_signal + 5) :], macd_slow)
        macd_line = fast_ema - slow_ema
        signal_line = ema(macd_line, macd_signal)
        return macd_line[-1] - signal_line[-1]

    def _calc_bb_width_pctile(self, closes, period):
        if len(closes) < period * 3:
            return 50.0
        windows = np.lib.stride_tricks.sliding_window_view(closes, period)
        sma = windows.mean(axis=1)
        std = windows.std(axis=1)
        widths = np.where(sma > 0, (2 * std) / sma, 0.0)
        valid = widths[period:]
        if len(valid) < 2:
            return 50.0
        current_width = valid[
            -2
        ]  # match original: closes[n-1-period:n-1] excludes current bar
        pctile = 100 * np.sum(valid[:-1] <= current_width) / len(valid[:-1])
        return pctile

    def on_bar(self, bar_data, portfolio):
        signals = []
        equity = portfolio.equity if portfolio.equity > 0 else portfolio.cash
        self.bar_count += 1

        self.peak_equity = max(self.peak_equity, equity)
        current_dd = (self.peak_equity - equity) / self.peak_equity
        dd_scale = 1.0
        if current_dd > DD_REDUCE_THRESHOLD:
            dd_scale = max(
                DD_REDUCE_SCALE, 1.0 - (current_dd - DD_REDUCE_THRESHOLD) * 5
            )

        # BTC momentum uses BTC-specific params
        _BTC_LONG_WINDOW = _get_param("BTC", "LONG_WINDOW")
        _BTC_MED2_WINDOW = _get_param("BTC", "MED2_WINDOW")
        if "BTC" in bar_data and len(bar_data["BTC"].history) >= _BTC_LONG_WINDOW + 1:
            btc_closes = bar_data["BTC"].history["close"].values
            self.btc_momentum = (
                btc_closes[-1] - btc_closes[-_BTC_MED2_WINDOW]
            ) / btc_closes[-_BTC_MED2_WINDOW]

        btc_eth_corr = self._calc_correlation(bar_data)
        high_corr = btc_eth_corr > HIGH_CORR_THRESHOLD

        for symbol in ACTIVE_SYMBOLS:
            if symbol not in bar_data:
                continue
            bd = bar_data[symbol]

            _LONG_WINDOW = _get_param(symbol, "LONG_WINDOW")
            _MED2_WINDOW = _get_param(symbol, "MED2_WINDOW")
            _SHORT_WINDOW = _get_param(symbol, "SHORT_WINDOW")
            _MED_WINDOW = _get_param(symbol, "MED_WINDOW")
            _BASE_THRESHOLD = _get_param(symbol, "BASE_THRESHOLD")
            _THRESHOLD_MIN = THRESHOLD_MIN
            _THRESHOLD_MAX = THRESHOLD_MAX
            _EMA_FAST = _get_param(symbol, "EMA_FAST")
            _EMA_SLOW = _get_param(symbol, "EMA_SLOW")
            _RSI_PERIOD = _get_param(symbol, "RSI_PERIOD")
            _RSI_BULL = _get_param(symbol, "RSI_BULL")
            _RSI_BEAR = _get_param(symbol, "RSI_BEAR")
            _BB_PERIOD = _get_param(symbol, "BB_PERIOD")
            _BB_COMPRESS_PCTILE = BB_COMPRESS_PCTILE
            _MIN_VOTES = _get_param(symbol, "MIN_VOTES")
            _COOLDOWN_BARS = _get_param(symbol, "COOLDOWN_BARS")
            _BASE_POSITION_PCT = _get_param(symbol, "BASE_POSITION_PCT")
            _VOL_LOOKBACK = _get_param(symbol, "VOL_LOOKBACK")
            _TARGET_VOL = _get_param(symbol, "TARGET_VOL")
            _ATR_LOOKBACK = _get_param(symbol, "ATR_LOOKBACK")
            _ATR_STOP_MULT = _get_param(symbol, "ATR_STOP_MULT")
            _TAKE_PROFIT_PCT = _get_param(symbol, "TAKE_PROFIT_PCT")
            _RSI_OVERBOUGHT = _get_param(symbol, "RSI_OVERBOUGHT")
            _RSI_OVERSOLD = _get_param(symbol, "RSI_OVERSOLD")
            _PYRAMID_THRESHOLD = _get_param(symbol, "PYRAMID_THRESHOLD")
            _PYRAMID_SIZE = PYRAMID_SIZE
            _FUNDING_LOOKBACK = _get_param(symbol, "FUNDING_LOOKBACK")
            _FUNDING_BOOST = FUNDING_BOOST
            _BTC_OPPOSE_THRESHOLD = BTC_OPPOSE_THRESHOLD
            _MACD_FAST = _get_param(symbol, "MACD_FAST")
            _MACD_SLOW = _get_param(symbol, "MACD_SLOW")
            _MACD_SIGNAL = _get_param(symbol, "MACD_SIGNAL")

            if (
                len(bd.history)
                < max(
                    _LONG_WINDOW,
                    _EMA_SLOW,
                    _MACD_SLOW + _MACD_SIGNAL + 5,
                    _BB_PERIOD * 3,
                )
                + 1
            ):
                continue

            closes = bd.history["close"].values
            mid = bd.close

            realized_vol = self._calc_vol(closes, _VOL_LOOKBACK, _TARGET_VOL)
            vol_ratio = realized_vol / _TARGET_VOL
            dyn_threshold = _BASE_THRESHOLD * (0.3 + vol_ratio * 0.7)
            dyn_threshold = max(_THRESHOLD_MIN, min(_THRESHOLD_MAX, dyn_threshold))

            ret_vshort = (closes[-1] - closes[-_SHORT_WINDOW]) / closes[-_SHORT_WINDOW]
            ret_short = (closes[-1] - closes[-_MED_WINDOW]) / closes[-_MED_WINDOW]
            mom_bull = ret_short > dyn_threshold
            mom_bear = ret_short < -dyn_threshold
            vshort_bull = ret_vshort > dyn_threshold * 0.7
            vshort_bear = ret_vshort < -dyn_threshold * 0.7

            ema_fast_arr = ema(closes[-(_EMA_SLOW + 10) :], _EMA_FAST)
            ema_slow_arr = ema(closes[-(_EMA_SLOW + 10) :], _EMA_SLOW)
            ema_bull = ema_fast_arr[-1] > ema_slow_arr[-1]
            ema_bear = ema_fast_arr[-1] < ema_slow_arr[-1]

            rsi = calc_rsi(closes, _RSI_PERIOD)
            rsi_bull = rsi > _RSI_BULL
            rsi_bear = rsi < _RSI_BEAR

            macd_hist = self._calc_macd(closes, _MACD_FAST, _MACD_SLOW, _MACD_SIGNAL)
            macd_bull = macd_hist > 0
            macd_bear = macd_hist < 0

            bb_pctile = self._calc_bb_width_pctile(closes, _BB_PERIOD)
            bb_compressed = bb_pctile < _BB_COMPRESS_PCTILE

            bull_votes = sum(
                [mom_bull, vshort_bull, ema_bull, rsi_bull, macd_bull, bb_compressed]
            )
            bear_votes = sum(
                [mom_bear, vshort_bear, ema_bear, rsi_bear, macd_bear, bb_compressed]
            )

            btc_confirm = True
            if symbol != "BTC":
                if (
                    bull_votes >= _MIN_VOTES
                    and self.btc_momentum < _BTC_OPPOSE_THRESHOLD
                ):
                    btc_confirm = False
                if (
                    bear_votes >= _MIN_VOTES
                    and self.btc_momentum > -_BTC_OPPOSE_THRESHOLD
                ):
                    btc_confirm = False

            bullish = bull_votes >= _MIN_VOTES and btc_confirm
            bearish = bear_votes >= _MIN_VOTES and btc_confirm

            in_cooldown = (
                self.bar_count - self.exit_bar.get(symbol, -999)
            ) < _COOLDOWN_BARS

            vol_scale = 1.0
            weight = SYMBOL_WEIGHTS.get(symbol, 0.33)
            if high_corr and symbol == "SOL":
                weight *= 0.5
            strength_scale = 1.0
            size = (
                equity
                * _BASE_POSITION_PCT
                * weight
                * vol_scale
                * strength_scale
                * dd_scale
            )

            funding_rates = bd.history["funding_rate"].values[-_FUNDING_LOOKBACK:]
            avg_funding = (
                np.mean(funding_rates)
                if len(funding_rates) >= _FUNDING_LOOKBACK
                else 0.0
            )

            current_pos = portfolio.positions.get(symbol, 0.0)
            target = current_pos

            if current_pos == 0:
                if not in_cooldown:
                    funding_mult = 1.0
                    if bullish:
                        if avg_funding < 0:
                            funding_mult = 1.0 + _FUNDING_BOOST
                        target = size * funding_mult
                        self.pyramided[symbol] = False
                    elif bearish:
                        if avg_funding > 0:
                            funding_mult = 1.0 + _FUNDING_BOOST
                        target = -size * funding_mult
                        self.pyramided[symbol] = False
            else:
                if symbol in self.entry_prices and not self.pyramided.get(symbol, True):
                    entry = self.entry_prices[symbol]
                    pnl = (mid - entry) / entry
                    if current_pos < 0:
                        pnl = -pnl
                    if pnl > _PYRAMID_THRESHOLD:
                        if current_pos > 0 and bullish:
                            target = current_pos + size * _PYRAMID_SIZE
                            self.pyramided[symbol] = True
                        elif current_pos < 0 and bearish:
                            target = current_pos - size * _PYRAMID_SIZE
                            self.pyramided[symbol] = True

                atr = self._calc_atr(bd.history, _ATR_LOOKBACK)
                if atr is None:
                    atr = self.atr_at_entry.get(symbol, mid * 0.02)

                if symbol not in self.peak_prices:
                    self.peak_prices[symbol] = mid

                if current_pos > 0:
                    self.peak_prices[symbol] = max(self.peak_prices[symbol], mid)
                    stop = self.peak_prices[symbol] - _ATR_STOP_MULT * atr
                    if mid < stop:
                        target = 0.0
                else:
                    self.peak_prices[symbol] = min(self.peak_prices[symbol], mid)
                    stop = self.peak_prices[symbol] + _ATR_STOP_MULT * atr
                    if mid > stop:
                        target = 0.0

                if symbol in self.entry_prices:
                    entry = self.entry_prices[symbol]
                    pnl = (mid - entry) / entry
                    if current_pos < 0:
                        pnl = -pnl
                    if pnl > _TAKE_PROFIT_PCT:
                        target = 0.0

                if current_pos > 0 and rsi > _RSI_OVERBOUGHT:
                    target = 0.0
                elif current_pos < 0 and rsi < _RSI_OVERSOLD:
                    target = 0.0

                if current_pos > 0 and bearish and not in_cooldown:
                    target = -size
                elif current_pos < 0 and bullish and not in_cooldown:
                    target = size

            if abs(target - current_pos) > 1.0:
                signals.append(Signal(symbol=symbol, target_position=target))
                if target != 0 and current_pos == 0:
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.atr_at_entry[symbol] = (
                        self._calc_atr(bd.history, _ATR_LOOKBACK) or mid * 0.02
                    )
                elif target == 0:
                    self.entry_prices.pop(symbol, None)
                    self.peak_prices.pop(symbol, None)
                    self.atr_at_entry.pop(symbol, None)
                    self.pyramided.pop(symbol, None)
                    self.exit_bar[symbol] = self.bar_count
                elif (target > 0 and current_pos < 0) or (
                    target < 0 and current_pos > 0
                ):
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.atr_at_entry[symbol] = (
                        self._calc_atr(bd.history, _ATR_LOOKBACK) or mid * 0.02
                    )
                    self.pyramided[symbol] = False

        return signals


def _load_params_from_db():
    from dotenv import load_dotenv
    import psycopg2

    INT_PARAMS = {
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
        "VOL_LOOKBACK",
        "ATR_LOOKBACK",
        "COOLDOWN_BARS",
        "MIN_VOTES",
        "CORR_LOOKBACK",
    }

    try:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        load_dotenv(env_path)
        db_url = os.environ.get("SUPABASE_DB_URL", "")
        if not db_url:
            return

        with psycopg2.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol, "
                    + ", ".join(PARAM_COLUMNS)
                    + " FROM param_snapshots WHERE is_active = TRUE AND period = '15m'"
                )
                rows = cur.fetchall()
                col_names = [desc[0] for desc in cur.description]

        if not rows:
            return

        global _SYMBOL_PARAMS
        _SYMBOL_PARAMS = {}
        for row in rows:
            raw = {k.lower(): v for k, v in zip(col_names, row)}
            symbol = raw.get("symbol", "ALL")
            symbol_params = {}
            for col in PARAM_COLUMNS:
                val = raw[col.lower()]
                if col in INT_PARAMS:
                    val = int(val)
                else:
                    val = float(val)
                symbol_params[col] = val
            _SYMBOL_PARAMS[symbol] = symbol_params
        print(f"Loaded active params for: {list(_SYMBOL_PARAMS.keys())}")
    except Exception as e:
        print(f"Warning: Could not load params from DB: {e}")


_load_params_from_db()

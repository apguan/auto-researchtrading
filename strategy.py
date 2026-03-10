"""
Exp3: Build on exp2 (3.292) — add cross-asset lead-lag and dynamic thresholds.

Changes from exp2:
1. BTC momentum as leading indicator for ETH/SOL entries
2. Dynamic momentum threshold scaled by recent volatility
3. Slightly higher base position for more conviction trades
4. Momentum strength scaling (stronger signal = bigger position)
"""

import numpy as np
from prepare import Signal, PortfolioState, BarData

ACTIVE_SYMBOLS = ["BTC", "ETH", "SOL"]
SYMBOL_WEIGHTS = {"BTC": 0.40, "ETH": 0.35, "SOL": 0.25}

# Momentum
SHORT_WINDOW = 12
MED_WINDOW = 24
LONG_WINDOW = 48

# EMA
EMA_FAST = 12
EMA_SLOW = 26

# Funding
FUNDING_LOOKBACK = 24
FUNDING_BOOST = 0.3

# Position sizing
BASE_POSITION_PCT = 0.14
VOL_LOOKBACK = 48
TARGET_VOL = 0.015

# Stops
ATR_LOOKBACK = 24
ATR_STOP_MULT = 3.5
TAKE_PROFIT_PCT = 0.08

# Dynamic threshold
BASE_THRESHOLD = 0.015
THRESHOLD_VOL_SCALE = 1.0  # threshold = BASE * (1 + realized_vol / TARGET_VOL * SCALE)

def ema(values, span):
    alpha = 2.0 / (span + 1)
    result = np.empty_like(values, dtype=float)
    result[0] = values[0]
    for i in range(1, len(values)):
        result[i] = alpha * values[i] + (1 - alpha) * result[i - 1]
    return result

class Strategy:
    def __init__(self):
        self.entry_prices = {}
        self.peak_prices = {}
        self.atr_at_entry = {}
        self.btc_momentum = 0.0  # cached BTC momentum for lead-lag

    def _calc_atr(self, history, lookback):
        if len(history) < lookback + 1:
            return None
        highs = history["high"].values[-lookback:]
        lows = history["low"].values[-lookback:]
        closes = history["close"].values[-(lookback+1):-1]
        tr = np.maximum(highs - lows,
                        np.maximum(np.abs(highs - closes), np.abs(lows - closes)))
        return np.mean(tr)

    def _calc_vol(self, closes, lookback):
        if len(closes) < lookback:
            return TARGET_VOL
        log_rets = np.diff(np.log(closes[-lookback:]))
        return max(np.std(log_rets), 1e-6)

    def on_bar(self, bar_data: dict, portfolio: PortfolioState) -> list:
        signals = []
        equity = portfolio.equity if portfolio.equity > 0 else portfolio.cash

        # First pass: compute BTC momentum for lead-lag
        if "BTC" in bar_data and len(bar_data["BTC"].history) >= LONG_WINDOW + 1:
            btc_closes = bar_data["BTC"].history["close"].values
            self.btc_momentum = (btc_closes[-1] - btc_closes[-MED_WINDOW]) / btc_closes[-MED_WINDOW]

        for symbol in ACTIVE_SYMBOLS:
            if symbol not in bar_data:
                continue
            bd = bar_data[symbol]
            if len(bd.history) < max(LONG_WINDOW, EMA_SLOW) + 1:
                continue

            closes = bd.history["close"].values
            mid = bd.close

            # Realized vol for dynamic threshold
            realized_vol = self._calc_vol(closes, VOL_LOOKBACK)
            vol_ratio = realized_vol / TARGET_VOL
            dyn_threshold = BASE_THRESHOLD * (0.5 + vol_ratio * 0.5)
            dyn_threshold = max(0.008, min(0.03, dyn_threshold))

            # Multi-timeframe momentum
            ret_short = (closes[-1] - closes[-SHORT_WINDOW]) / closes[-SHORT_WINDOW]
            ret_med = (closes[-1] - closes[-MED_WINDOW]) / closes[-MED_WINDOW]
            ret_long = (closes[-1] - closes[-LONG_WINDOW]) / closes[-LONG_WINDOW]

            # EMA crossover
            ema_fast = ema(closes[-(EMA_SLOW+10):], EMA_FAST)
            ema_slow_arr = ema(closes[-(EMA_SLOW+10):], EMA_SLOW)
            ema_bull = ema_fast[-1] > ema_slow_arr[-1]
            ema_bear = ema_fast[-1] < ema_slow_arr[-1]

            # Cross-asset: for ETH/SOL, BTC momentum adds confirmation
            btc_confirm = True
            if symbol != "BTC":
                if ret_short > 0 and self.btc_momentum > 0:
                    btc_confirm = True
                elif ret_short < 0 and self.btc_momentum < 0:
                    btc_confirm = True
                else:
                    btc_confirm = False  # disagrees with BTC

            bullish = (ret_short > dyn_threshold and
                       ret_med > dyn_threshold * 0.8 and
                       ret_long > 0 and ema_bull and btc_confirm)
            bearish = (ret_short < -dyn_threshold and
                       ret_med < -dyn_threshold * 0.8 and
                       ret_long < 0 and ema_bear and btc_confirm)

            # Momentum strength scaling
            mom_strength = abs(ret_short) / dyn_threshold
            strength_scale = min(1.5, max(0.7, mom_strength * 0.5 + 0.5))

            # Vol-adaptive sizing
            vol_scale = min(2.0, max(0.3, TARGET_VOL / realized_vol))
            weight = SYMBOL_WEIGHTS.get(symbol, 0.33)
            size = equity * BASE_POSITION_PCT * weight * vol_scale * strength_scale

            # Funding
            funding_rates = bd.history["funding_rate"].values[-FUNDING_LOOKBACK:]
            avg_funding = np.mean(funding_rates) if len(funding_rates) >= FUNDING_LOOKBACK else 0.0
            funding_mult = 1.0

            current_pos = portfolio.positions.get(symbol, 0.0)
            target = current_pos

            if current_pos == 0:
                if bullish:
                    if avg_funding < 0:
                        funding_mult = 1.0 + FUNDING_BOOST
                    target = size * funding_mult
                elif bearish:
                    if avg_funding > 0:
                        funding_mult = 1.0 + FUNDING_BOOST
                    target = -size * funding_mult
            else:
                atr = self._calc_atr(bd.history, ATR_LOOKBACK)
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

                if current_pos > 0 and bearish:
                    target = -size
                elif current_pos < 0 and bullish:
                    target = size

            if abs(target - current_pos) > 1.0:
                signals.append(Signal(symbol=symbol, target_position=target))
                if target != 0 and current_pos == 0:
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.atr_at_entry[symbol] = self._calc_atr(bd.history, ATR_LOOKBACK) or mid * 0.02
                elif target == 0:
                    self.entry_prices.pop(symbol, None)
                    self.peak_prices.pop(symbol, None)
                    self.atr_at_entry.pop(symbol, None)
                elif (target > 0 and current_pos < 0) or (target < 0 and current_pos > 0):
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.atr_at_entry[symbol] = self._calc_atr(bd.history, ATR_LOOKBACK) or mid * 0.02

        return signals

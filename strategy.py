"""
Multi-timeframe momentum with vol-regime adaptive sizing.

Key ideas:
1. Require momentum agreement across 12h, 24h, 48h windows
2. Scale position size inversely with realized volatility
3. ATR-based trailing stops instead of fixed stops
4. Include SOL with lower weight for diversification
"""

import numpy as np
from prepare import Signal, PortfolioState, BarData

ACTIVE_SYMBOLS = ["BTC", "ETH", "SOL"]
SYMBOL_WEIGHTS = {"BTC": 0.40, "ETH": 0.35, "SOL": 0.25}

# Momentum windows
SHORT_WINDOW = 12
MED_WINDOW = 24
LONG_WINDOW = 48
MOMENTUM_THRESHOLD = 0.015

# Position sizing
BASE_POSITION_PCT = 0.12
VOL_LOOKBACK = 48
TARGET_VOL = 0.015  # target hourly vol

# Stops
ATR_LOOKBACK = 24
ATR_STOP_MULT = 3.0
TAKE_PROFIT_PCT = 0.08

class Strategy:
    def __init__(self):
        self.entry_prices = {}
        self.peak_prices = {}
        self.atr_at_entry = {}

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

        for symbol in ACTIVE_SYMBOLS:
            if symbol not in bar_data:
                continue
            bd = bar_data[symbol]
            if len(bd.history) < LONG_WINDOW + 1:
                continue

            closes = bd.history["close"].values
            mid = bd.close

            # Multi-timeframe momentum
            ret_short = (closes[-1] - closes[-SHORT_WINDOW]) / closes[-SHORT_WINDOW]
            ret_med = (closes[-1] - closes[-MED_WINDOW]) / closes[-MED_WINDOW]
            ret_long = (closes[-1] - closes[-LONG_WINDOW]) / closes[-LONG_WINDOW]

            # Direction: all three must agree
            bullish = (ret_short > MOMENTUM_THRESHOLD and
                       ret_med > MOMENTUM_THRESHOLD * 0.8 and
                       ret_long > 0)
            bearish = (ret_short < -MOMENTUM_THRESHOLD and
                       ret_med < -MOMENTUM_THRESHOLD * 0.8 and
                       ret_long < 0)

            # Vol-adaptive sizing
            realized_vol = self._calc_vol(closes, VOL_LOOKBACK)
            vol_scale = min(2.0, max(0.3, TARGET_VOL / realized_vol))
            weight = SYMBOL_WEIGHTS.get(symbol, 0.33)
            size = equity * BASE_POSITION_PCT * weight * vol_scale

            current_pos = portfolio.positions.get(symbol, 0.0)
            target = current_pos

            # Entry
            if current_pos == 0:
                if bullish:
                    target = size
                elif bearish:
                    target = -size
            else:
                # ATR trailing stop
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

                # Take profit
                if symbol in self.entry_prices:
                    entry = self.entry_prices[symbol]
                    pnl = (mid - entry) / entry
                    if current_pos < 0:
                        pnl = -pnl
                    if pnl > TAKE_PROFIT_PCT:
                        target = 0.0

                # Re-entry: flip direction if signal reverses
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
                    # Flipping direction
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.atr_at_entry[symbol] = self._calc_atr(bd.history, ATR_LOOKBACK) or mid * 0.02

        return signals

"""
Exp2: Multi-TF momentum + EMA crossover + funding carry overlay.

Build on exp1 (2.962) by adding:
1. EMA crossover as additional confirmation (reduces whipsaws)
2. Funding rate carry overlay (bias toward collecting funding)
3. Slightly wider ATR stops for fewer false exits
"""

import numpy as np
from prepare import Signal, PortfolioState, BarData

ACTIVE_SYMBOLS = ["BTC", "ETH", "SOL"]
SYMBOL_WEIGHTS = {"BTC": 0.40, "ETH": 0.35, "SOL": 0.25}

# Momentum
SHORT_WINDOW = 12
MED_WINDOW = 24
LONG_WINDOW = 48
MOMENTUM_THRESHOLD = 0.015

# EMA
EMA_FAST = 12
EMA_SLOW = 26

# Funding
FUNDING_LOOKBACK = 24
FUNDING_BOOST = 0.3  # 30% size boost when collecting funding

# Position sizing
BASE_POSITION_PCT = 0.12
VOL_LOOKBACK = 48
TARGET_VOL = 0.015

# Stops
ATR_LOOKBACK = 24
ATR_STOP_MULT = 3.5  # wider than exp1
TAKE_PROFIT_PCT = 0.08

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
            if len(bd.history) < max(LONG_WINDOW, EMA_SLOW) + 1:
                continue

            closes = bd.history["close"].values
            mid = bd.close

            # Multi-timeframe momentum
            ret_short = (closes[-1] - closes[-SHORT_WINDOW]) / closes[-SHORT_WINDOW]
            ret_med = (closes[-1] - closes[-MED_WINDOW]) / closes[-MED_WINDOW]
            ret_long = (closes[-1] - closes[-LONG_WINDOW]) / closes[-LONG_WINDOW]

            # EMA crossover
            ema_fast = ema(closes[-(EMA_SLOW+10):], EMA_FAST)
            ema_slow = ema(closes[-(EMA_SLOW+10):], EMA_SLOW)
            ema_bull = ema_fast[-1] > ema_slow[-1]
            ema_bear = ema_fast[-1] < ema_slow[-1]

            # Direction: momentum + EMA agreement
            bullish = (ret_short > MOMENTUM_THRESHOLD and
                       ret_med > MOMENTUM_THRESHOLD * 0.8 and
                       ret_long > 0 and ema_bull)
            bearish = (ret_short < -MOMENTUM_THRESHOLD and
                       ret_med < -MOMENTUM_THRESHOLD * 0.8 and
                       ret_long < 0 and ema_bear)

            # Funding carry overlay
            funding_rates = bd.history["funding_rate"].values[-FUNDING_LOOKBACK:]
            avg_funding = np.mean(funding_rates) if len(funding_rates) >= FUNDING_LOOKBACK else 0.0
            
            # Funding boost: increase size when position collects funding
            funding_mult = 1.0
            
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
                    # Long collects when funding negative
                    if avg_funding < 0:
                        funding_mult = 1.0 + FUNDING_BOOST
                    target = size * funding_mult
                elif bearish:
                    # Short collects when funding positive
                    if avg_funding > 0:
                        funding_mult = 1.0 + FUNDING_BOOST
                    target = -size * funding_mult
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

                # Flip on signal reversal
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

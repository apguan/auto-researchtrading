"""
Exp4: Ensemble voting + correlation regime + position pyramiding.

Build on exp3 (3.671):
1. Ensemble: momentum, EMA, RSI must agree (2/3 minimum)
2. Correlation regime: when BTC-ETH corr is high, reduce SOL weight (redundant)
3. Pyramiding: add to winning positions at half-size
4. Tighter take-profit with partial exits
"""

import numpy as np
from prepare import Signal, PortfolioState, BarData

ACTIVE_SYMBOLS = ["BTC", "ETH", "SOL"]
SYMBOL_WEIGHTS = {"BTC": 0.40, "ETH": 0.35, "SOL": 0.25}

SHORT_WINDOW = 12
MED_WINDOW = 24
LONG_WINDOW = 48

EMA_FAST = 12
EMA_SLOW = 26

RSI_PERIOD = 14
RSI_BULL = 55
RSI_BEAR = 45

FUNDING_LOOKBACK = 24
FUNDING_BOOST = 0.3

BASE_POSITION_PCT = 0.14
VOL_LOOKBACK = 48
TARGET_VOL = 0.015

ATR_LOOKBACK = 24
ATR_STOP_MULT = 3.5
TAKE_PROFIT_PCT = 0.06
PYRAMID_THRESHOLD = 0.02  # add to winner after 2% move

CORR_LOOKBACK = 72
HIGH_CORR_THRESHOLD = 0.85

BASE_THRESHOLD = 0.015

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
    deltas = np.diff(closes[-(period+1):])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains) if len(gains) > 0 else 0
    avg_loss = np.mean(losses) if len(losses) > 0 else 1e-10
    rs = avg_gain / max(avg_loss, 1e-10)
    return 100 - 100 / (1 + rs)

class Strategy:
    def __init__(self):
        self.entry_prices = {}
        self.peak_prices = {}
        self.atr_at_entry = {}
        self.btc_momentum = 0.0
        self.pyramided = {}

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

    def _calc_correlation(self, bar_data):
        """BTC-ETH return correlation over CORR_LOOKBACK."""
        if "BTC" not in bar_data or "ETH" not in bar_data:
            return 0.5
        btc_h = bar_data["BTC"].history
        eth_h = bar_data["ETH"].history
        if len(btc_h) < CORR_LOOKBACK or len(eth_h) < CORR_LOOKBACK:
            return 0.5
        btc_rets = np.diff(np.log(btc_h["close"].values[-CORR_LOOKBACK:]))
        eth_rets = np.diff(np.log(eth_h["close"].values[-CORR_LOOKBACK:]))
        if len(btc_rets) < 10:
            return 0.5
        corr = np.corrcoef(btc_rets, eth_rets)[0, 1]
        return corr if not np.isnan(corr) else 0.5

    def on_bar(self, bar_data: dict, portfolio: PortfolioState) -> list:
        signals = []
        equity = portfolio.equity if portfolio.equity > 0 else portfolio.cash

        # BTC momentum for lead-lag
        if "BTC" in bar_data and len(bar_data["BTC"].history) >= LONG_WINDOW + 1:
            btc_closes = bar_data["BTC"].history["close"].values
            self.btc_momentum = (btc_closes[-1] - btc_closes[-MED_WINDOW]) / btc_closes[-MED_WINDOW]

        # Correlation regime
        btc_eth_corr = self._calc_correlation(bar_data)
        high_corr = btc_eth_corr > HIGH_CORR_THRESHOLD

        for symbol in ACTIVE_SYMBOLS:
            if symbol not in bar_data:
                continue
            bd = bar_data[symbol]
            if len(bd.history) < max(LONG_WINDOW, EMA_SLOW) + 1:
                continue

            closes = bd.history["close"].values
            mid = bd.close

            realized_vol = self._calc_vol(closes, VOL_LOOKBACK)
            vol_ratio = realized_vol / TARGET_VOL
            dyn_threshold = BASE_THRESHOLD * (0.5 + vol_ratio * 0.5)
            dyn_threshold = max(0.008, min(0.03, dyn_threshold))

            # Signal 1: Multi-TF momentum
            ret_short = (closes[-1] - closes[-SHORT_WINDOW]) / closes[-SHORT_WINDOW]
            ret_med = (closes[-1] - closes[-MED_WINDOW]) / closes[-MED_WINDOW]
            ret_long = (closes[-1] - closes[-LONG_WINDOW]) / closes[-LONG_WINDOW]
            mom_bull = ret_short > dyn_threshold and ret_med > dyn_threshold * 0.8 and ret_long > 0
            mom_bear = ret_short < -dyn_threshold and ret_med < -dyn_threshold * 0.8 and ret_long < 0

            # Signal 2: EMA crossover
            ema_fast_arr = ema(closes[-(EMA_SLOW+10):], EMA_FAST)
            ema_slow_arr = ema(closes[-(EMA_SLOW+10):], EMA_SLOW)
            ema_bull = ema_fast_arr[-1] > ema_slow_arr[-1]
            ema_bear = ema_fast_arr[-1] < ema_slow_arr[-1]

            # Signal 3: RSI
            rsi = calc_rsi(closes, RSI_PERIOD)
            rsi_bull = rsi > RSI_BULL
            rsi_bear = rsi < RSI_BEAR

            # Ensemble: 2/3 must agree
            bull_votes = sum([mom_bull, ema_bull, rsi_bull])
            bear_votes = sum([mom_bear, ema_bear, rsi_bear])

            # BTC confirmation for alts
            btc_confirm = True
            if symbol != "BTC":
                if bull_votes >= 2 and self.btc_momentum <= 0:
                    btc_confirm = False
                elif bear_votes >= 2 and self.btc_momentum >= 0:
                    btc_confirm = False

            bullish = bull_votes >= 2 and btc_confirm
            bearish = bear_votes >= 2 and btc_confirm

            # Position sizing with correlation adjustment
            vol_scale = min(2.0, max(0.3, TARGET_VOL / realized_vol))
            weight = SYMBOL_WEIGHTS.get(symbol, 0.33)
            if high_corr and symbol == "SOL":
                weight *= 0.5  # reduce SOL when highly correlated
            
            mom_strength = abs(ret_short) / dyn_threshold
            strength_scale = min(1.5, max(0.7, mom_strength * 0.5 + 0.5))
            size = equity * BASE_POSITION_PCT * weight * vol_scale * strength_scale

            # Funding
            funding_rates = bd.history["funding_rate"].values[-FUNDING_LOOKBACK:]
            avg_funding = np.mean(funding_rates) if len(funding_rates) >= FUNDING_LOOKBACK else 0.0

            current_pos = portfolio.positions.get(symbol, 0.0)
            target = current_pos

            if current_pos == 0:
                funding_mult = 1.0
                if bullish:
                    if avg_funding < 0:
                        funding_mult = 1.0 + FUNDING_BOOST
                    target = size * funding_mult
                    self.pyramided[symbol] = False
                elif bearish:
                    if avg_funding > 0:
                        funding_mult = 1.0 + FUNDING_BOOST
                    target = -size * funding_mult
                    self.pyramided[symbol] = False
            else:
                # Pyramiding: add half-size if winning by PYRAMID_THRESHOLD
                if symbol in self.entry_prices and not self.pyramided.get(symbol, True):
                    entry = self.entry_prices[symbol]
                    pnl = (mid - entry) / entry
                    if current_pos < 0:
                        pnl = -pnl
                    if pnl > PYRAMID_THRESHOLD:
                        if current_pos > 0 and bullish:
                            target = current_pos + size * 0.5
                            self.pyramided[symbol] = True
                        elif current_pos < 0 and bearish:
                            target = current_pos - size * 0.5
                            self.pyramided[symbol] = True

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

                # Flip
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
                    self.pyramided.pop(symbol, None)
                elif (target > 0 and current_pos < 0) or (target < 0 and current_pos > 0):
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.atr_at_entry[symbol] = self._calc_atr(bd.history, ATR_LOOKBACK) or mid * 0.02
                    self.pyramided[symbol] = False

        return signals

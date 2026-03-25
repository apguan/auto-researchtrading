import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime

from exchange.order_manager import Signal
from exchange.types import Candle, AccountState
from data.indicators import Indicators
from config import get_settings


@dataclass
class BarData:
    symbol: str
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    funding_rate: float
    history: pd.DataFrame


@dataclass
class PortfolioState:
    cash: float
    positions: Dict[str, float]
    entry_prices: Dict[str, float]
    equity: float = 0.0
    timestamp: int = 0


class EnsembleStrategy:
    def __init__(self):
        self.settings = get_settings()
        self.indicators = Indicators()

        self.entry_prices: Dict[str, float] = {}
        self.peak_prices: Dict[str, float] = {}
        self.atr_at_entry: Dict[str, float] = {}
        self.btc_momentum: float = 0.0
        self.pyramided: Dict[str, bool] = {}
        self.peak_equity: float = 100000.0
        self.exit_bar: Dict[str, int] = {}
        self.bar_count: int = 0

        self.trading_pairs = self.settings.TRADING_PAIRS
        self.symbol_weights = self.settings.SYMBOL_WEIGHTS

    def _candles_to_dataframe(self, candles: List[Candle]) -> pd.DataFrame:
        if not candles:
            return pd.DataFrame()

        data = []
        for c in candles:
            data.append(
                {
                    "timestamp": c.timestamp,
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "volume": c.volume,
                    "funding_rate": c.funding_rate,
                }
            )

        return pd.DataFrame(data)

    def _calc_atr(self, history: pd.DataFrame, lookback: int) -> Optional[float]:
        if len(history) < lookback + 1:
            return None
        highs = history["high"].values[-lookback:]
        lows = history["low"].values[-lookback:]
        closes = history["close"].values[-(lookback + 1) : -1]
        tr = np.maximum(
            highs - lows, np.maximum(np.abs(highs - closes), np.abs(lows - closes))
        )
        return float(np.mean(tr))

    def on_bar(
        self,
        histories: Dict[str, List[Candle]],
        account_state: AccountState,
        current_prices: Dict[str, float],
    ) -> List[Signal]:
        signals = []

        equity = account_state.total_equity
        if equity <= 0:
            equity = account_state.available_balance

        self.bar_count += 1
        self.peak_equity = max(self.peak_equity, equity)

        current_dd = (
            (self.peak_equity - equity) / self.peak_equity
            if self.peak_equity > 0
            else 0
        )
        dd_scale = 1.0
        if current_dd > 0.99:
            dd_scale = max(0.5, 1.0 - (current_dd - 0.99) * 5)

        bar_data: Dict[str, BarData] = {}
        for symbol, candles in histories.items():
            if not candles:
                continue

            history_df = self._candles_to_dataframe(candles)
            last_candle = candles[-1]

            bar_data[symbol] = BarData(
                symbol=symbol,
                timestamp=last_candle.timestamp,
                open=last_candle.open,
                high=last_candle.high,
                low=last_candle.low,
                close=last_candle.close,
                volume=last_candle.volume,
                funding_rate=last_candle.funding_rate,
                history=history_df,
            )

        if (
            "BTC" in bar_data
            and len(bar_data["BTC"].history) >= self.settings.LONG_WINDOW + 1
        ):
            btc_closes = bar_data["BTC"].history["close"].values
            self.btc_momentum = (
                btc_closes[-1] - btc_closes[-self.settings.MED2_WINDOW]
            ) / btc_closes[-self.settings.MED2_WINDOW]

        for symbol in self.trading_pairs:
            if symbol not in bar_data:
                continue

            bd = bar_data[symbol]
            min_len = (
                max(
                    self.settings.LONG_WINDOW,
                    self.settings.EMA_SLOW,
                    self.settings.MACD_SLOW + self.settings.MACD_SIGNAL + 5,
                    self.settings.BB_PERIOD * 3,
                )
                + 1
            )

            if len(bd.history) < min_len:
                continue

            closes = bd.history["close"].values
            mid = bd.close

            realized_vol = self.indicators.calc_volatility(
                closes, self.settings.VOL_LOOKBACK
            )
            vol_ratio = realized_vol / self.settings.TARGET_VOL
            dyn_threshold = self.settings.BASE_THRESHOLD * (0.3 + vol_ratio * 0.7)
            dyn_threshold = max(0.005, min(0.020, dyn_threshold))

            ret_vshort = self.indicators.calc_returns(
                closes, self.settings.SHORT_WINDOW
            )
            ret_short = self.indicators.calc_returns(closes, self.settings.MED_WINDOW)

            mom_bull = ret_short > dyn_threshold
            mom_bear = ret_short < -dyn_threshold
            vshort_bull = ret_vshort > dyn_threshold * 0.7
            vshort_bear = ret_vshort < -dyn_threshold * 0.7

            ema_fast_arr = self.indicators.ema(
                closes[-(self.settings.EMA_SLOW + 10) :], self.settings.EMA_FAST
            )
            ema_slow_arr = self.indicators.ema(
                closes[-(self.settings.EMA_SLOW + 10) :], self.settings.EMA_SLOW
            )
            ema_bull = ema_fast_arr[-1] > ema_slow_arr[-1]
            ema_bear = ema_fast_arr[-1] < ema_slow_arr[-1]

            rsi = self.indicators.calc_rsi(closes, self.settings.RSI_PERIOD)
            rsi_bull = rsi > self.settings.RSI_BULL
            rsi_bear = rsi < self.settings.RSI_BEAR

            macd_hist = self.indicators.calc_macd_histogram(
                closes,
                self.settings.MACD_FAST,
                self.settings.MACD_SLOW,
                self.settings.MACD_SIGNAL,
            )
            macd_bull = macd_hist > 0
            macd_bear = macd_hist < 0

            bb_pctile = self.indicators.calc_bb_width_percentile(
                closes, self.settings.BB_PERIOD
            )
            bb_compressed = bb_pctile < 90

            bull_votes = sum(
                [mom_bull, vshort_bull, ema_bull, rsi_bull, macd_bull, bb_compressed]
            )
            bear_votes = sum(
                [mom_bear, vshort_bear, ema_bear, rsi_bear, macd_bear, bb_compressed]
            )

            btc_confirm = True
            if symbol != "BTC":
                if bull_votes >= self.settings.MIN_VOTES and self.btc_momentum < -0.99:
                    btc_confirm = False
                if bear_votes >= self.settings.MIN_VOTES and self.btc_momentum > 0.99:
                    btc_confirm = False

            bullish = bull_votes >= self.settings.MIN_VOTES and btc_confirm
            bearish = bear_votes >= self.settings.MIN_VOTES and btc_confirm

            in_cooldown = (
                self.bar_count - self.exit_bar.get(symbol, -999)
            ) < self.settings.COOLDOWN_BARS

            weight = self.symbol_weights.get(symbol, 0.33)
            size = equity * self.settings.BASE_POSITION_PCT * weight * dd_scale

            funding_rates = (
                bd.history["funding_rate"].values[-24:]
                if len(bd.history) >= 24
                else [0]
            )
            avg_funding = float(np.mean(funding_rates)) if funding_rates else 0.0

            pos_info = account_state.positions.get(symbol)
            if pos_info:
                current_pos = (
                    pos_info.size if pos_info.side.value == "long" else -pos_info.size
                )
            else:
                current_pos = 0.0

            target = current_pos

            if current_pos == 0:
                if not in_cooldown:
                    funding_mult = 1.0
                    if bullish:
                        if avg_funding < 0:
                            funding_mult = 1.0
                        target = size * funding_mult
                        self.pyramided[symbol] = False
                    elif bearish:
                        if avg_funding > 0:
                            funding_mult = 1.0
                        target = -size * funding_mult
                        self.pyramided[symbol] = False
            else:
                if symbol in self.entry_prices and not self.pyramided.get(symbol, True):
                    entry = self.entry_prices[symbol]
                    pnl = (mid - entry) / entry
                    if current_pos < 0:
                        pnl = -pnl

                    pyramid_threshold = 0.015
                    pyramid_size = 0.0

                    if pnl > pyramid_threshold:
                        if current_pos > 0 and bullish:
                            target = current_pos + size * pyramid_size
                            self.pyramided[symbol] = True
                        elif current_pos < 0 and bearish:
                            target = current_pos - size * pyramid_size
                            self.pyramided[symbol] = True

                atr = self._calc_atr(bd.history, self.settings.ATR_LOOKBACK)
                if atr is None:
                    atr = self.atr_at_entry.get(symbol, mid * 0.02)

                if symbol not in self.peak_prices:
                    self.peak_prices[symbol] = mid

                if current_pos > 0:
                    self.peak_prices[symbol] = max(self.peak_prices[symbol], mid)
                    stop = self.peak_prices[symbol] - self.settings.ATR_STOP_MULT * atr
                    if mid < stop:
                        target = 0.0
                else:
                    self.peak_prices[symbol] = min(self.peak_prices[symbol], mid)
                    stop = self.peak_prices[symbol] + self.settings.ATR_STOP_MULT * atr
                    if mid > stop:
                        target = 0.0

                take_profit_pct = 0.99
                if symbol in self.entry_prices:
                    entry = self.entry_prices[symbol]
                    pnl = (mid - entry) / entry
                    if current_pos < 0:
                        pnl = -pnl
                    if pnl > take_profit_pct:
                        target = 0.0

                if current_pos > 0 and rsi > self.settings.RSI_OVERBOUGHT:
                    target = 0.0
                elif current_pos < 0 and rsi < self.settings.RSI_OVERSOLD:
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
                        self._calc_atr(bd.history, self.settings.ATR_LOOKBACK)
                        or mid * 0.02
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
                        self._calc_atr(bd.history, self.settings.ATR_LOOKBACK)
                        or mid * 0.02
                    )
                    self.pyramided[symbol] = False

        return signals

    def reset(self):
        self.entry_prices = {}
        self.peak_prices = {}
        self.atr_at_entry = {}
        self.btc_momentum = 0.0
        self.pyramided = {}
        self.peak_equity = 100000.0
        self.exit_bar = {}
        self.bar_count = 0

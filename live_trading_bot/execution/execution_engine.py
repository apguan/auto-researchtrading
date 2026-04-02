import time
import sys
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional

# Import strategy module for stop/take-profit constants
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.append(str(_repo_root))
import strategy as _strategy

from ..execution.signal_state import SignalState
from ..exchange.interface import Exchange
from ..exchange.types import Order, OrderSide, OrderStatus, OrderType, AccountState, PositionSide
from ..config.settings import Settings
from ..monitoring.logger import get_logger

logger = get_logger(__name__)


class ExecutionEngine:
    """Unified tick-level execution engine — always on.

    The execution engine runs on every tick and is the sole decision-maker
    for entries and exits. It uses direction set by the hourly bar close
    (stored in signal_state.target_positions) and direction-aware momentum
    sizing for position sizes.

    Priority order (highest first):
    1. Emergency exit — adverse move >= EMERGENCY_EXIT_PCT from entry
    2. ATR trailing stop — price beyond ATR_STOP_MULT × ATR from peak/trough
    3. Take profit — unrealized PnL >= TAKE_PROFIT_PCT from entry
    4. Signal flip to flat — target_direction == 0 and we have a position
    5. Signal reversal — close now, re-enter on next tick (Option A)
    6. Entry — we're flat, direction set, not in cooldown, slippage OK
    """

    def __init__(
        self,
        signal_state: SignalState,
        client: Exchange,
        settings: Settings,
        symbols: List[str],
        risk_controller=None,  # Optional[RiskController]
        position_limiter=None,  # Optional[PositionLimiter]
    ):
        self.signal_state = signal_state
        self.client = client
        self.settings = settings
        self.symbols = set(symbols)
        self._risk_controller = risk_controller
        self._position_limiter = position_limiter

        # Position tracking (coin quantities)
        self._position_sizes: Dict[str, float] = {}  # symbol -> coin qty
        self._entry_prices: Dict[str, float] = {}  # symbol -> fill price
        self._last_executed_direction: Dict[str, int] = {}  # symbol -> +1/-1/0
        self._last_execution_attempt: Dict[str, float] = {}  # symbol -> timestamp

        # Per-symbol close info for PnL calculation in bot (symbol -> (entry_price, direction))
        self._pending_close_info: Dict[str, tuple[float, int]] = {}

        # Symbols currently in the process of closing (prevent sync_positions re-hydrating)
        self._closing: set = set()

        # Equity for position sizing (set by bot on bar close via set_equity)
        self._equity: float = 0.0

        # Pending reversal: symbol -> direction to enter after closing.
        # Implements Option A flip behavior (close now, re-enter on next tick).
        self._pending_reversal: Dict[str, int] = {}

        # Callback for when a position is fully closed (for StopManager coordination)
        self.on_position_closed: Optional[Callable[[str], object]] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_equity(self, equity: float) -> None:
        """Called by bot on bar close with current equity for sizing."""
        self._equity = equity

    def clear_pending_reversal(self, symbol: str) -> None:
        """Clear pending reversal for a symbol when new direction is set on bar close."""
        self._pending_reversal.pop(symbol, None)

    def consume_close_info(self, symbol: str) -> tuple[float, int]:
        """Return and clear the last close info for a symbol. Returns (0, 0) if none."""
        info = self._pending_close_info.pop(symbol, (0.0, 0))
        return info

    async def on_tick(self, symbol: str, price: float) -> Optional[Order]:
        """Process a tick. Returns the order placed, or None.

        Runs on EVERY tick. Execution is always on — no toggle.
        """
        if symbol not in self.symbols:
            return None
        if price <= 0:
            return None

        # Update peak/trough tracking in signal state
        self.signal_state.update_peak_trough(symbol, price)

        # Cooldown check (ms-based, prevents rapid-fire execution attempts)
        now_ms = time.time() * 1000
        last_attempt = self._last_execution_attempt.get(symbol, 0)
        if now_ms - last_attempt < self.settings.EXECUTION_COOLDOWN_MS:
            return None

        current_size = self._position_sizes.get(symbol, 0.0)
        current_direction = self._last_executed_direction.get(symbol, 0)
        target_direction = self.signal_state.get_direction(symbol)
        entry_price = self._entry_prices.get(symbol, 0.0)

        logger.debug(
            "tick",
            extra={
                "symbol": symbol,
                "price": price,
                "current_size": round(current_size, 8),
                "current_dir": current_direction,
                "target_dir": target_direction,
                "entry_price": entry_price,
                "pending_reversal": self._pending_reversal.get(symbol),
                "cooldown": self.signal_state.is_in_cooldown(symbol, self.settings.COOLDOWN_BARS) if current_size == 0 else False,
            },
        )

        # ==============================================================
        # EXIT CHECKS (only if we have a position)
        # ==============================================================

        if current_size > 0 and entry_price > 0:

            # Priority 1: Emergency exit — hard adverse move >= EMERGENCY_EXIT_PCT
            if current_direction > 0:  # long
                adverse_pct = (entry_price - price) / entry_price
            else:  # short
                adverse_pct = (price - entry_price) / entry_price

            if adverse_pct >= self.settings.EMERGENCY_EXIT_PCT:
                logger.critical(
                    "EMERGENCY EXIT triggered",
                    extra={
                        "symbol": symbol,
                        "entry": entry_price,
                        "price": price,
                        "adverse_pct": f"{adverse_pct:.2%}",
                    },
                )
                return await self._close_position(
                    symbol, price, reason="emergency_exit"
                )

            # Priority 2: ATR trailing stop
            atr = self.signal_state.signal_atr.get(symbol, 0.0)
            if atr > 0:
                atr_stop_mult = _strategy.ATR_STOP_MULT

                if current_direction > 0:  # long
                    peak = self.signal_state.peak_prices.get(symbol, entry_price)
                    stop = peak - atr_stop_mult * atr
                    if price < stop:
                        logger.info(
                            "ATR trailing stop (long)",
                            extra={
                                "symbol": symbol,
                                "peak": peak,
                                "stop": stop,
                                "price": price,
                            },
                        )
                        return await self._close_position(
                            symbol, price, reason="atr_stop"
                        )
                elif current_direction < 0:  # short
                    trough = self.signal_state.trough_prices.get(symbol, entry_price)
                    stop = trough + atr_stop_mult * atr
                    if price > stop:
                        logger.info(
                            "ATR trailing stop (short)",
                            extra={
                                "symbol": symbol,
                                "trough": trough,
                                "stop": stop,
                                "price": price,
                            },
                        )
                        return await self._close_position(
                            symbol, price, reason="atr_stop"
                        )

            # Priority 3: Take profit
            if current_direction > 0:  # long
                pnl_pct = (price - entry_price) / entry_price
            else:  # short
                pnl_pct = (entry_price - price) / entry_price

            if pnl_pct >= _strategy.TAKE_PROFIT_PCT:
                logger.info(
                    "Take profit triggered",
                    extra={
                        "symbol": symbol,
                        "entry": entry_price,
                        "price": price,
                        "pnl_pct": f"{pnl_pct:.2%}",
                    },
                )
                return await self._close_position(
                    symbol, price, reason="take_profit"
                )

        # Priority 4: Signal flip to flat — target is flat but we have a position
        if current_size > 0 and target_direction == 0:
            logger.info(
                "Signal flip to flat",
                extra={"symbol": symbol, "from_direction": current_direction},
            )
            return await self._close_position(symbol, price, reason="signal_flat")

        # Priority 5: Signal reversal — close now, re-enter on next tick (Option A)
        if (
            current_size > 0
            and target_direction != 0
            and target_direction != current_direction
        ):
            self._pending_reversal[symbol] = target_direction
            logger.info(
                "Signal reversal — closing, will re-enter on next tick",
                extra={
                    "symbol": symbol,
                    "from": current_direction,
                    "to": target_direction,
                },
            )
            return await self._close_position(symbol, price, reason="signal_reversal")

        # ==============================================================
        # ENTRY CHECKS (only if we're flat)
        # ==============================================================

        if current_size == 0:
            # Check for pending reversal first (Option A: re-enter after close)
            pending_dir = self._pending_reversal.pop(symbol, None)
            if pending_dir is not None:
                effective_direction = pending_dir
            else:
                effective_direction = target_direction

            if effective_direction != 0:
                # Bar-based cooldown — skip for pending reversals (Option A: re-enter on next tick)
                if pending_dir is None and self.signal_state.is_in_cooldown(
                    symbol, self.settings.COOLDOWN_BARS
                ):
                    logger.debug(
                        "Entry skipped — cooldown active",
                        extra={"symbol": symbol},
                    )
                    self._last_execution_attempt[symbol] = now_ms
                    return None

                # Slippage guard
                signal_entry = self.signal_state.signal_entry.get(symbol, 0.0)
                if signal_entry > 0:
                    slippage = abs(price - signal_entry) / signal_entry
                    if slippage > self.settings.ENTRY_SLIPPAGE_PCT:
                        logger.info(
                            "Entry skipped — slippage too high",
                            extra={
                                "symbol": symbol,
                                "signal_entry": signal_entry,
                                "price": price,
                                "slippage_pct": f"{slippage:.2%}",
                                "max_slippage_pct": (
                                    f"{self.settings.ENTRY_SLIPPAGE_PCT:.2%}"
                                ),
                            },
                        )
                        self._last_execution_attempt[symbol] = now_ms
                        return None

                # Direction-aware momentum sizing
                target_usd = self._calculate_position_size(symbol, effective_direction)
                if target_usd <= 0:
                    if pending_dir is not None:
                        logger.warning(
                            "Pending reversal vetoed by momentum — clearing",
                            extra={"symbol": symbol, "direction": effective_direction},
                        )
                    else:
                        logger.debug(
                            "Entry skipped — momentum sizing returned 0",
                            extra={
                                "symbol": symbol,
                                "direction": effective_direction,
                            },
                        )
                    self._last_execution_attempt[symbol] = now_ms
                    return None

                # Risk controller: block new entries when trading is disabled
                if self._risk_controller:
                    if not self._risk_controller.is_trading_enabled():
                        logger.debug(
                            "Entry skipped — trading disabled by risk controls",
                            extra={"symbol": symbol},
                        )
                        self._last_execution_attempt[symbol] = now_ms
                        return None

                # Position limiter: cap entry size by MAX_POSITION_PCT
                if self._position_limiter and self._equity > 0:
                    max_notional = self._equity * self.settings.MAX_POSITION_PCT
                    if target_usd > max_notional:
                        target_usd = max_notional
                        logger.info(
                            "Entry size limited by MAX_POSITION_PCT",
                            extra={"symbol": symbol, "adjusted_usd": round(target_usd, 2)},
                        )

                size_coins = target_usd / price
                if size_coins < 0.00001:
                    return None

                side = OrderSide.BUY if effective_direction > 0 else OrderSide.SELL

                logger.info(
                    "Entry signal executing",
                    extra={
                        "symbol": symbol,
                        "side": side.value,
                        "size_coins": round(size_coins, 8),
                        "price": price,
                        "target_usd": target_usd,
                        "direction": effective_direction,
                    },
                )

                order = await self.client.place_order(
                    symbol=symbol,
                    side=side,
                    size=round(size_coins, 8),
                    order_type=OrderType.MARKET,
                    reduce_only=False,
                )

                if order.status in (
                    OrderStatus.FILLED,
                    OrderStatus.PARTIALLY_FILLED,
                ):
                    self._position_sizes[symbol] = order.filled_size
                    self._entry_prices[symbol] = order.avg_fill_price or price
                    self._last_executed_direction[symbol] = effective_direction

                self._last_execution_attempt[symbol] = now_ms
                return order

        # Hold — no action
        return None

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def _calculate_position_size(self, symbol: str, direction: int) -> float:
        """Direction-aware momentum sizing.

        Longs sized by positive momentum, shorts sized by negative momentum.
        Each side normalized independently so strongest conviction gets most capital.

        Returns target USD notional (always positive).
        """
        momentum = self.signal_state.momentum.get(symbol, 0.0)

        # Only size if direction matches momentum sign
        if direction > 0 and momentum <= 0:
            logger.debug(
                "momentum veto: long signal but non-positive momentum",
                extra={"symbol": symbol, "momentum": momentum, "direction": direction},
            )
            return 0.0  # Bullish signal but negative/zero momentum — skip
        if direction < 0 and momentum >= 0:
            logger.debug(
                "momentum veto: short signal but non-negative momentum",
                extra={"symbol": symbol, "momentum": momentum, "direction": direction},
            )
            return 0.0  # Bearish signal but positive/zero momentum — skip

        equity = self._equity
        if equity <= 0:
            return 0.0

        # Collect all symbols with same-direction momentum for normalization
        same_direction_momenta: Dict[str, float] = {}
        for sym, mom in self.signal_state.momentum.items():
            sym_dir = self.signal_state.get_direction(sym)
            if sym_dir == direction and abs(mom) > 0:
                same_direction_momenta[sym] = mom

        total_momentum = sum(same_direction_momenta.values())
        if total_momentum == 0:
            # Fallback: equal weight across all symbols
            return equity * self.settings.BASE_POSITION_PCT

        weight = momentum / total_momentum
        size = equity * self.settings.BASE_POSITION_PCT * weight * len(self.symbols)
        # len(self.symbols) multiplier because each symbol gets BASE_POSITION_PCT
        # of equity, distributed by momentum so strongest conviction gets most.

        logger.debug(
            "position sized",
            extra={
                "symbol": symbol,
                "momentum": momentum,
                "weight": round(weight, 4),
                "total_momentum": round(total_momentum, 6),
                "same_dir_symbols": list(same_direction_momenta.keys()),
                "equity": round(equity, 2),
                "target_usd": round(size, 2),
            },
        )

        return size

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _close_position(
        self, symbol: str, price: float, reason: str
    ) -> Optional[Order]:
        """Close the current position for a symbol."""
        current_size = self._position_sizes.get(symbol, 0.0)
        if current_size <= 0:
            return None

        current_direction = self._last_executed_direction.get(symbol, 0)
        close_side = OrderSide.SELL if current_direction > 0 else OrderSide.BUY

        logger.info(
            "Closing position",
            extra={
                "symbol": symbol,
                "reason": reason,
                "side": close_side.value,
                "size": current_size,
                "price": price,
            },
        )

        # Capture close info BEFORE the await to prevent race condition:
        # _on_bar can fire during await and overwrite _last_executed_direction.
        self._pending_close_info[symbol] = (
            self._entry_prices.get(symbol, 0.0),
            current_direction,
        )

        self._closing.add(symbol)

        order = await self.client.place_order(
            symbol=symbol,
            side=close_side,
            size=round(current_size, 8),
            order_type=OrderType.MARKET,
            reduce_only=True,
        )

        self._closing.discard(symbol)

        if order.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            self._position_sizes[symbol] = 0.0
            self._entry_prices[symbol] = 0.0
            self._last_executed_direction[symbol] = 0

            # Reset peak/trough for fresh start on re-entry.
            # Do NOT clear direction — it persists from hourly bar close.
            self.signal_state.peak_prices[symbol] = price
            self.signal_state.trough_prices[symbol] = price

            # Record exit for bar-based cooldown tracking
            self.signal_state.record_exit(symbol, self.signal_state.bar_count)

            # Notify bot to cancel exchange-side stop
            if self.on_position_closed:
                try:
                    result = self.on_position_closed(symbol)
                    if isinstance(result, Awaitable):
                        await result
                except Exception as e:
                    logger.error(
                        "on_position_closed callback error",
                        extra={"symbol": symbol, "error": str(e)},
                    )
        else:
            logger.warning(
                "Close order did not fill — clearing internal tracking "
                "(position may have been closed externally)",
                extra={
                    "symbol": symbol,
                    "status": order.status.value,
                    "reason": reason,
                },
            )
            self._position_sizes[symbol] = 0.0
            self._entry_prices[symbol] = 0.0
            self._last_executed_direction[symbol] = 0

        self._last_execution_attempt[symbol] = time.time() * 1000
        return order

    async def sync_positions(
        self, account_state: AccountState, current_prices: Dict[str, float]
    ) -> None:
        """Resync position tracking from exchange state. Called on bar close."""
        for symbol in self.symbols:
            if symbol in self._closing:
                continue
            pos = account_state.positions.get(symbol)
            if pos and pos.size > 0:
                self._position_sizes[symbol] = pos.size
                if pos.entry_price > 0:
                    self._entry_prices[symbol] = pos.entry_price
                # Determine direction from the signal state
                direction = self.signal_state.get_direction(symbol)
                if direction != 0:
                    self._last_executed_direction[symbol] = direction
                elif self._last_executed_direction.get(symbol, 0) == 0:
                    self._last_executed_direction[symbol] = (
                        1 if pos.side == PositionSide.LONG else -1
                    )
            else:
                if self._position_sizes.get(symbol, 0.0) > 0:
                    logger.info(
                        "Position cleared on sync",
                        extra={"symbol": symbol},
                    )
                    self._position_sizes[symbol] = 0.0
                    self._entry_prices[symbol] = 0.0
                    self._last_executed_direction[symbol] = 0

    def reset(self) -> None:
        """Clear all internal state."""
        self._position_sizes.clear()
        self._entry_prices.clear()
        self._last_executed_direction.clear()
        self._last_execution_attempt.clear()
        self._pending_reversal.clear()
        self._pending_close_info.clear()
        self._closing.clear()

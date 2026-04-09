from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional


@dataclass
class SignalState:
    """Persistent signal state updated on bar close, consumed by ExecutionEngine on every tick."""

    # symbol -> target USD notional (positive=long, negative=short, 0=flat)
    target_positions: Dict[str, float] = field(default_factory=dict)

    # symbol -> ATR value at time of signal (used by execution engine for stop pricing)
    signal_atr: Dict[str, float] = field(default_factory=dict)

    # symbol -> entry price at time of signal (used for slippage guard and emergency exit)
    signal_entry: Dict[str, float] = field(default_factory=dict)

    # symbol -> peak price since signal (for trailing stop on longs)
    peak_prices: Dict[str, float] = field(default_factory=dict)

    # symbol -> trough price since signal (for trailing stop on shorts)
    trough_prices: Dict[str, float] = field(default_factory=dict)

    # symbol -> timestamp of last bar that generated the signal
    signal_timestamps: Dict[str, Optional[datetime]] = field(default_factory=dict)

    # symbol -> raw 12h return (set on bar close by bot)
    momentum: Dict[str, float] = field(default_factory=dict)

    # incremented on each bar close, used for cooldown tracking
    bar_count: int = 0

    # symbol -> bar_count when position was closed by execution engine
    last_exit_bar: Dict[str, int] = field(default_factory=dict)

    # symbol -> consecutive flat bar count (for exit conviction)
    flat_count: Dict[str, int] = field(default_factory=dict)

    # symbol -> bar_count when position was opened (for minimum hold period)
    entry_bar: Dict[str, int] = field(default_factory=dict)

    def set_direction(
        self,
        symbol: str,
        direction: int,
        momentum: float,
        atr: float,
        entry_price: float,
        bar_count: int,
    ) -> None:
        """Called by bot on each bar close after strategy runs.

        direction: +1 (long), -1 (short), or 0 (flat/no signal).
        Stores direction placeholder in target_positions; actual sizing done
        by execution engine. Resets peak/trough to entry_price on new signal.
        """
        self.bar_count = bar_count
        self.momentum[symbol] = momentum
        self.signal_atr[symbol] = atr
        self.signal_entry[symbol] = entry_price

        if direction == 0:
            self.target_positions[symbol] = 0.0
            self.flat_count[symbol] = self.flat_count.get(symbol, 0) + 1
            self.clear_signal(symbol)
        else:
            self.target_positions[symbol] = float(direction)
            self.flat_count[symbol] = 0
            self.peak_prices[symbol] = entry_price
            self.trough_prices[symbol] = entry_price

    def record_exit(self, symbol: str, bar_count: int) -> None:
        """Called by execution engine when it closes a position on a tick.

        Records bar_count for cooldown enforcement. Does NOT clear direction —
        the strategy may still want to be in this direction.
        """
        self.last_exit_bar[symbol] = bar_count

    def is_in_cooldown(self, symbol: str, cooldown_bars: int) -> bool:
        """Returns True if fewer than cooldown_bars have elapsed since last exit."""
        return (self.bar_count - self.last_exit_bar.get(symbol, -999)) < cooldown_bars

    def update_signal(
        self,
        symbol: str,
        target_position: float,
        atr: float,
        entry_price: float,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Store a new signal from bar close. Resets peak/trough to entry_price."""
        self.target_positions[symbol] = target_position
        self.signal_atr[symbol] = atr
        self.signal_entry[symbol] = entry_price
        self.signal_timestamps[symbol] = timestamp
        # Reset peak/trough on new signal
        self.peak_prices[symbol] = entry_price
        self.trough_prices[symbol] = entry_price

    def get_target(self, symbol: str) -> float:
        """Get target position for symbol. Returns 0.0 if no signal."""
        return self.target_positions.get(symbol, 0.0)

    def get_direction(self, symbol: str) -> int:
        """Returns +1 (long), -1 (short), or 0 (flat/no signal)."""
        t = self.target_positions.get(symbol, 0.0)
        if t > 0:
            return 1
        elif t < 0:
            return -1
        return 0

    def update_peak_trough(self, symbol: str, price: float) -> None:
        """Update peak/trough tracking for a symbol. Called on every tick.
        Must be O(1) — just max/min comparison.
        Silently ignores symbols with no active signal."""
        if symbol not in self.target_positions:
            return
        if price > self.peak_prices.get(symbol, price):
            self.peak_prices[symbol] = price
        if price < self.trough_prices.get(symbol, price):
            self.trough_prices[symbol] = price

    def clear_signal(self, symbol: str) -> None:
        """Remove all tracking for a symbol. Called when position is fully closed."""
        self.target_positions.pop(symbol, None)
        self.signal_atr.pop(symbol, None)
        self.signal_entry.pop(symbol, None)
        self.peak_prices.pop(symbol, None)
        self.trough_prices.pop(symbol, None)
        self.signal_timestamps.pop(symbol, None)

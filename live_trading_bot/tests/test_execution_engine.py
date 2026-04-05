import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from live_trading_bot.execution.signal_state import SignalState
from live_trading_bot.execution.execution_engine import ExecutionEngine
from live_trading_bot.exchange.types import (
    Order,
    OrderSide,
    OrderType,
    OrderStatus,
)
from live_trading_bot.config.settings import Settings


def _make_filled_order(symbol="BTC", side=OrderSide.BUY, size=0.1, price=50000.0):
    return Order(
        id="test-order-1",
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        size=size,
        price=price,
        status=OrderStatus.FILLED,
        filled_size=size,
        avg_fill_price=price,
    )


@pytest.fixture
def settings():
    return Settings(
        ENTRY_SLIPPAGE_PCT=0.02,
        EXECUTION_COOLDOWN_MS=100,
        EMERGENCY_EXIT_PCT=0.10,
        EXIT_CONVICTION_BARS=1,
        MIN_HOLD_BARS=0,
    )


@pytest.fixture
def signal_state():
    return SignalState()


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.place_order = AsyncMock(return_value=_make_filled_order())
    return client


class TestExecutionEngine:
    @pytest.mark.asyncio
    async def test_entry_on_signal(self, settings, signal_state, mock_client):
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)
        signal_state.update_signal("BTC", 5000.0, 50.0, 50000.0, None)
        signal_state.momentum["BTC"] = 0.05
        order = await engine.on_tick("BTC", 50000.0)
        assert order is not None
        assert mock_client.place_order.called
        assert engine._position_sizes["BTC"] == 0.1

    @pytest.mark.asyncio
    async def test_no_entry_on_stale_signal(self, settings, signal_state, mock_client):
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)
        signal_state.update_signal("BTC", 5000.0, 50.0, 50000.0, None)
        signal_state.momentum["BTC"] = 0.05
        order = await engine.on_tick("BTC", 52000.0)
        assert order is None
        assert not mock_client.place_order.called

    @pytest.mark.asyncio
    async def test_close_on_signal_flip(self, settings, signal_state, mock_client):
        mock_client.place_order = AsyncMock(
            return_value=_make_filled_order(side=OrderSide.SELL, price=51000.0)
        )
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine._position_sizes["BTC"] = 0.1
        engine._entry_prices["BTC"] = 50000.0
        engine._last_executed_direction["BTC"] = 1

        signal_state.set_direction("BTC", 0, 0.0, 50.0, 50000.0, bar_count=1)
        order = await engine.on_tick("BTC", 51000.0)
        assert order is not None
        assert mock_client.place_order.called
        call_args = mock_client.place_order.call_args
        assert call_args.kwargs.get("reduce_only") is True

    @pytest.mark.asyncio
    async def test_trailing_stop_long(self, settings, signal_state, mock_client):
        mock_client.place_order = AsyncMock(
            return_value=_make_filled_order(side=OrderSide.SELL, price=50500.0)
        )
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine._position_sizes["BTC"] = 0.1
        engine._entry_prices["BTC"] = 50000.0
        engine._last_executed_direction["BTC"] = 1
        signal_state.update_signal("BTC", 5000.0, 50.0, 50000.0, None)
        signal_state.update_peak_trough("BTC", 51000.0)

        order = await engine.on_tick("BTC", 50500.0)
        assert order is not None
        assert mock_client.place_order.called

    @pytest.mark.asyncio
    async def test_trailing_stop_short(self, settings, signal_state, mock_client):
        mock_client.place_order = AsyncMock(
            return_value=_make_filled_order(side=OrderSide.BUY, price=49500.0)
        )
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine._position_sizes["BTC"] = 0.1
        engine._entry_prices["BTC"] = 50000.0
        engine._last_executed_direction["BTC"] = -1
        signal_state.update_signal("BTC", -5000.0, 50.0, 50000.0, None)
        signal_state.update_peak_trough("BTC", 49000.0)

        order = await engine.on_tick("BTC", 49500.0)
        assert order is not None
        assert mock_client.place_order.called

    @pytest.mark.asyncio
    async def test_emergency_exit_long(self, settings, signal_state, mock_client):
        mock_client.place_order = AsyncMock(
            return_value=_make_filled_order(side=OrderSide.SELL, price=44500.0)
        )
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine._position_sizes["BTC"] = 0.1
        engine._entry_prices["BTC"] = 50000.0
        engine._last_executed_direction["BTC"] = 1
        signal_state.update_signal("BTC", 5000.0, 50.0, 50000.0, None)

        order = await engine.on_tick("BTC", 44500.0)
        assert order is not None
        assert mock_client.place_order.called

    @pytest.mark.asyncio
    async def test_cooldown_prevents_duplicate(
        self, settings, signal_state, mock_client
    ):
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)
        signal_state.update_signal("BTC", 5000.0, 50.0, 50000.0, None)
        signal_state.momentum["BTC"] = 0.05
        await engine.on_tick("BTC", 50000.0)
        mock_client.place_order.reset_mock()
        order = await engine.on_tick("BTC", 50001.0)
        assert order is None
        assert not mock_client.place_order.called

    @pytest.mark.asyncio
    async def test_unknown_symbol_ignored(self, settings, signal_state, mock_client):
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        order = await engine.on_tick("DOGE", 100.0)
        assert order is None

    @pytest.mark.asyncio
    async def test_zero_price_ignored(self, settings, signal_state, mock_client):
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        order = await engine.on_tick("BTC", 0.0)
        assert order is None

    @pytest.mark.asyncio
    async def test_no_atr_skips_trailing_stop(
        self, settings, signal_state, mock_client
    ):
        mock_client.place_order = AsyncMock(
            return_value=_make_filled_order(side=OrderSide.SELL, price=40000.0)
        )
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine._position_sizes["BTC"] = 0.1
        engine._entry_prices["BTC"] = 50000.0
        engine._last_executed_direction["BTC"] = 1
        signal_state.update_signal("BTC", 5000.0, 0.0, 50000.0, None)
        signal_state.update_peak_trough("BTC", 51000.0)

        order = await engine.on_tick("BTC", 40000.0)
        assert order is not None

    @pytest.mark.asyncio
    async def test_sync_positions_from_exchange(
        self, settings, signal_state, mock_client
    ):
        from live_trading_bot.exchange.types import Position, PositionSide, AccountState

        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        signal_state.update_signal("BTC", 5000.0, 50.0, 50000.0, None)

        pos = Position(
            symbol="BTC",
            side=PositionSide.LONG,
            size=0.2,
            entry_price=49000.0,
            current_price=50000.0,
            unrealized_pnl=200.0,
        )
        account_state = AccountState(
            wallet_address="0xTest",
            total_equity=100000.0,
            available_balance=95000.0,
            margin_used=5000.0,
            unrealized_pnl=200.0,
            positions={"BTC": pos},
        )

        await engine.sync_positions(account_state, {"BTC": 50000.0})
        assert engine._position_sizes["BTC"] == 0.2

    @pytest.mark.asyncio
    async def test_non_filled_close_clears_internal_state(
        self, settings, signal_state, mock_client
    ):
        rejected_order = Order(
            id="test-order-1",
            symbol="BTC",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            size=0.1,
            price=50500.0,
            status=OrderStatus.REJECTED,
            filled_size=0.0,
            avg_fill_price=0.0,
        )
        mock_client.place_order = AsyncMock(return_value=rejected_order)
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine._position_sizes["BTC"] = 0.1
        engine._entry_prices["BTC"] = 50000.0
        engine._last_executed_direction["BTC"] = 1
        signal_state.update_signal("BTC", 5000.0, 50.0, 50000.0, None)
        signal_state.update_peak_trough("BTC", 51000.0)

        order = await engine.on_tick("BTC", 50500.0)
        assert order is not None
        assert order.status == OrderStatus.REJECTED
        assert engine._position_sizes["BTC"] == 0.0
        assert engine._entry_prices["BTC"] == 0.0
        assert engine._last_executed_direction["BTC"] == 0

    def test_calculate_position_size_momentum_weighting(
        self, signal_state, mock_client
    ):
        settings = Settings(BASE_POSITION_PCT=0.088, EXECUTION_COOLDOWN_MS=100)
        engine = ExecutionEngine(
            signal_state,
            mock_client,
            settings,
            ["BTC", "ETH", "SOL", "XRP", "HYPE"],
        )
        engine.set_equity(100000.0)

        signal_state.set_direction("BTC", 1, 0.05, 50.0, 50000.0, 10)
        signal_state.set_direction("ETH", 1, 0.03, 50.0, 3000.0, 10)
        signal_state.set_direction("SOL", 1, 0.02, 50.0, 100.0, 10)

        assert engine._calculate_position_size("BTC", 1) == pytest.approx(22000.0)
        assert engine._calculate_position_size("ETH", 1) == pytest.approx(13200.0)
        assert engine._calculate_position_size("SOL", 1) == pytest.approx(8800.0)

        signal_state.set_direction("SOL", 0, 0.0, 50.0, 100.0, 10)
        signal_state.momentum["ETH"] = -0.05
        assert engine._calculate_position_size("BTC", 1) == pytest.approx(8800.0)

        engine.set_equity(0.0)
        signal_state.momentum["ETH"] = 0.03
        assert engine._calculate_position_size("BTC", 1) == 0.0

    def test_calculate_position_size_direction_veto(
        self, signal_state, mock_client
    ):
        settings = Settings(
            BASE_POSITION_PCT=0.088,
            EXECUTION_COOLDOWN_MS=100,
            MOMENTUM_VETO_THRESHOLD=0.005,
        )
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)

        signal_state.set_direction("BTC", 1, -0.05, 50.0, 50000.0, 10)
        assert engine._calculate_position_size("BTC", 1) == 0.0

        signal_state.set_direction("BTC", -1, 0.05, 50.0, 50000.0, 10)
        assert engine._calculate_position_size("BTC", -1) == 0.0

        signal_state.set_direction("BTC", 1, 0.0, 50.0, 50000.0, 10)
        assert engine._calculate_position_size("BTC", 1) > 0

        signal_state.set_direction("BTC", 1, 0.05, 50.0, 50000.0, 10)
        assert engine._calculate_position_size("BTC", 1) > 0

    @pytest.mark.asyncio
    async def test_atr_trailing_stop_not_triggering(
        self, settings, signal_state, mock_client
    ):
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine._position_sizes["BTC"] = 0.1
        engine._entry_prices["BTC"] = 50000.0
        engine._last_executed_direction["BTC"] = 1

        signal_state.update_signal("BTC", 5000.0, 50.0, 50000.0, None)
        signal_state.peak_prices["BTC"] = 51000.0

        order = await engine.on_tick("BTC", 50750.0)
        assert order is None
        assert not mock_client.place_order.called

    @pytest.mark.asyncio
    async def test_position_limiter_caps_size(self, signal_state, mock_client):
        settings = Settings(
            MAX_POSITION_PCT=0.005,
            BASE_POSITION_PCT=0.088,
            EXECUTION_COOLDOWN_MS=0,
        )
        engine = ExecutionEngine(
            signal_state,
            mock_client,
            settings,
            ["BTC"],
            position_limiter=True,
        )
        engine.set_equity(100000.0)
        signal_state.update_signal("BTC", 5000.0, 50.0, 50000.0, None)
        signal_state.momentum["BTC"] = 0.05

        order = await engine.on_tick("BTC", 50000.0)
        assert order is not None
        call_kwargs = mock_client.place_order.call_args.kwargs
        assert call_kwargs["size"] == pytest.approx(0.01, abs=1e-6)

    @pytest.mark.asyncio
    async def test_risk_controller_blocks_entry(self, signal_state, mock_client):
        mock_rc = MagicMock()
        mock_rc.is_trading_enabled.return_value = False

        settings = Settings(EXECUTION_COOLDOWN_MS=0)
        engine = ExecutionEngine(
            signal_state,
            mock_client,
            settings,
            ["BTC"],
            risk_controller=mock_rc,
        )
        engine.set_equity(100000.0)
        signal_state.update_signal("BTC", 5000.0, 50.0, 50000.0, None)
        signal_state.momentum["BTC"] = 0.05

        order = await engine.on_tick("BTC", 50000.0)
        assert order is None
        assert not mock_client.place_order.called

    @pytest.mark.asyncio
    async def test_multiple_symbols_mixed_directions(
        self, signal_state, mock_client
    ):
        settings = Settings(BASE_POSITION_PCT=0.088, EXECUTION_COOLDOWN_MS=0, EXIT_CONVICTION_BARS=1, MIN_HOLD_BARS=0)
        engine = ExecutionEngine(
            signal_state,
            mock_client,
            settings,
            ["BTC", "ETH", "SOL"],
        )
        engine.set_equity(100000.0)

        signal_state.set_direction("BTC", 1, 0.05, 50.0, 50000.0, 10)
        signal_state.set_direction("ETH", -1, -0.03, 50.0, 3000.0, 10)
        signal_state.set_direction("SOL", 1, 0.02, 50.0, 100.0, 10)

        await engine.on_tick("BTC", 50000.0)
        btc_kwargs = mock_client.place_order.call_args.kwargs
        assert btc_kwargs["size"] == pytest.approx(0.37714286, abs=1e-5)

        mock_client.place_order.reset_mock()

        await engine.on_tick("ETH", 3000.0)
        eth_kwargs = mock_client.place_order.call_args.kwargs
        assert eth_kwargs["size"] == pytest.approx(8.8, abs=1e-6)

        mock_client.place_order.reset_mock()

        await engine.on_tick("SOL", 100.0)
        sol_kwargs = mock_client.place_order.call_args.kwargs
        assert sol_kwargs["size"] == pytest.approx(75.428571, abs=1e-3)

    @pytest.mark.asyncio
    async def test_signal_flat_blocked_by_conviction(self, settings, signal_state, mock_client):
        mock_client.place_order = AsyncMock(
            return_value=_make_filled_order(side=OrderSide.SELL, price=51000.0)
        )
        settings.EXIT_CONVICTION_BARS = 2
        settings.MIN_HOLD_BARS = 0
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine._position_sizes["BTC"] = 0.1
        engine._entry_prices["BTC"] = 50000.0
        engine._last_executed_direction["BTC"] = 1

        signal_state.bar_count = 5
        signal_state.flat_count["BTC"] = 1
        signal_state.update_signal("BTC", 0.0, 50.0, 50000.0, None)

        order = await engine.on_tick("BTC", 51000.0)
        assert order is None

        signal_state.flat_count["BTC"] = 2
        order = await engine.on_tick("BTC", 51000.0)
        assert order is not None

    @pytest.mark.asyncio
    async def test_signal_flat_executes_after_conviction_bars(self, settings, signal_state, mock_client):
        mock_client.place_order = AsyncMock(
            return_value=_make_filled_order(side=OrderSide.SELL, price=51000.0)
        )
        settings.EXIT_CONVICTION_BARS = 2
        settings.MIN_HOLD_BARS = 0
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine._position_sizes["BTC"] = 0.1
        engine._entry_prices["BTC"] = 50000.0
        engine._last_executed_direction["BTC"] = 1

        signal_state.bar_count = 5
        signal_state.flat_count["BTC"] = 2
        signal_state.update_signal("BTC", 0.0, 50.0, 50000.0, None)

        order = await engine.on_tick("BTC", 51000.0)
        assert order is not None
        assert mock_client.place_order.called

    @pytest.mark.asyncio
    async def test_signal_reversal_blocked_by_min_hold(self, signal_state, mock_client):
        mock_client.place_order = AsyncMock(
            return_value=_make_filled_order(side=OrderSide.SELL, size=0.1, price=49000.0)
        )
        settings = Settings(
            EXECUTION_COOLDOWN_MS=0,
            EXIT_CONVICTION_BARS=1,
            MIN_HOLD_BARS=2,
        )
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine._position_sizes["BTC"] = 0.1
        engine._entry_prices["BTC"] = 50000.0
        engine._last_executed_direction["BTC"] = 1

        signal_state.bar_count = 5
        signal_state.entry_bar["BTC"] = 5
        signal_state.update_signal("BTC", -5000.0, 50.0, 49000.0, None)

        order = await engine.on_tick("BTC", 49000.0)
        assert order is None

    @pytest.mark.asyncio
    async def test_signal_reversal_executes_after_hold_period(self, signal_state, mock_client):
        mock_client.place_order = AsyncMock(
            return_value=_make_filled_order(side=OrderSide.SELL, size=0.1, price=49000.0)
        )
        settings = Settings(
            EXECUTION_COOLDOWN_MS=0,
            EXIT_CONVICTION_BARS=1,
            MIN_HOLD_BARS=2,
        )
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine._position_sizes["BTC"] = 0.1
        engine._entry_prices["BTC"] = 50000.0
        engine._last_executed_direction["BTC"] = 1

        signal_state.bar_count = 8
        signal_state.entry_bar["BTC"] = 5
        signal_state.update_signal("BTC", -5000.0, 50.0, 49000.0, None)

        order = await engine.on_tick("BTC", 49000.0)
        assert order is not None
        assert mock_client.place_order.called

    @pytest.mark.asyncio
    async def test_atr_stop_not_blocked_by_min_hold(self, signal_state, mock_client):
        mock_client.place_order = AsyncMock(
            return_value=_make_filled_order(side=OrderSide.SELL, price=49500.0)
        )
        settings = Settings(
            EXECUTION_COOLDOWN_MS=0,
            EMERGENCY_EXIT_PCT=0.10,
            EXIT_CONVICTION_BARS=1,
            MIN_HOLD_BARS=2,
        )
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine._position_sizes["BTC"] = 0.1
        engine._entry_prices["BTC"] = 50000.0
        engine._last_executed_direction["BTC"] = 1

        signal_state.bar_count = 5
        signal_state.entry_bar["BTC"] = 5
        signal_state.update_signal("BTC", 5000.0, 50.0, 50000.0, None)
        signal_state.peak_prices["BTC"] = 51000.0

        order = await engine.on_tick("BTC", 49500.0)
        assert order is not None
        assert mock_client.place_order.called

    @pytest.mark.asyncio
    async def test_emergency_exit_not_blocked_by_min_hold(self, signal_state, mock_client):
        mock_client.place_order = AsyncMock(
            return_value=_make_filled_order(side=OrderSide.SELL, price=44500.0)
        )
        settings = Settings(
            EXECUTION_COOLDOWN_MS=0,
            EMERGENCY_EXIT_PCT=0.10,
            EXIT_CONVICTION_BARS=1,
            MIN_HOLD_BARS=2,
        )
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine._position_sizes["BTC"] = 0.1
        engine._entry_prices["BTC"] = 50000.0
        engine._last_executed_direction["BTC"] = 1

        signal_state.bar_count = 5
        signal_state.entry_bar["BTC"] = 5
        signal_state.update_signal("BTC", 5000.0, 50.0, 50000.0, None)

        order = await engine.on_tick("BTC", 44500.0)
        assert order is not None
        assert mock_client.place_order.called

    @pytest.mark.asyncio
    async def test_pending_reversal_cleared_on_successful_fill(self, signal_state, mock_client):
        settings = Settings(
            BASE_POSITION_PCT=0.088,
            EXECUTION_COOLDOWN_MS=0,
            EXIT_CONVICTION_BARS=1,
            MIN_HOLD_BARS=0,
        )
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)

        signal_state.bar_count = 10
        signal_state.set_direction("BTC", 1, 0.05, 50.0, 50000.0, 10)
        signal_state.momentum["BTC"] = 0.05
        signal_state.signal_entry["BTC"] = 50000.0

        engine._pending_reversal["BTC"] = 1

        order = await engine.on_tick("BTC", 50000.0)
        assert order is not None
        assert mock_client.place_order.called
        assert engine._pending_reversal.get("BTC") is None

    @pytest.mark.asyncio
    async def test_pending_reversal_cleared_on_rejected_entry(self, signal_state, mock_client):
        rejected_order = Order(
            id="test-order-rej",
            symbol="BTC",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            size=0.1,
            price=50000.0,
            status=OrderStatus.REJECTED,
            filled_size=0.0,
            avg_fill_price=0.0,
        )
        mock_client.place_order = AsyncMock(return_value=rejected_order)
        settings = Settings(
            BASE_POSITION_PCT=0.088,
            EXECUTION_COOLDOWN_MS=0,
            EXIT_CONVICTION_BARS=1,
            MIN_HOLD_BARS=0,
        )
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)

        signal_state.bar_count = 10
        signal_state.set_direction("BTC", 1, 0.05, 50.0, 50000.0, 10)
        signal_state.momentum["BTC"] = 0.05
        signal_state.signal_entry["BTC"] = 50000.0

        engine._pending_reversal["BTC"] = 1

        order = await engine.on_tick("BTC", 50000.0)
        assert order is not None
        assert order.status == OrderStatus.REJECTED
        assert engine._pending_reversal.get("BTC") is None

    def test_soft_momentum_threshold_allows_near_zero(self, signal_state, mock_client):
        settings = Settings(
            BASE_POSITION_PCT=0.088,
            EXECUTION_COOLDOWN_MS=100,
            MOMENTUM_VETO_THRESHOLD=0.005,
        )
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)

        signal_state.set_direction("BTC", 1, -0.003, 50.0, 50000.0, 10)
        assert engine._calculate_position_size("BTC", 1) > 0

    def test_soft_momentum_threshold_blocks_strongly_against(self, signal_state, mock_client):
        settings = Settings(
            BASE_POSITION_PCT=0.088,
            EXECUTION_COOLDOWN_MS=100,
            MOMENTUM_VETO_THRESHOLD=0.005,
        )
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)

        signal_state.set_direction("BTC", 1, -0.01, 50.0, 50000.0, 10)
        assert engine._calculate_position_size("BTC", 1) == 0.0

    def test_momentum_zero_not_blocked(self, signal_state, mock_client):
        settings = Settings(
            BASE_POSITION_PCT=0.088,
            EXECUTION_COOLDOWN_MS=100,
            MOMENTUM_VETO_THRESHOLD=0.005,
        )
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)

        signal_state.set_direction("BTC", 1, 0.0, 50.0, 50000.0, 10)
        assert engine._calculate_position_size("BTC", 1) > 0

    def test_reentry_grace_period_skips_momentum_veto(self, signal_state, mock_client):
        settings = Settings(
            BASE_POSITION_PCT=0.088,
            EXECUTION_COOLDOWN_MS=100,
            MOMENTUM_VETO_THRESHOLD=0.005,
            REENTRY_GRACE_BARS=3,
        )
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)

        signal_state.bar_count = 12
        signal_state.last_exit_bar["BTC"] = 10
        signal_state.set_direction("BTC", 1, -0.05, 50.0, 50000.0, 12)

        assert engine._calculate_position_size("BTC", 1, is_pending_reversal=True) > 0

    def test_reentry_grace_period_expires(self, signal_state, mock_client):
        settings = Settings(
            BASE_POSITION_PCT=0.088,
            EXECUTION_COOLDOWN_MS=100,
            MOMENTUM_VETO_THRESHOLD=0.005,
            REENTRY_GRACE_BARS=3,
        )
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)

        signal_state.bar_count = 20
        signal_state.last_exit_bar["BTC"] = 10
        signal_state.set_direction("BTC", 1, -0.05, 50.0, 50000.0, 20)

        assert engine._calculate_position_size("BTC", 1, is_pending_reversal=True) == 0.0

    def test_reentry_grace_period_only_for_reversals(self, signal_state, mock_client):
        settings = Settings(
            BASE_POSITION_PCT=0.088,
            EXECUTION_COOLDOWN_MS=100,
            MOMENTUM_VETO_THRESHOLD=0.005,
            REENTRY_GRACE_BARS=3,
        )
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)

        signal_state.bar_count = 12
        signal_state.last_exit_bar["BTC"] = 10
        signal_state.set_direction("BTC", 1, -0.05, 50.0, 50000.0, 12)

        assert engine._calculate_position_size("BTC", 1, is_pending_reversal=False) == 0.0
    def test_volatility_circuit_breaker(self):
        mock_db = MagicMock()
        with patch("live_trading_bot.risk.risk_controller.get_settings") as mock_gs:
            mock_gs.return_value = Settings(
                VOLATILITY_CIRCUIT_BREAKER_PCT=0.05,
                VOLATILITY_LOOKBACK_MINUTES=10,
            )
            from live_trading_bot.risk.risk_controller import RiskController

            rc = RiskController(mock_db)

        base_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        r1 = rc.check_volatility_circuit_breaker(
            "BTC", 50000.0, base_time
        )
        assert r1.allowed is True

        r2 = rc.check_volatility_circuit_breaker(
            "BTC", 50300.0, base_time + timedelta(minutes=1)
        )
        assert r2.allowed is True

        r3 = rc.check_volatility_circuit_breaker(
            "BTC", 53000.0, base_time + timedelta(minutes=5)
        )
        assert r3.allowed is False
        assert "Volatility circuit breaker" in r3.reason

        r4 = rc.check_volatility_circuit_breaker(
            "BTC", 53000.0, base_time + timedelta(minutes=11)
        )
        assert r4.allowed is True

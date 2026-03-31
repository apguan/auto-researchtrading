import pytest
from unittest.mock import AsyncMock, MagicMock
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
        TICK_EXECUTION_ENABLED=True,
        ENTRY_SLIPPAGE_PCT=0.02,
        EXECUTION_COOLDOWN_MS=100,
        EMERGENCY_EXIT_PCT=0.10,
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
        signal_state.update_signal("BTC", 5000.0, 50.0, 50000.0, None)
        order = await engine.on_tick("BTC", 50000.0)
        assert order is not None
        assert mock_client.place_order.called
        assert engine._position_sizes["BTC"] == 0.1

    @pytest.mark.asyncio
    async def test_no_entry_on_stale_signal(self, settings, signal_state, mock_client):
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        signal_state.update_signal("BTC", 5000.0, 50.0, 50000.0, None)
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

        signal_state.update_signal("BTC", 0.0, 50.0, 50000.0, None)
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
        signal_state.update_signal("BTC", 5000.0, 50.0, 50000.0, None)
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
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        signal_state.update_signal("BTC", 5000.0, 50.0, 50000.0, None)

        mock_pos = MagicMock()
        mock_pos.size = 0.2
        mock_pos.entry_price = 49000.0
        account_state = MagicMock()
        account_state.positions = {"BTC": mock_pos}

        await engine.sync_positions(account_state, {"BTC": 50000.0})
        assert engine._position_sizes["BTC"] == 0.2

    def test_reset(self, settings, signal_state, mock_client):
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine._position_sizes["BTC"] = 0.1
        engine._entry_prices["BTC"] = 50000.0
        engine._last_executed_direction["BTC"] = 1
        engine.reset()
        assert len(engine._position_sizes) == 0
        assert len(engine._entry_prices) == 0
        assert len(engine._last_executed_direction) == 0

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

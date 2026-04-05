"""Behavioral regression tests — each test would have FAILED before the Oracle review fixes.

These test cross-component interactions and edge cases, not individual methods.
If any of these start failing, the corresponding fix regressed.
"""

import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from live_trading_bot.config.settings import Settings
from live_trading_bot.exchange.types import (
    AccountState,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
)
from live_trading_bot.execution.execution_engine import ExecutionEngine
from live_trading_bot.execution.signal_state import SignalState
from live_trading_bot.risk.risk_controller import RiskController


def _make_filled_order(
    symbol="BTC",
    side=OrderSide.BUY,
    size=0.1,
    price=50000.0,
    order_type=OrderType.MARKET,
):
    return Order(
        id="test-order",
        symbol=symbol,
        side=side,
        order_type=order_type,
        size=size,
        price=price,
        status=OrderStatus.FILLED,
        filled_size=size,
        avg_fill_price=price,
    )


@pytest.fixture
def settings():
    return Settings(
        ENTRY_SLIPPAGE_PCT=0.10,
        EXECUTION_COOLDOWN_MS=0,
        EMERGENCY_EXIT_PCT=0.10,
        COOLDOWN_BARS=2,
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


class TestReversalBypassesCooldown:
    """Bug: pending_reversal was popped but cooldown blocked re-entry.
    Reversals should re-enter on the next tick (Option A), ignoring bar cooldown."""

    @pytest.mark.asyncio
    async def test_reversal_re_enters_next_tick(self, settings, signal_state, mock_client):
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)

        signal_state.set_direction("BTC", 1, 0.05, 50.0, 50000.0, bar_count=1)
        entry_order = await engine.on_tick("BTC", 50000.0)
        assert entry_order is not None
        assert engine._last_executed_direction["BTC"] == 1

        time.sleep(0.05)

        mock_client.place_order.reset_mock()

        signal_state.set_direction("BTC", -1, -0.03, 50.0, 49000.0, bar_count=2)
        engine.clear_pending_reversal("BTC")

        close_order = await engine.on_tick("BTC", 49000.0)
        assert close_order is not None
        assert engine._pending_reversal.get("BTC") == -1

        time.sleep(0.05)
        mock_client.place_order.reset_mock()

        reentry_order = await engine.on_tick("BTC", 49000.0)
        assert reentry_order is not None, "Reversal should re-enter on next tick, cooldown must not block"
        call_kwargs = mock_client.place_order.call_args.kwargs
        assert call_kwargs["side"] == OrderSide.SELL
        assert engine._last_executed_direction["BTC"] == -1


class TestDailyLossLimitMidnightReset:
    """Bug: once trading_enabled was set to False, it stayed False forever.
    The midnight reset only reset daily_start_equity, not trading_enabled."""

    @pytest.mark.asyncio
    async def test_midnight_resets_trading_enabled(self):
        mock_db = AsyncMock()
        mock_db.get_daily_pnl = AsyncMock(return_value=0.0)

        with patch(
            "live_trading_bot.risk.risk_controller.get_settings",
            return_value=Settings(DAILY_LOSS_LIMIT_PCT=0.05),
        ):
            rc = RiskController(mock_db)

        rc.trading_enabled = False
        rc.daily_start_equity = 100000.0

        account_state = AccountState(
            wallet_address="0xTest",
            total_equity=100000.0,
            available_balance=100000.0,
            margin_used=0.0,
            unrealized_pnl=0.0,
            positions={},
        )

        midnight = datetime(2026, 4, 2, 0, 0, 0, tzinfo=timezone.utc)
        with patch(
            "live_trading_bot.risk.risk_controller.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = midnight
            result = await rc.check_daily_loss_limit(account_state)

        assert rc.is_trading_enabled(), "trading_enabled must auto-reset at midnight"


class TestConsumeCloseInfoPreventsStalePnL:
    """Bug: after close→re-entry, the entry order read stale _last_close_* state.
    consume_close_info() returns AND clears, so the next order gets (0, 0)."""

    @pytest.mark.asyncio
    async def test_entry_after_close_gets_zero_pnl(self, settings, signal_state, mock_client):
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)

        signal_state.set_direction("BTC", 1, 0.05, 50.0, 50000.0, bar_count=1)

        entry_order = await engine.on_tick("BTC", 50000.0)
        assert entry_order is not None
        assert engine._position_sizes["BTC"] > 0

        time.sleep(0.05)
        mock_client.place_order.reset_mock()
        mock_client.place_order = AsyncMock(
            return_value=_make_filled_order(side=OrderSide.SELL, size=0.1, price=51000.0)
        )

        signal_state.set_direction("BTC", 0, 0.0, 50.0, 51000.0, bar_count=2)
        engine.clear_pending_reversal("BTC")

        close_order = await engine.on_tick("BTC", 51000.0)
        assert close_order is not None

        entry_price, direction = engine.consume_close_info("BTC")
        assert entry_price == 50000.0
        assert direction == 1

        entry_price2, direction2 = engine.consume_close_info("BTC")
        assert entry_price2 == 0.0, "consume_close_info must return 0 after first read"
        assert direction2 == 0


class TestOrphanedPositionDirectionFromSide:
    """Bug: orphaned positions (no signal state) defaulted to SHORT direction=-1.
    Must use pos.side from exchange instead of guessing."""

    @pytest.mark.asyncio
    async def test_long_orphan_inferred_correctly(self, settings, signal_state, mock_client):
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)

        long_position = Position(
            symbol="BTC",
            side=PositionSide.LONG,
            size=0.1,
            entry_price=50000.0,
            current_price=51000.0,
            unrealized_pnl=100.0,
            leverage=2.0,
        )

        account_state = AccountState(
            wallet_address="0xTest",
            total_equity=100000.0,
            available_balance=95000.0,
            margin_used=5000.0,
            unrealized_pnl=100.0,
            positions={"BTC": long_position},
        )

        await engine.sync_positions(account_state, {"BTC": 51000.0})

        assert engine._last_executed_direction["BTC"] == 1, (
            "Long orphaned position must infer direction from pos.side, not default to -1"
        )

    @pytest.mark.asyncio
    async def test_short_orphan_inferred_correctly(self, settings, signal_state, mock_client):
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)

        short_position = Position(
            symbol="BTC",
            side=PositionSide.SHORT,
            size=0.1,
            entry_price=50000.0,
            current_price=49000.0,
            unrealized_pnl=100.0,
            leverage=2.0,
        )

        account_state = AccountState(
            wallet_address="0xTest",
            total_equity=100000.0,
            available_balance=95000.0,
            margin_used=5000.0,
            unrealized_pnl=100.0,
            positions={"BTC": short_position},
        )

        await engine.sync_positions(account_state, {"BTC": 49000.0})

        assert engine._last_executed_direction["BTC"] == -1, (
            "Short orphaned position must infer direction from pos.side"
        )


class TestRaceConditionSafeCloseInfo:
    """Bug: _close_position read _last_executed_direction AFTER await place_order().
    During the await, _on_bar could overwrite direction via sync_positions.
    Fix: capture direction BEFORE the await."""

    @pytest.mark.asyncio
    async def test_close_info_captured_before_await(self, settings, signal_state, mock_client):
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)

        signal_state.set_direction("BTC", 1, 0.05, 50.0, 50000.0, bar_count=1)
        await engine.on_tick("BTC", 50000.0)
        assert engine._last_executed_direction["BTC"] == 1

        time.sleep(0.05)
        mock_client.place_order.reset_mock()
        mock_client.place_order = AsyncMock(
            return_value=_make_filled_order(side=OrderSide.SELL, size=0.1, price=51000.0)
        )

        original_place = mock_client.place_order

        async def place_and_corrupt(**kwargs):
            engine._last_executed_direction["BTC"] = -1  # corrupted by race
            return await original_place(**kwargs)

        mock_client.place_order = place_and_corrupt

        signal_state.set_direction("BTC", 0, 0.0, 50.0, 51000.0, bar_count=2)
        engine.clear_pending_reversal("BTC")

        close_order = await engine.on_tick("BTC", 51000.0)
        assert close_order is not None

        entry_price, direction = engine.consume_close_info("BTC")
        assert direction == 1, "Close info must capture direction BEFORE the await, not after"
        assert entry_price == 50000.0


class TestPerSymbolCloseInfo:
    """Bug: _last_close_* were instance-level scalars. Two symbols closing in
    the same tick would clobber each other's PnL data."""

    @pytest.mark.asyncio
    async def test_two_symbols_close_independently(self, settings, signal_state, mock_client):
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC", "ETH"])
        engine.set_equity(100000.0)

        async def make_entry_order(**kwargs):
            sym = kwargs.get("symbol", "BTC")
            price = 3000.0 if sym == "ETH" else 50000.0
            return _make_filled_order(symbol=sym, price=price)

        mock_client.place_order = AsyncMock(side_effect=make_entry_order)

        signal_state.set_direction("BTC", 1, 0.05, 50.0, 50000.0, bar_count=1)
        signal_state.set_direction("ETH", 1, 0.03, 3.0, 3000.0, bar_count=1)
        signal_state.momentum["ETH"] = 0.03

        await engine.on_tick("BTC", 50000.0)
        await engine.on_tick("ETH", 3000.0)

        time.sleep(0.05)

        async def make_close_order(**kwargs):
            sym = kwargs.get("symbol", "BTC")
            price = 51000.0 if sym == "BTC" else 3100.0
            side = kwargs.get("side", OrderSide.SELL)
            return _make_filled_order(symbol=sym, side=side, price=price)

        mock_client.place_order = AsyncMock(side_effect=make_close_order)

        signal_state.set_direction("BTC", 0, 0.0, 50.0, 51000.0, bar_count=2)
        signal_state.set_direction("ETH", 0, 0.0, 3.0, 3100.0, bar_count=2)
        engine.clear_pending_reversal("BTC")
        engine.clear_pending_reversal("ETH")

        await engine.on_tick("BTC", 51000.0)
        await engine.on_tick("ETH", 3100.0)

        btc_entry, btc_dir = engine.consume_close_info("BTC")
        eth_entry, eth_dir = engine.consume_close_info("ETH")

        assert btc_entry == 50000.0, "BTC close info should not be clobbered by ETH"
        assert btc_dir == 1
        assert eth_entry == 3000.0, "ETH close info should not be clobbered by BTC"
        assert eth_dir == 1

        assert engine.consume_close_info("BTC") == (0.0, 0)
        assert engine.consume_close_info("ETH") == (0.0, 0)


class TestPendingReversalSurvivesMomentumVeto:
    """Pending reversals now survive momentum veto (improvement #1).
    Within grace period, momentum veto is skipped entirely (improvement #3)."""

    @pytest.mark.asyncio
    async def test_reversal_succeeds_within_grace_period(self, settings, signal_state, mock_client):
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)

        signal_state.set_direction("BTC", 1, 0.05, 50.0, 50000.0, bar_count=1)
        await engine.on_tick("BTC", 50000.0)
        assert engine._position_sizes["BTC"] > 0

        time.sleep(0.05)
        mock_client.place_order.reset_mock()
        mock_client.place_order = AsyncMock(
            return_value=_make_filled_order(side=OrderSide.SELL, size=0.1, price=49000.0)
        )

        signal_state.set_direction("BTC", -1, 0.02, 50.0, 49000.0, bar_count=2)
        engine.clear_pending_reversal("BTC")

        close_order = await engine.on_tick("BTC", 49000.0)
        assert close_order is not None
        assert "BTC" in engine._pending_reversal

        time.sleep(0.05)
        mock_client.place_order.reset_mock()

        reentry = await engine.on_tick("BTC", 49000.0)
        assert reentry is not None, "Re-entry should succeed within grace period"
        assert engine._pending_reversal.get("BTC") is None, "Pending reversal cleared on fill"


class TestSyncPositionsSkipsClosingSymbols:
    """Bug: sync_positions could re-hydrate position state from exchange while
    a close order was in-flight, causing duplicate close attempts."""

    @pytest.mark.asyncio
    async def test_sync_skips_symbol_during_close(self, settings, signal_state, mock_client):
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)

        signal_state.set_direction("BTC", 1, 0.05, 50.0, 50000.0, bar_count=1)
        await engine.on_tick("BTC", 50000.0)
        assert engine._position_sizes["BTC"] > 0

        time.sleep(0.05)

        engine._closing.add("BTC")

        long_position = Position(
            symbol="BTC",
            side=PositionSide.LONG,
            size=0.1,
            entry_price=50000.0,
            current_price=51000.0,
            unrealized_pnl=100.0,
            leverage=2.0,
        )
        account_state = AccountState(
            wallet_address="0xTest",
            total_equity=100000.0,
            available_balance=95000.0,
            margin_used=5000.0,
            unrealized_pnl=100.0,
            positions={"BTC": long_position},
        )

        await engine.sync_positions(account_state, {"BTC": 51000.0})

        assert engine._position_sizes["BTC"] == 0.1
        assert engine._entry_prices["BTC"] == 50000.0

        engine._closing.discard("BTC")

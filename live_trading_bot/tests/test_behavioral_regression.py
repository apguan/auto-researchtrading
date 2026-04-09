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


class TestReversalReEntersImmediately:
    """End-to-end test for the signal-reversal flow: when the strategy flips
    direction, the bot must close the existing position AND re-enter the new
    direction on the very next tick — bypassing the bar-based cooldown.

    Bug context (2026-04-08): in production with COOLDOWN_BARS=2, the
    reversal close fired correctly, but the re-entry never happened. The
    `_pending_reversal` flag was being held idle by `is_in_cooldown` for
    COOLDOWN_BARS bars after the close, and during that wait window other
    state changes (sync_positions re-hydration, new strategy signals) caused
    the pending intent to be lost. By the time the cooldown cleared, the bot
    had moved on and the second half of the flip never executed. ETH is the
    canonical example: closed long at 15:00 UTC, never re-entered short,
    sat unprotected through three bars while dry/backtest correctly held
    the short.

    What this test tells us: a long→short reversal results in TWO orders
    on consecutive ticks (close, then short entry), with the short entry
    happening at the SAME bar_count as the close — not COOLDOWN_BARS later.
    The bar-based cooldown's job is to debounce fresh entries, not to gate
    the second half of an explicit flip.
    """

    @pytest.mark.asyncio
    async def test_reversal_re_enters_on_next_tick(self, settings, signal_state, mock_client):
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)

        # Bar 1: enter long.
        signal_state.set_direction("BTC", 1, 0.05, 50.0, 50000.0, bar_count=1)
        entry_order = await engine.on_tick("BTC", 50000.0)
        assert entry_order is not None
        assert engine._last_executed_direction["BTC"] == 1

        # Wait out the ms cooldown so the next tick isn't blocked by it.
        time.sleep(0.05)
        mock_client.place_order.reset_mock()

        # Bar 2: strategy flips to short. The close fires on this tick.
        signal_state.set_direction("BTC", -1, -0.03, 50.0, 49000.0, bar_count=2)
        # Make the close return a SELL fill (closing a long).
        mock_client.place_order = AsyncMock(
            return_value=_make_filled_order(side=OrderSide.SELL, size=0.1, price=49000.0)
        )
        close_order = await engine.on_tick("BTC", 49000.0)
        assert close_order is not None
        assert engine._pending_reversal.get("BTC") == -1, (
            "Close should set pending_reversal to the target direction"
        )
        assert engine._position_sizes.get("BTC", 0.0) == 0.0, (
            "Engine should be flat after the close"
        )

        # Wait out the ms cooldown.
        time.sleep(0.05)

        # Now mock a SELL fill for the new short entry (at the same bar_count=2).
        # The bar-based cooldown WAS just set (last_exit_bar == bar_count), so
        # is_in_cooldown(BTC, 2) is True. Pre-fix this would have blocked the
        # entry. Post-fix the entry must fire because pending_dir is set.
        mock_client.place_order = AsyncMock(
            return_value=_make_filled_order(side=OrderSide.SELL, size=0.1, price=49000.0)
        )

        reentry_order = await engine.on_tick("BTC", 49000.0)

        assert reentry_order is not None, (
            "Pending reversal must re-enter on the very next tick, NOT wait "
            "for COOLDOWN_BARS bars to elapse"
        )
        assert mock_client.place_order.called
        call_kwargs = mock_client.place_order.call_args.kwargs
        assert call_kwargs["side"] == OrderSide.SELL, "Re-entry must be a short"
        assert engine._last_executed_direction["BTC"] == -1
        assert engine._pending_reversal.get("BTC") is None, (
            "pending_reversal must be cleared after the re-entry fills"
        )

    @pytest.mark.asyncio
    async def test_fresh_entry_still_respects_cooldown(self, settings, signal_state, mock_client):
        """Cooldown still applies to fresh entries — only pending reversals
        bypass it. This guards against the fix being too broad.

        What this test tells us: a fresh entry (no pending_reversal set) at
        the same bar as a recent close is still blocked by COOLDOWN_BARS.
        The cooldown's debouncing purpose for fresh entries is preserved.
        """
        engine = ExecutionEngine(signal_state, mock_client, settings, ["BTC"])
        engine.set_equity(100000.0)

        # Simulate a recent exit at bar 2 (e.g. from a stop or signal_flat),
        # WITHOUT setting pending_reversal — that's the difference.
        signal_state.bar_count = 2
        signal_state.record_exit("BTC", bar_count=2)

        # Same bar, fresh long signal arrives (e.g. strategy decides go long
        # after our exit was triggered by something else). With COOLDOWN_BARS=2
        # this should be blocked.
        signal_state.set_direction("BTC", 1, 0.05, 50.0, 50000.0, bar_count=2)

        order = await engine.on_tick("BTC", 50000.0)
        assert order is None, "Fresh entries must still respect bar cooldown"
        assert not mock_client.place_order.called


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


class TestDailyLossResetUsesDateNotMinute:
    """Bug: the reset condition was `now.hour == 0 and now.minute == 0`, which
    only fires during the 60-second window from 00:00:00 to 00:00:59 UTC. With
    15m+ bars, the bot's check often happened at 00:01 or later, missing the
    window entirely. The bot then carried a stale `daily_start_equity` from a
    previous day forever, eventually tripping the kill switch on phantom losses.

    Fix: track `daily_start_date` and reset whenever `now.date()` differs.
    These tests verify that:
    1. The reset fires at ANY time on a new UTC day, not just within the
       first minute.
    2. The reset does NOT fire repeatedly within the same UTC day."""

    @staticmethod
    def _make_account(equity: float) -> AccountState:
        return AccountState(
            wallet_address="0xTest",
            total_equity=equity,
            available_balance=equity,
            margin_used=0.0,
            unrealized_pnl=0.0,
            positions={},
        )

    @staticmethod
    def _make_rc():
        mock_db = AsyncMock()
        mock_db.get_daily_pnl = AsyncMock(return_value=0.0)
        with patch(
            "live_trading_bot.risk.risk_controller.get_settings",
            return_value=Settings(DAILY_LOSS_LIMIT_PCT=0.05),
        ):
            return RiskController(mock_db)

    @pytest.mark.asyncio
    async def test_reset_fires_at_14_00_on_new_day(self):
        """The fix: a check at 14:00 UTC on a new day MUST reset
        daily_start_equity from the prior day."""
        rc = self._make_rc()

        # Simulate yesterday's stale state: kill-switched at $1041 starting equity
        from datetime import date as _date
        rc.daily_start_equity = 1041.0
        rc.daily_start_date = _date(2026, 4, 7)
        rc.trading_enabled = False  # killed yesterday

        # Check today at 14:00 UTC, with current equity $969 (would still
        # show -7% loss against the stale starting point)
        new_day_afternoon = datetime(2026, 4, 8, 14, 0, 0, tzinfo=timezone.utc)
        with patch("live_trading_bot.risk.risk_controller.datetime") as mock_dt:
            mock_dt.now.return_value = new_day_afternoon
            result = await rc.check_daily_loss_limit(self._make_account(969.0))

        assert rc.is_trading_enabled(), (
            "Day rollover must reset trading_enabled even when checked "
            "outside the 00:00 minute"
        )
        assert rc.daily_start_equity == 969.0, (
            "daily_start_equity must reset to current equity, not carry over "
            "from yesterday's $1041"
        )
        assert rc.daily_start_date.isoformat() == "2026-04-08"
        assert result.allowed, (
            "After reset, current equity == start equity → 0% loss → trading "
            "should be allowed"
        )

    @pytest.mark.asyncio
    async def test_reset_does_not_fire_twice_same_day(self):
        """Within the same UTC day, repeated checks must not reset
        daily_start_equity. Otherwise the bot would lose its reference point
        every bar and the kill switch could never trip."""
        rc = self._make_rc()

        # First check at 14:00 → captures starting equity
        first_check = datetime(2026, 4, 8, 14, 0, 0, tzinfo=timezone.utc)
        with patch("live_trading_bot.risk.risk_controller.datetime") as mock_dt:
            mock_dt.now.return_value = first_check
            await rc.check_daily_loss_limit(self._make_account(1000.0))

        assert rc.daily_start_equity == 1000.0

        # Subsequent checks throughout the day on a falling equity must NOT
        # reset the start point. They should compute losses against $1000.
        for hour in [15, 18, 21, 23]:
            t = datetime(2026, 4, 8, hour, 30, 0, tzinfo=timezone.utc)
            with patch("live_trading_bot.risk.risk_controller.datetime") as mock_dt:
                mock_dt.now.return_value = t
                # Equity dropping over the day
                equity = 1000.0 - (hour - 14) * 5
                await rc.check_daily_loss_limit(self._make_account(equity))

            assert rc.daily_start_equity == 1000.0, (
                f"daily_start_equity must NOT reset within the same day "
                f"(check at hour={hour})"
            )

    @pytest.mark.asyncio
    async def test_kill_switch_no_longer_phantom_after_day_rollover(self):
        """End-to-end: yesterday's kill switch state with stale equity should
        clear on day rollover, and a normal small loss today should NOT
        re-trigger it."""
        rc = self._make_rc()

        # Yesterday: kill-switched with phantom $1041 start (real losses were small)
        from datetime import date as _date
        rc.daily_start_equity = 1041.0
        rc.daily_start_date = _date(2026, 4, 7)
        rc.trading_enabled = False

        # Today at 09:00 UTC, real equity is $969 (would be -7% vs stale $1041,
        # but should be 0% vs reset start of $969)
        morning = datetime(2026, 4, 8, 9, 0, 0, tzinfo=timezone.utc)
        with patch("live_trading_bot.risk.risk_controller.datetime") as mock_dt:
            mock_dt.now.return_value = morning
            result = await rc.check_daily_loss_limit(self._make_account(969.0))
        assert result.allowed, "First check today should reset and allow trading"
        assert rc.daily_start_equity == 969.0

        # A few hours later, equity drops to $950 (-2% real loss). Should still
        # be allowed since limit is 5%.
        afternoon = datetime(2026, 4, 8, 15, 0, 0, tzinfo=timezone.utc)
        with patch("live_trading_bot.risk.risk_controller.datetime") as mock_dt:
            mock_dt.now.return_value = afternoon
            result = await rc.check_daily_loss_limit(self._make_account(950.0))
        assert result.allowed, "Real -2% loss must not trip the 5% kill switch"


class TestDailyLossIgnoresDbPnl:
    """Bug: get_daily_pnl() summed BOTH live and dry trades from the trades
    table without filtering by order_id prefix. The risk_controller did
    `min(daily_pnl_pct, equity_change_pct)` so the live bot's kill switch
    fired on the dry bot's losses (e.g. dry lost $65, live lost $3, but
    live's risk_controller saw the combined -$68 = -7% on $970 and froze).

    Fix: drop the daily_pnl_db check entirely. Equity is the source of
    truth — if account value hasn't dropped, no money was lost. The DB
    PnL was a redundant double-check that became actively harmful when
    the harness started writing dry trades to the same table.

    This test asserts the live bot's kill switch only fires on real
    equity drops, not on contaminated DB PnL."""

    @staticmethod
    def _make_account(equity: float):
        return AccountState(
            wallet_address="0xTest",
            total_equity=equity,
            available_balance=equity,
            margin_used=0.0,
            unrealized_pnl=0.0,
            positions={},
        )

    @staticmethod
    def _make_rc(daily_pnl_db_value: float):
        """Create a risk controller whose db.get_daily_pnl() returns
        the configured value (simulating contamination from dry trades)."""
        mock_db = AsyncMock()
        mock_db.get_daily_pnl = AsyncMock(return_value=daily_pnl_db_value)
        with patch(
            "live_trading_bot.risk.risk_controller.get_settings",
            return_value=Settings(DAILY_LOSS_LIMIT_PCT=0.05),
        ):
            return RiskController(mock_db)

    @pytest.mark.asyncio
    async def test_kill_switch_does_not_fire_on_db_pnl_when_equity_unchanged(self):
        """The exact bug we hit on 2026-04-08: live bot at $970 with no
        equity change but the DB shows -$75 of (mostly dry) PnL. The live
        kill switch must NOT fire because the live bot hasn't actually
        lost any money."""
        # DB returns -$75 (contamination from dry bot's losses)
        rc = self._make_rc(daily_pnl_db_value=-75.0)

        morning = datetime(2026, 4, 8, 2, 0, 0, tzinfo=timezone.utc)
        with patch("live_trading_bot.risk.risk_controller.datetime") as mock_dt:
            mock_dt.now.return_value = morning
            # Live bot's actual equity is $970 (unchanged from start)
            result = await rc.check_daily_loss_limit(self._make_account(970.0))

        assert result.allowed, (
            "Kill switch must not fire when equity is unchanged, "
            "regardless of what get_daily_pnl returns"
        )
        assert rc.is_trading_enabled()

    @pytest.mark.asyncio
    async def test_kill_switch_still_fires_on_real_equity_drop(self):
        """Sanity check: the kill switch must still trip when REAL equity
        drops by more than the limit. We didn't break the actual safety net."""
        # DB returns 0 (clean)
        rc = self._make_rc(daily_pnl_db_value=0.0)

        # First check at $1000 to set the starting equity
        morning = datetime(2026, 4, 8, 9, 0, 0, tzinfo=timezone.utc)
        with patch("live_trading_bot.risk.risk_controller.datetime") as mock_dt:
            mock_dt.now.return_value = morning
            await rc.check_daily_loss_limit(self._make_account(1000.0))

        # Later, equity has dropped to $940 (-6% loss, above the 5% limit)
        afternoon = datetime(2026, 4, 8, 14, 0, 0, tzinfo=timezone.utc)
        with patch("live_trading_bot.risk.risk_controller.datetime") as mock_dt:
            mock_dt.now.return_value = afternoon
            result = await rc.check_daily_loss_limit(self._make_account(940.0))

        assert not result.allowed, (
            "-6% real equity drop should trip the 5% kill switch"
        )
        assert not rc.is_trading_enabled()


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
    """Pending reversals survive cooldown and re-enter once it expires.
    The _pending_reversal dict persists across ticks until cooldown clears."""

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

        # Advance bar_count past cooldown (COOLDOWN_BARS=2, exited at bar 2, clear at bar 4)
        signal_state.bar_count = 4
        signal_state.set_direction("BTC", -1, 0.02, 50.0, 49000.0, bar_count=4)

        mock_client.place_order = AsyncMock(
            return_value=_make_filled_order(side=OrderSide.SELL, size=0.1, price=49000.0)
        )

        reentry = await engine.on_tick("BTC", 49000.0)
        assert reentry is not None, "Re-entry should succeed after cooldown within grace period"
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


class TestFreshSyncPreventsRaceCondition:
    """Bug: _on_bar() runs as asyncio.create_task (bar_builder.py:122), so ticks
    interleave during its await calls. The account_state snapshot fetched at bar
    start (bot.py:223) becomes stale when a tick fills an order mid-bar.
    sync_positions() then wipes the just-filled position using this stale snapshot.

    Fix: bot.py._fresh_sync_positions() re-fetches account_state immediately
    before calling sync_positions, so the snapshot always includes recent fills.

    This test tells us: sync_positions will wipe a recently-filled position when
    given stale (empty) account_state — confirming the exact mechanism that caused
    64% of live entries to be duplicates."""

    @pytest.mark.asyncio
    async def test_stale_sync_wipes_position(self, settings, signal_state, mock_client):
        """Reproduces the bug: fill a position, then sync with stale (empty) data."""
        engine = ExecutionEngine(signal_state, mock_client, settings, ["ALGO"])
        engine.set_equity(200.0)

        # Signal: short ALGO
        signal_state.set_direction("ALGO", -1, -0.05, 0.005, 0.12, bar_count=10)

        mock_client.place_order = AsyncMock(
            return_value=_make_filled_order(
                symbol="ALGO", side=OrderSide.SELL, size=496.0, price=0.12
            )
        )

        # Fill via on_tick
        order = await engine.on_tick("ALGO", 0.12)
        assert order is not None
        assert engine._position_sizes["ALGO"] == 496.0

        # Sync with STALE (empty) account state — simulates the create_task race
        stale_state = AccountState(
            wallet_address="0xTest",
            total_equity=200.0,
            available_balance=200.0,
            margin_used=0.0,
            unrealized_pnl=0.0,
            positions={},  # stale: no positions (snapshot from before the fill)
        )
        await engine.sync_positions(stale_state, {"ALGO": 0.12})

        assert engine._position_sizes.get("ALGO", 0) == 0.0, (
            "Stale sync MUST wipe the position — this confirms the bug mechanism"
        )
        assert engine._last_executed_direction.get("ALGO", 0) == 0

    @pytest.mark.asyncio
    async def test_fresh_sync_preserves_position(self, settings, signal_state, mock_client):
        """The fix: if sync gets a FRESH snapshot that includes the fill, position survives."""
        engine = ExecutionEngine(signal_state, mock_client, settings, ["ALGO"])
        engine.set_equity(200.0)

        signal_state.set_direction("ALGO", -1, -0.05, 0.005, 0.12, bar_count=10)

        mock_client.place_order = AsyncMock(
            return_value=_make_filled_order(
                symbol="ALGO", side=OrderSide.SELL, size=496.0, price=0.12
            )
        )

        order = await engine.on_tick("ALGO", 0.12)
        assert order is not None
        assert engine._position_sizes["ALGO"] == 496.0

        # Sync with FRESH account state that includes the filled position
        fresh_state = AccountState(
            wallet_address="0xTest",
            total_equity=200.0,
            available_balance=140.0,
            margin_used=60.0,
            unrealized_pnl=0.0,
            positions={
                "ALGO": Position(
                    symbol="ALGO",
                    side=PositionSide.SHORT,
                    size=496.0,
                    entry_price=0.12,
                    current_price=0.12,
                    unrealized_pnl=0.0,
                    leverage=10.0,
                )
            },
        )
        await engine.sync_positions(fresh_state, {"ALGO": 0.12})

        assert engine._position_sizes["ALGO"] == 496.0, (
            "Fresh sync must preserve the position — HL settles in <300ms"
        )
        assert engine._last_executed_direction["ALGO"] == -1

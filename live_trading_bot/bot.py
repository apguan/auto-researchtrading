#!/usr/bin/env python3
"""
Live Trading Bot for Hyperliquid

Main entry point that orchestrates:
- Data streaming via WebSocket
- Strategy signal generation
- Order execution
- Risk management
- State persistence
- Monitoring and alerts
"""

import argparse
import asyncio
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from strategy_utils import calc_atr

from live_trading_bot.config import get_settings
from live_trading_bot.config.settings import Settings, refresh_db_params
from live_trading_bot.exchange import create_exchange, Exchange
from live_trading_bot.exchange.types import AccountState, Candle, OrderSide, OrderType
from live_trading_bot.data.streamer import DataStreamer
from live_trading_bot.adapter.ensemble import EnsembleStrategy
from live_trading_bot.risk.risk_controller import RiskController
from live_trading_bot.risk.position_limiter import PositionLimiter
from live_trading_bot.storage import create_repository, Repository
from live_trading_bot.storage.models import Trade, SignalRecord
from live_trading_bot.monitoring.logger import setup_logger, get_logger
from live_trading_bot.monitoring.alerts import Alerter
from live_trading_bot.monitoring.metrics import MetricsTracker
from live_trading_bot.monitoring.telegram_bot import TelegramCommandBot
from live_trading_bot.execution.signal_state import SignalState
from live_trading_bot.execution.execution_engine import ExecutionEngine
from live_trading_bot.exchange.stop_manager import StopManager
from live_trading_bot.monitoring.watchdog import Watchdog


logger = get_logger(__name__)


class TradingBot:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()

        self.client: Optional[Exchange] = None
        self.data_streamer: Optional[DataStreamer] = None
        self.strategy: Optional[EnsembleStrategy] = None
        self.risk_controller: Optional[RiskController] = None
        self.position_limiter: Optional[PositionLimiter] = None
        self.db: Optional[Repository] = None
        self.alerter: Optional[Alerter] = None
        self.metrics: Optional[MetricsTracker] = None
        self.signal_state: Optional[SignalState] = None
        self.execution_engine: Optional[ExecutionEngine] = None
        self.stop_manager: Optional[StopManager] = None
        self.watchdog: Optional[Watchdog] = None
        self.command_bot: Optional[TelegramCommandBot] = None

        self._running = False
        self._shutdown_event = asyncio.Event()

        self._current_positions: Dict[str, float] = {}
        self._current_prices: Dict[str, float] = {}
        self._last_summary_time: Optional[datetime] = None
        self._bar_count: int = 0
        self._last_heartbeat: float = 0
        self._last_processed_bar_ts: int = 0

    async def initialize(self):
        log_dir = Path(self.settings.LOG_PATH).parent
        log_dir.mkdir(parents=True, exist_ok=True)

        setup_logger(
            log_level=self.settings.LOG_LEVEL,
            log_path=self.settings.LOG_PATH,
            json_format=True,
        )

        # Log the operational config at INFO level so a deploy is verifiable
        # at a glance from `railway logs`. Critical for catching cases like
        # "I bumped BAR_INTERVAL to 1h but the running container is still on
        # 15m". Never log secrets here (no API keys, DB URLs, private keys).
        logger.info(
            "Initializing trading bot",
            extra={
                "dry_run": self.settings.DRY_RUN,
                "trading_pairs": self.settings.TRADING_PAIRS,
                "bar_interval": self.settings.BAR_INTERVAL,
                "max_leverage": self.settings.MAX_LEVERAGE,
                "max_position_pct": self.settings.MAX_POSITION_PCT,
                "daily_loss_limit_pct": self.settings.DAILY_LOSS_LIMIT_PCT,
                "alert_instance_name": self.settings.ALERT_INSTANCE_NAME or "(none)",
            },
        )

        self.db = create_repository()
        await self.db.connect()

        self.client = create_exchange()

        self.strategy = EnsembleStrategy()

        self.risk_controller = RiskController(self.db)

        self.position_limiter = PositionLimiter()

        self.alerter = Alerter()

        self.metrics = MetricsTracker()

        self.signal_state = SignalState()
        self.execution_engine = ExecutionEngine(
            signal_state=self.signal_state,
            client=self.client,
            settings=self.settings,
            symbols=self.settings.TRADING_PAIRS,
            risk_controller=self.risk_controller,
            position_limiter=self.position_limiter,
        )
        self.stop_manager = StopManager(self.client, self.settings)
        self.watchdog = Watchdog(self.settings, self.client, self.alerter)
        self.execution_engine.on_position_closed = self._on_execution_position_closed

        await self.client.set_leverage_for_symbols(
            self.settings.TRADING_PAIRS, int(self.settings.MAX_LEVERAGE)
        )

        if self.watchdog:
            await self.watchdog.startup_cleanup()

        self.data_streamer = DataStreamer(
            symbols=self.settings.TRADING_PAIRS,
            on_bar_callback=self._on_bar,
            on_tick_callback=self._on_tick,
        )

        account_state = await self.client.get_account_state()

        if account_state.total_equity <= 0:
            raise RuntimeError(
                f"Account equity is ${account_state.total_equity:.2f}. "
                f"Check HYPERLIQUID_MAIN_WALLET env var and wallet funding."
            )

        for sym, pos in account_state.positions.items():
            self._current_positions[sym] = (
                pos.size if pos.side.value == "long" else -pos.size
            )

        self.execution_engine.set_equity(account_state.total_equity)
        await self.execution_engine.sync_positions(
            account_state, self._current_prices
        )

        if account_state.positions:
            logger.warning(
                "Position reconciliation: inherited open positions from previous run",
                extra={
                    "symbols": list(account_state.positions.keys()),
                    "count": len(account_state.positions),
                },
            )

        if self.stop_manager and not self.settings.DRY_RUN:
            await self.stop_manager.load_existing_stops(
                set(self._current_positions.keys())
            )

        self.metrics.update(
            equity=account_state.total_equity,
            cash=account_state.available_balance,
            positions=self._current_positions,
        )

        if self.watchdog:
            await self.watchdog.start()

        self.command_bot = TelegramCommandBot(
            alerter=self.alerter,
            metrics=self.metrics,
            client=self.client,
            db=self.db,
        )
        await self.command_bot.start()

        if self.settings.HYPERLIQUID_VAULT_ADDRESS:
            raw_vault = self.settings.HYPERLIQUID_VAULT_ADDRESS
            if raw_vault.startswith("HL:"):
                raw_vault = raw_vault[3:]
            logger.info("Vault mode enabled", extra={"vault_address": raw_vault})

        logger.info(
            "Bot initialized",
            extra={
                "wallet": self.client.wallet_address,
                "equity": account_state.total_equity,
                "positions": list(self._current_positions.keys()),
            },
        )

        await self.alerter.send_alert(
            f"🤖 <b>Trading Bot Started</b>\n\n"
            f"Mode: {'DRY RUN' if self.settings.DRY_RUN else 'LIVE'}\n"
            f"Equity: ${account_state.total_equity:.2f}\n"
            f"Pairs: {', '.join(self.settings.TRADING_PAIRS)}",
            urgent=True,
        )

    async def _fresh_sync_positions(
        self, account_state: Optional[AccountState] = None
    ) -> None:
        assert self.client is not None
        if account_state is None:
            account_state = await self.client.get_account_state()
        self.execution_engine.set_equity(account_state.total_equity)
        await self.execution_engine.sync_positions(
            account_state, self._current_prices
        )

    def _positions_to_usd(self) -> Dict[str, float]:
        assert self.client is not None
        positions_usd = {}
        for symbol, coin_qty in self._current_positions.items():
            price = self._current_prices.get(symbol, 0)
            if price > 0:
                positions_usd[symbol] = coin_qty * price
            else:
                positions_usd[symbol] = coin_qty
        return positions_usd

    async def _on_bar(self, symbol: str, candle: Candle):
        if not self._running:
            return

        bar_ts = getattr(candle, "timestamp", 0)
        if bar_ts <= self._last_processed_bar_ts:
            return
        self._last_processed_bar_ts = bar_ts

        self._bar_count += 1

        refresh_db_params()

        now = time.time()
        if now - self._last_heartbeat >= 600:
            self._last_heartbeat = now
            logger.info(
                "Heartbeat",
                extra={
                    "bars_total": self._bar_count,
                    "positions": {
                        s: round(v, 4) for s, v in self._current_positions.items()
                    },
                },
            )

        try:
            assert self.client is not None
            assert self.data_streamer is not None
            assert self.strategy is not None
            assert self.db is not None
            assert self.alerter is not None
            assert self.metrics is not None
            assert self.signal_state is not None
            assert self.execution_engine is not None

            account_state = await self.client.get_account_state()

            prices = await self.client.get_all_mid_prices()
            for sym in self.settings.TRADING_PAIRS:
                if sym in prices:
                    self._current_prices[sym] = prices[sym]

            histories = self.data_streamer.get_all_histories()

            for sym in self.settings.TRADING_PAIRS:
                self._current_positions.pop(sym, None)
            for sym, pos in account_state.positions.items():
                self._current_positions[sym] = (
                    pos.size if pos.side.value == "long" else -pos.size
                )

            if self.risk_controller:
                daily_check = await self.risk_controller.check_daily_loss_limit(account_state)
                if not daily_check.allowed:
                    logger.warning(
                        "Bar skipped — daily loss limit",
                        extra={"reason": daily_check.reason},
                    )
                    await self._fresh_sync_positions(account_state)
                    return

            signals = self.strategy.on_bar(
                histories=histories,
                account_state=account_state,
                current_prices=self._current_prices,
            )

            if not signals:
                logger.info("No signals generated")
                await self._fresh_sync_positions(account_state)
                return

            logger.info(
                "Generated signals",
                extra={
                    "triggered_by": symbol,
                    "count": len(signals),
                    "symbols": [s.symbol for s in signals],
                },
            )

            for sig in signals:
                direction = (
                    1 if sig.target_position > 0
                    else -1 if sig.target_position < 0
                    else 0
                )

                # momentum = MED_WINDOW-bar return
                candles = histories.get(sig.symbol, [])
                momentum = 0.0
                if candles and len(candles) > self.settings.MED_WINDOW:
                    closes = [c.close for c in candles]
                    ref = closes[-self.settings.MED_WINDOW]
                    momentum = (closes[-1] - ref) / max(ref, 1e-10)

                atr = self._get_current_atr(sig.symbol)

                entry_price = self._current_prices.get(sig.symbol, 0)

                self.signal_state.set_direction(
                    symbol=sig.symbol,
                    direction=direction,
                    momentum=momentum,
                    atr=atr,
                    entry_price=entry_price,
                    bar_count=self._bar_count,
                )

                logger.debug(
                    "signal processed",
                    extra={
                        "symbol": sig.symbol,
                        "direction": direction,
                        "target_position": sig.target_position,
                        "momentum": round(momentum, 6),
                        "atr": round(atr, 4),
                        "entry_price": entry_price,
                        "bar_count": self._bar_count,
                    },
                )

                self.execution_engine.clear_pending_reversal(sig.symbol)

            positions_usd = self._positions_to_usd()
            for sig in signals:
                await self.db.insert_signal(
                    SignalRecord(
                        id=None,
                        timestamp=datetime.now(timezone.utc),
                        symbol=sig.symbol,
                        signal_type="target_position",
                        target_position=sig.target_position,
                        current_position=positions_usd.get(sig.symbol, 0),
                        executed=False,
                    )
                )

            await self._fresh_sync_positions()

            pos_state = {
                s: {
                    "direction": (
                        "long"
                        if self.execution_engine._last_executed_direction.get(s, 0) > 0
                        else "short"
                        if self.execution_engine._last_executed_direction.get(s, 0) < 0
                        else "flat"
                    ),
                    "coins": round(
                        self.execution_engine._position_sizes.get(s, 0.0), 8
                    ),
                    "entry": round(
                        self.execution_engine._entry_prices.get(s, 0.0), 2
                    ),
                }
                for s in self.settings.TRADING_PAIRS
                if self.execution_engine._position_sizes.get(s, 0.0) > 0
            }
            if pos_state:
                logger.info("Position state after bar", extra={"positions": pos_state})
            else:
                logger.info("No open positions after bar")

            if self.stop_manager:
                atrs = {}
                for sym in self.settings.TRADING_PAIRS:
                    atrs[sym] = self._get_current_atr(sym)
                await self.stop_manager.refresh_stops(account_state.positions, atrs)

            self.metrics.update(
                equity=account_state.total_equity,
                cash=account_state.available_balance,
                positions=self._current_positions,
                unrealized_pnl=account_state.unrealized_pnl,
            )

            await self._check_hourly_summary(account_state)

        except Exception as e:
            logger.error(
                "Error processing bar", extra={"symbol": symbol, "error": str(e)}
            )
            if self.alerter is not None:
                await self.alerter.alert_error(
                    str(e), context=f"Processing bar for {symbol}"
                )

    async def _on_tick(self, symbol: str, price: float):
        if not self._running or not self.execution_engine:
            return
        try:
            self._current_prices[symbol] = price
            order = await self.execution_engine.on_tick(symbol, price)
            if order and order.status.value in ("filled", "partially_filled"):
                await self._record_execution_order(order)
        except Exception as e:
            logger.error(
                "Tick execution error", extra={"symbol": symbol, "error": str(e)}
            )

    async def _record_execution_order(self, order):
        assert self.db is not None
        assert self.metrics is not None
        assert self.alerter is not None

        pnl = None
        if self.execution_engine:
            entry, direction = self.execution_engine.consume_close_info(order.symbol)
            if entry > 0 and direction != 0:
                if direction > 0 and order.side.value == "sell":  # closing long
                    pnl = (order.avg_fill_price - entry) * order.filled_size
                elif direction < 0 and order.side.value == "buy":  # closing short
                    pnl = (entry - order.avg_fill_price) * order.filled_size

        await self.db.insert_trade(
            Trade(
                id=None,
                timestamp=datetime.now(timezone.utc),
                symbol=order.symbol,
                side=order.side.value,
                size=order.filled_size,
                price=order.avg_fill_price,
                # Fee = notional * taker rate. filled_size is in coins, so we
                # must multiply by price to get USD notional. The previous
                # formula (size * 0.0005) gave near-zero fees for low-coin-count
                # symbols like BTC/ETH. Matches DryExchange.TAKER_FEE_BPS.
                fee=order.filled_size * order.avg_fill_price * 0.0005,
                pnl=pnl,
                order_id=order.id,
                dry_run=self.settings.DRY_RUN,
                snapshot_id=self.settings.active_snapshot_id,
            )
        )

        self.metrics.record_trade(
            symbol=order.symbol,
            side=order.side.value,
            size=order.filled_size,
            price=order.avg_fill_price,
            pnl=pnl,
        )

        await self.alerter.alert_trade(
            symbol=order.symbol,
            side=order.side.value,
            size=order.filled_size,
            price=order.avg_fill_price,
            pnl=pnl,
        )

    async def _on_execution_position_closed(self, symbol: str):
        if self.stop_manager:
            await self.stop_manager.cancel_stop(symbol)

    def _get_current_atr(self, symbol: str) -> float:
        atr = self.signal_state.signal_atr.get(symbol, 0.0)
        if atr > 0:
            return atr

        if self.strategy:
            atr = self.strategy.get_atr_at_entry(symbol)
            if atr > 0:
                return atr

        try:
            histories = self.data_streamer.get_all_histories()
            candles = histories.get(symbol, [])
            if len(candles) >= 20:
                df = pd.DataFrame([{
                    "high": c.high, "low": c.low, "close": c.close,
                } for c in candles])
                atr = calc_atr(df, 14)
                if atr and atr > 0:
                    return atr
        except Exception:
            pass

        price = self._current_prices.get(symbol, 0.0)
        if price > 0:
            return price * 0.02

        return 0.0

    async def _check_hourly_summary(self, account_state: AccountState):
        assert self.metrics is not None
        assert self.alerter is not None
        now = datetime.now(timezone.utc)

        if self._last_summary_time:
            hours_since = (now - self._last_summary_time).total_seconds() / 3600
            if hours_since < self.settings.ALERT_INTERVAL_HOURS:
                return

        self._last_summary_time = now

        daily_pnl = account_state.total_equity - self.metrics.daily_start_equity
        trade_count = self.metrics.get_trade_count_today()

        await self.alerter.send_hourly_summary(
            equity=account_state.total_equity,
            positions={
                s: {"size": p, "notional": abs(p) * self._current_prices.get(s, 0)}
                for s, p in self._current_positions.items()
            },
            daily_pnl=daily_pnl,
            trade_count=trade_count,
        )

    async def run(self):
        self._running = True
        assert self.data_streamer is not None
        assert self.alerter is not None

        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGINT, self._signal_handler)
        loop.add_signal_handler(signal.SIGTERM, self._signal_handler)

        logger.info("Starting trading bot")

        try:
            await self.data_streamer.start(client=self.client)
        except asyncio.CancelledError:
            logger.info("Bot cancelled")
        except Exception as e:
            logger.critical("Bot crashed", extra={"error": str(e)})
            await self.alerter.alert_error(f"Bot crashed: {str(e)}")
            raise
        finally:
            await self.shutdown()

    def _signal_handler(self):
        logger.info("Shutdown signal received")
        self._running = False
        self._shutdown_event.set()

    async def shutdown(self):
        logger.info("Shutting down trading bot")

        self._running = False

        if self.watchdog:
            await self.watchdog.stop()

        if self.command_bot:
            await self.command_bot.stop()

        # Stops are NOT cancelled here — they are reduce-only safety nets that
        # protect positions during restarts.  On next startup,
        # load_existing_stops() will hydrate StopManager from the exchange.

        if self.data_streamer:
            await self.data_streamer.stop()

        if self.client:
            await self.client.close()

        if self.db:
            await self.db.close()

        if self.alerter:
            await self.alerter.send_alert("🛑 <b>Trading Bot Stopped</b>", urgent=True)
            await self.alerter.close()

        logger.info("Shutdown complete")


async def main():
    parser = argparse.ArgumentParser(description="Live Trading Bot")
    parser.add_argument(
        "--close-all",
        action="store_true",
        help="Close all positions, cancel all orders (including stops), then exit",
    )
    args = parser.parse_args()

    bot = TradingBot()

    try:
        await bot.initialize()

        if args.close_all:
            logger.info("--close-all: closing all positions and cancelling all orders")
            assert bot.client is not None
            await bot.client.cancel_all_orders()
            account_state = await bot.client.get_account_state()

            for sym, pos in account_state.positions.items():
                side = OrderSide.SELL if pos.side.value == "long" else OrderSide.BUY
                await bot.client.place_order(
                    sym, side, pos.size, OrderType.MARKET, reduce_only=True
                )
                logger.info(f"Closed {sym}: {pos.size} {pos.side.value}")
            await bot.shutdown()
            return

        await bot.run()
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

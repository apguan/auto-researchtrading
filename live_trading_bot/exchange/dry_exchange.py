import asyncio
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from eth_account import Account
from hyperliquid.info import Info

from .types import (
    AccountState,
    Candle,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
)
from .dry_run_ledger import DryRunLedger
from .hyperliquid import fetch_candles_paginated
from ..config import get_settings
from ..monitoring.logger import get_logger

logger = get_logger(__name__)


class DryExchange:
    def __init__(self, private_key: str):
        self.settings = get_settings()
        self.account = Account.from_key(private_key)
        self.wallet_address = self.account.address
        self.query_address = (
            self.settings.HYPERLIQUID_MAIN_WALLET or self.wallet_address
        )

        self._info = Info(
            base_url=self.settings.HYPERLIQUID_API_URL, skip_ws=True
        )
        self._nonce_counter = int(time.time() * 1000)

        self.ledger = DryRunLedger(
            path=self.settings.DRY_RUN_STATE_PATH,
            initial_equity=self.settings.DRY_RUN_INITIAL_CAPITAL,
        )
        self.ledger.load()

    def _get_nonce(self) -> int:
        self._nonce_counter += 1
        return self._nonce_counter

    async def get_account_state(self) -> AccountState:
        positions: Dict[str, Position] = {}
        unrealized_pnl = 0.0
        total_margin = 0.0

        if self.ledger.positions:
            prices = await self.get_all_mid_prices()
            for symbol, sim in self.ledger.positions.items():
                current_price = prices.get(symbol, sim.entry_price)
                if sim.is_long:
                    upnl = (current_price - sim.entry_price) * sim.size
                else:
                    upnl = (sim.entry_price - current_price) * sim.size
                unrealized_pnl += upnl

                margin = sim.size * current_price / self.settings.MAX_LEVERAGE
                total_margin += margin

                positions[symbol] = Position(
                    symbol=symbol,
                    side=PositionSide.LONG if sim.is_long else PositionSide.SHORT,
                    size=sim.size,
                    entry_price=sim.entry_price,
                    current_price=current_price,
                    unrealized_pnl=upnl,
                    leverage=self.settings.MAX_LEVERAGE,
                    margin_used=margin,
                    liquidation_price=None,
                )

        total_equity = self.ledger.initial_equity + self.ledger.realized_pnl + unrealized_pnl

        return AccountState(
            wallet_address=self.query_address,
            total_equity=total_equity,
            available_balance=total_equity - total_margin,
            margin_used=total_margin,
            unrealized_pnl=unrealized_pnl,
            positions=positions,
        )

    async def get_mid_price(self, symbol: str) -> float:
        mids = await asyncio.to_thread(self._info.all_mids)
        return float(mids.get(symbol, "0"))

    async def get_all_mid_prices(self) -> Dict[str, float]:
        mids = await asyncio.to_thread(self._info.all_mids)
        return {symbol: float(px) for symbol, px in mids.items()}

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        pass

    async def set_leverage_for_symbols(
        self, symbols: List[str], leverage: int
    ) -> None:
        pass

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        size: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        reduce_only: bool = False,
    ) -> Order:
        fill_price = price or await self.get_mid_price(symbol)
        applied = self._apply_fill(symbol, side, size, fill_price, reduce_only)
        self.ledger.save()

        if not applied:
            return Order(
                id=f"dry-{self._get_nonce()}",
                symbol=symbol,
                side=side,
                order_type=order_type,
                size=size,
                price=fill_price,
                status=OrderStatus.REJECTED,
                filled_size=0.0,
                avg_fill_price=fill_price,
            )

        return Order(
            id=f"dry-{self._get_nonce()}",
            symbol=symbol,
            side=side,
            order_type=order_type,
            size=size,
            price=fill_price,
            status=OrderStatus.FILLED,
            filled_size=size,
            avg_fill_price=fill_price,
        )

    async def place_trigger_order(
        self,
        symbol: str,
        side: OrderSide,
        size: float,
        trigger_price: float,
        is_market: bool = True,
        tpsl: str = "sl",
    ) -> Order:
        return Order(
            id=f"dry-trigger-{self._get_nonce()}",
            symbol=symbol,
            side=side,
            order_type=OrderType.TRIGGER,
            size=size,
            price=trigger_price,
            status=OrderStatus.PENDING,
            filled_size=0.0,
            avg_fill_price=trigger_price,
        )

    def _apply_fill(
        self,
        symbol: str,
        side: OrderSide,
        size: float,
        fill_price: float,
        reduce_only: bool,
    ) -> bool:
        pos = self.ledger.positions.get(symbol)
        is_buy = side == OrderSide.BUY
        now = datetime.now(timezone.utc).isoformat()

        if pos is None:
            if reduce_only:
                return False
            self.ledger.open_position(symbol, is_buy, size, fill_price)
            self.ledger.record_transaction({
                "timestamp": now,
                "symbol": symbol,
                "action": "open_long" if is_buy else "open_short",
                "side": "buy" if is_buy else "sell",
                "size": size,
                "price": fill_price,
                "pnl": 0.0,
                "realized_pnl_cumulative": self.ledger.realized_pnl,
            })
            return True

        is_closing = (pos.is_long and not is_buy) or (
            not pos.is_long and is_buy
        )

        if is_closing:
            close_size = min(size, pos.size)
            if pos.is_long:
                pnl = (fill_price - pos.entry_price) * close_size
            else:
                pnl = (pos.entry_price - fill_price) * close_size
            self.ledger.add_realized_pnl(pnl)

            remaining = pos.size - close_size
            if remaining < 1e-10:
                self.ledger.close_position(symbol)
                self.ledger.record_transaction({
                    "timestamp": now,
                    "symbol": symbol,
                    "action": "close",
                    "side": "buy" if is_buy else "sell",
                    "size": close_size,
                    "price": fill_price,
                    "pnl": round(pnl, 10),
                    "realized_pnl_cumulative": round(self.ledger.realized_pnl, 10),
                })
            else:
                self.ledger.update_position(symbol, remaining, pos.entry_price)
                self.ledger.record_transaction({
                    "timestamp": now,
                    "symbol": symbol,
                    "action": "partial_close",
                    "side": "buy" if is_buy else "sell",
                    "size": close_size,
                    "price": fill_price,
                    "pnl": round(pnl, 10),
                    "realized_pnl_cumulative": round(self.ledger.realized_pnl, 10),
                })

            leftover = size - close_size
            if leftover > 1e-10 and not reduce_only:
                self.ledger.open_position(symbol, is_buy, leftover, fill_price)
                self.ledger.record_transaction({
                    "timestamp": now,
                    "symbol": symbol,
                    "action": "open_long" if is_buy else "open_short",
                    "side": "buy" if is_buy else "sell",
                    "size": leftover,
                    "price": fill_price,
                    "pnl": 0.0,
                    "realized_pnl_cumulative": round(self.ledger.realized_pnl, 10),
                })
        else:
            total_size = pos.size + size
            avg_price = (
                pos.entry_price * pos.size + fill_price * size
            ) / total_size
            self.ledger.update_position(symbol, total_size, avg_price)
            self.ledger.record_transaction({
                "timestamp": now,
                "symbol": symbol,
                "action": "add_to_position",
                "side": "buy" if is_buy else "sell",
                "size": size,
                "price": fill_price,
                "pnl": 0.0,
                "realized_pnl_cumulative": self.ledger.realized_pnl,
            })

        return True

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        return True

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> bool:
        return True

    async def get_open_orders(
        self, symbol: Optional[str] = None
    ) -> List[Order]:
        return []

    async def get_user_fills(self, start_time: int, end_time: int) -> list[dict]:
        return []

    async def get_funding_history(self, start_time: int, end_time: int | None = None) -> list[dict]:
        return []

    async def get_funding_rate(self, symbol: str) -> float:
        result = await asyncio.to_thread(self._info.meta_and_asset_ctxs)
        if isinstance(result, list) and len(result) > 1:
            meta = result[0]
            asset_ctxs = result[1]
            for i, coin_info in enumerate(meta.get("universe", [])):
                if coin_info.get("name") == symbol:
                    if i < len(asset_ctxs):
                        return float(asset_ctxs[i].get("funding", 0))
        return 0.0

    async def get_recent_candles(
        self,
        symbol: str,
        interval: str = "1h",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 500,
    ) -> List[Candle]:
        candles = await fetch_candles_paginated(
            self._info, symbol, interval, start_time, end_time, limit
        )
        if candles:
            try:
                current_funding = await self.get_funding_rate(symbol)
                candles[-1] = Candle(
                    symbol=candles[-1].symbol,
                    timestamp=candles[-1].timestamp,
                    open=candles[-1].open,
                    high=candles[-1].high,
                    low=candles[-1].low,
                    close=candles[-1].close,
                    volume=candles[-1].volume,
                    funding_rate=current_funding,
                )
            except Exception as e:
                logger.warning(
                    "Failed to fetch funding rate for candle",
                    extra={"symbol": symbol, "error": str(e)},
                )
        return candles

    async def close(self) -> None:
        pass

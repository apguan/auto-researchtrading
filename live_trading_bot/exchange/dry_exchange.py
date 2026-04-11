import asyncio
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from eth_account import Account
from hyperliquid.info import Info

from .hyperliquid import _parse_all_funding_rates, _attach_funding_to_last_candle

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
        raw_vault = self.settings.HYPERLIQUID_VAULT_ADDRESS or None
        if raw_vault and raw_vault.startswith("HL:"):
            raw_vault = raw_vault[3:]
        self.vault_address = raw_vault
        self.query_address = (
            self.vault_address
            or self.settings.HYPERLIQUID_MAIN_WALLET
            or self.wallet_address
        )

        self._info = Info(
            base_url=self.settings.HYPERLIQUID_API_URL, skip_ws=True
        )
        self._nonce_counter = int(time.time() * 1000)

        # Cache szDecimals for each asset (fetched lazily, mirrors HyperliquidClient)
        self._sz_decimals: Dict[str, int] = {}

        # Taker fee on Hyperliquid: 5 basis points (0.05%)
        self.TAKER_FEE_BPS = 0.0005

        self.ledger = DryRunLedger(
            path=self.settings.DRY_RUN_STATE_PATH,
            initial_equity=self.settings.DRY_RUN_INITIAL_CAPITAL,
        )
        self.ledger.load()

    def _get_nonce(self) -> int:
        self._nonce_counter += 1
        return self._nonce_counter

    def _round_size(self, symbol: str, size: float) -> float:
        """Round order size to the asset's szDecimals precision (mirrors HyperliquidClient)."""
        if symbol not in self._sz_decimals:
            meta = self._info.meta()
            for asset in meta.get("universe", []):
                self._sz_decimals[asset["name"]] = asset.get("szDecimals", 0)
        decimals = self._sz_decimals.get(symbol, 0)
        return round(size, decimals)

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
        original_size = size
        size = self._round_size(symbol, size)

        if size != original_size:
            logger.debug(
                "Dry order size rounded",
                extra={
                    "symbol": symbol,
                    "original_size": original_size,
                    "rounded_size": size,
                    "sz_decimals": self._sz_decimals.get(symbol, 0),
                },
            )

        if size <= 0:
            logger.debug(
                "Dry order rejected: size rounded to zero",
                extra={"symbol": symbol, "original_size": original_size},
            )
            return Order(
                id=str(self._get_nonce()),
                symbol=symbol,
                side=side,
                order_type=order_type,
                size=size,
                price=fill_price,
                status=OrderStatus.REJECTED,
                filled_size=0.0,
                avg_fill_price=fill_price,
            )

        applied = self._apply_fill(symbol, side, size, fill_price, reduce_only)
        self.ledger.save()

        if not applied:
            return Order(
                id=str(self._get_nonce()),
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
            id=str(self._get_nonce()),
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
            id=str(self._get_nonce()),
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

            # Deduct taker fee (paid on close)
            fee = fill_price * close_size * self.TAKER_FEE_BPS
            pnl -= fee

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
                    "fee": round(fee, 10),
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
                    "fee": round(fee, 10),
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
        return _parse_all_funding_rates(result).get(symbol, 0.0)

    async def get_all_funding_rates(self) -> Dict[str, float]:
        result = await asyncio.to_thread(self._info.meta_and_asset_ctxs)
        return _parse_all_funding_rates(result)

    async def get_recent_candles(
        self,
        symbol: str,
        interval: str = "1h",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 500,
        funding_rates: Optional[Dict[str, float]] = None,
    ) -> List[Candle]:
        candles = await fetch_candles_paginated(
            self._info, symbol, interval, start_time, end_time, limit
        )
        _attach_funding_to_last_candle(candles, symbol, funding_rates, self.get_funding_rate)
        return candles

    async def close(self) -> None:
        pass

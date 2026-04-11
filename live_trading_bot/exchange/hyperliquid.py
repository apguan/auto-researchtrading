"""Hyperliquid exchange client wrapping the official hyperliquid-python-sdk.

Provides an async interface that returns the bot's internal types (Order,
AccountState, etc.) while delegating signing, wire formats, and API calls
to the SDK.

This is the **live** implementation — it places real orders on Hyperliquid.
For simulated trading, see dry_exchange.py.
"""

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from eth_account import Account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))


from .types import (
    Order,
    OrderSide,
    OrderType,
    OrderStatus,
    Position,
    PositionSide,
    AccountState,
    Candle,
    parse_user_state_positions,
)
from ..monitoring.logger import get_logger
from ..config import get_settings

logger = get_logger(__name__)


async def _attach_funding_to_last_candle(
    candles: List[Candle],
    symbol: str,
    funding_rates: Optional[Dict[str, float]],
    client_funding_fn,
):
    if not candles:
        return
    try:
        if funding_rates is not None:
            current_funding = funding_rates.get(symbol, 0.0)
        else:
            current_funding = await client_funding_fn(symbol)
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
            "Failed to attach funding rate",
            extra={"symbol": symbol, "error": str(e)},
        )


def _parse_all_funding_rates(result: Any) -> Dict[str, float]:
    rates: Dict[str, float] = {}
    if isinstance(result, list) and len(result) > 1:
        meta, asset_ctxs = result[0], result[1]
        for i, coin_info in enumerate(meta.get("universe", [])):
            if i < len(asset_ctxs):
                rates[coin_info.get("name", "")] = float(asset_ctxs[i].get("funding", 0))
    return rates



async def fetch_usdc_cross_margin_perps(
    info: Info, min_volume_24h: float = 10_000_000
) -> list[str]:
    """Fetch USDC-collateralized perps with cross margin and sufficient volume.

    Filters:
      - delisted (isDelisted=true)
      - isolated-only (onlyIsolated=true or marginMode ∈ {strictIsolated, noCross})
      - 24h notional volume < min_volume_24h (uses dayNtlVlm from metaAndAssetCtxs)

    Returns sorted list of coin names, e.g. ["BTC", "ETH", "SOL", ...].
    """
    meta, asset_ctxs = await asyncio.to_thread(info.meta_and_asset_ctxs)
    perps: list[str] = []
    for asset, ctx in zip(meta.get("universe", []), asset_ctxs):
        if asset.get("isDelisted", False):
            continue
        if asset.get("onlyIsolated"):
            continue
        margin_mode = asset.get("marginMode")
        if margin_mode in ("strictIsolated", "noCross"):
            continue
        if min_volume_24h > 0:
            vol = float(ctx.get("dayNtlVlm", 0))
            if vol < min_volume_24h:
                continue
        perps.append(asset["name"])
    return sorted(perps)


async def fetch_candles_paginated(
    info: Info,
    symbol: str,
    interval: str,
    start_time: Optional[int],
    end_time: Optional[int],
    limit: int,
) -> List[Candle]:
    """Fetch candles with pagination and deduplication.

    Shared by HyperliquidClient and DryExchange (both delegate to Info API).
    Does NOT attach a funding rate to the last candle — callers handle that.
    """
    if end_time is None:
        end_time = int(time.time() * 1000)

    raw = interval.rstrip("mhs")
    value = int(raw)
    unit = interval[-1]
    if unit == "h":
        interval_minutes = value * 60
    elif unit == "m":
        interval_minutes = value
    elif unit == "s":
        interval_minutes = max(1, value // 60)
    else:
        interval_minutes = 60

    if start_time is None:
        start_time = end_time - (limit * interval_minutes * 60 * 1000)

    all_candles: List[Candle] = []
    window_end = end_time
    ms_per_window = limit * interval_minutes * 60 * 1000

    while len(all_candles) < limit and window_end > start_time:
        window_start = max(start_time, window_end - ms_per_window)

        result = await asyncio.to_thread(
            info.candles_snapshot, symbol, interval, window_start, window_end
        )

        new_candles: List[Candle] = []
        for bar in result:
            new_candles.append(
                Candle(
                    symbol=symbol,
                    timestamp=int(bar.get("t", 0)),
                    open=float(bar.get("o", 0)),
                    high=float(bar.get("h", 0)),
                    low=float(bar.get("l", 0)),
                    close=float(bar.get("c", 0)),
                    volume=float(bar.get("v", 0)),
                    funding_rate=0.0,
                )
            )

        if not new_candles:
            break

        window_end = new_candles[0].timestamp - 1
        all_candles.extend(new_candles)

        logger.info(
            "Fetched candle batch",
            extra={
                "symbol": symbol,
                "batch_size": len(new_candles),
                "total": len(all_candles),
            },
        )

    seen_ts: set[int] = set()
    unique: List[Candle] = []
    for c in all_candles:
        if c.timestamp not in seen_ts:
            seen_ts.add(c.timestamp)
            unique.append(c)

    candles = sorted(unique, key=lambda x: x.timestamp)

    if len(candles) > limit:
        candles = candles[-limit:]

    return candles



class HyperliquidClient:
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

        base_url = self.settings.HYPERLIQUID_API_URL

        self._info = Info(base_url=base_url, skip_ws=True)
        self._exchange = Exchange(
            wallet=self.account,
            base_url=base_url,
            vault_address=self.vault_address,
            account_address=self.query_address,
        )

        self._nonce_counter = int(time.time() * 1000)

        # Cache szDecimals for each asset (fetched lazily)
        self._sz_decimals: Dict[str, int] = {}

    def _round_size(self, symbol: str, size: float) -> float:
        if symbol not in self._sz_decimals:
            meta = self._info.meta()
            for asset in meta.get("universe", []):
                self._sz_decimals[asset["name"]] = asset.get("szDecimals", 0)
        decimals = self._sz_decimals.get(symbol, 0)
        return round(size, decimals)

    async def close(self):
        pass

    def _get_nonce(self) -> int:
        self._nonce_counter += 1
        return self._nonce_counter

    async def get_account_state(self) -> AccountState:
        result = await asyncio.to_thread(self._info.user_state, self.query_address)

        positions = {
            p.symbol: p for p in parse_user_state_positions(result)
        }
        margin_summary = result.get("marginSummary", {})
        account_value = float(margin_summary.get("accountValue", 0))
        available_balance = float(margin_summary.get("availableBalance", 0))

        return AccountState(
            wallet_address=self.query_address,
            total_equity=account_value,
            available_balance=available_balance,
            margin_used=account_value - available_balance,
            unrealized_pnl=sum(p.unrealized_pnl for p in positions.values()),
            positions=positions,
        )

    async def get_mid_price(self, symbol: str) -> float:
        mids = await asyncio.to_thread(self._info.all_mids)
        price_str = mids.get(symbol, "0")
        return float(price_str)

    async def get_all_mid_prices(self) -> Dict[str, float]:
        mids = await asyncio.to_thread(self._info.all_mids)
        return {symbol: float(px) for symbol, px in mids.items()}

    async def set_leverage(self, symbol: str, leverage: int):
        result = await asyncio.to_thread(
            self._exchange.update_leverage, leverage, symbol, True
        )
        if result.get("status") != "ok":
            error_msg = result.get("response", "unknown")
            logger.critical(
                "Failed to set leverage",
                extra={"symbol": symbol, "leverage": leverage, "error": error_msg},
            )
            raise RuntimeError(
                f"set_leverage failed for {symbol} leverage={leverage}: {error_msg}"
            )
        logger.info(
            "Set leverage",
            extra={"symbol": symbol, "leverage": leverage, "status": "ok"},
        )
        return result

    async def set_leverage_for_symbols(self, symbols: List[str], leverage: int):
        failures: list[str] = []
        for symbol in symbols:
            try:
                await self.set_leverage(symbol, leverage)
            except RuntimeError:
                failures.append(symbol)
        if failures:
            raise RuntimeError(
                f"set_leverage failed for {len(failures)}/{len(symbols)} symbols: {', '.join(failures)}"
            )

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        size: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        reduce_only: bool = False,
    ) -> Order:
        original_size = size
        size = self._round_size(symbol, size)
        if size <= 0:
            logger.warning(
                "Order rejected: size rounded to zero after szDecimals",
                extra={"symbol": symbol, "original_size": original_size},
            )
            return Order(
                id=str(self._get_nonce()),
                symbol=symbol,
                side=side,
                order_type=order_type,
                size=size,
                price=price,
                status=OrderStatus.REJECTED,
            )
        is_buy = side == OrderSide.BUY
        slippage = 0.05  # 5% slippage for market orders

        if order_type == OrderType.MARKET:
            limit_px = self._exchange._slippage_price(symbol, is_buy, slippage, price)
            sdk_order_type: Any = {"limit": {"tif": "Ioc"}}
        else:
            limit_px = price or await self.get_mid_price(symbol)
            sdk_order_type = {"limit": {"tif": "Gtc"}}

        result = await asyncio.to_thread(
            self._exchange.order,
            symbol,
            is_buy,
            size,
            limit_px,
            sdk_order_type,
            reduce_only,
        )

        return self._parse_order_result(result, symbol, side, order_type, size, price)

    async def place_trigger_order(
        self,
        symbol: str,
        side: OrderSide,
        size: float,
        trigger_price: float,
        is_market: bool = True,
        tpsl: str = "sl",
    ) -> Order:
        original_size = size
        size = self._round_size(symbol, size)
        if size <= 0:
            logger.warning(
                "Trigger order rejected: size rounded to zero after szDecimals",
                extra={"symbol": symbol, "original_size": original_size},
            )
            return Order(
                id=str(self._get_nonce()),
                symbol=symbol,
                side=side,
                order_type=OrderType.TRIGGER,
                size=size,
                price=trigger_price,
                status=OrderStatus.REJECTED,
            )

        sdk_order_type: Any = {
            "trigger": {
                "triggerPx": trigger_price,
                "isMarket": is_market,
                "tpsl": tpsl,
            }
        }

        result = await asyncio.to_thread(
            self._exchange.order,
            symbol,
            side == OrderSide.BUY,
            size,
            trigger_price,
            sdk_order_type,
            True,  # reduce_only
        )

        return self._parse_trigger_result(result, symbol, side, size, trigger_price)

    def _parse_order_result(
        self,
        result: Any,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        size: float,
        price: Optional[float],
    ) -> Order:
        if result is None or result.get("status") == "err":
            error_msg = (
                result.get("response", "Unknown error") if result else "No response"
            )
            logger.error("Order rejected", extra={"symbol": symbol, "error": error_msg})
            return Order(
                id=str(self._get_nonce()),
                symbol=symbol,
                side=side,
                order_type=order_type,
                size=size,
                price=price,
                status=OrderStatus.REJECTED,
            )

        response_data = result.get("response", {}).get("data", {})
        statuses = response_data.get("statuses", [])

        if statuses and isinstance(statuses[0], dict):
            status = statuses[0]

            if "filled" in status:
                filled_data = status["filled"]
                filled_size = float(filled_data.get("totalSz", 0))
                avg_fill_price = float(filled_data.get("avgPx", 0))
                order_id = filled_data.get("oid", self._get_nonce())

                fill_status = (
                    OrderStatus.PARTIALLY_FILLED
                    if filled_size < size
                    else OrderStatus.FILLED
                )

                return Order(
                    id=str(order_id),
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    size=size,
                    price=price,
                    status=fill_status,
                    filled_size=filled_size,
                    avg_fill_price=avg_fill_price,
                )
            elif "resting" in status:
                oid = status["resting"].get("oid", self._get_nonce())
                return Order(
                    id=str(oid),
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    size=size,
                    price=price,
                    status=OrderStatus.PENDING,
                )
            elif "error" in status:
                logger.error(
                    "Order rejected",
                    extra={"symbol": symbol, "error": status["error"]},
                )

        return Order(
            id=str(self._get_nonce()),
            symbol=symbol,
            side=side,
            order_type=order_type,
            size=size,
            price=price,
            status=OrderStatus.REJECTED,
        )

    def _parse_trigger_result(
        self,
        result: Any,
        symbol: str,
        side: OrderSide,
        size: float,
        trigger_price: float,
    ) -> Order:
        if result is None or result.get("status") == "err":
            error_msg = (
                result.get("response", "Unknown error") if result else "No response"
            )
            logger.error(
                "Trigger order rejected",
                extra={"symbol": symbol, "error": error_msg},
            )
            return Order(
                id=str(self._get_nonce()),
                symbol=symbol,
                side=side,
                order_type=OrderType.TRIGGER,
                size=size,
                price=trigger_price,
                status=OrderStatus.REJECTED,
            )

        response_data = result.get("response", {}).get("data", {})
        statuses = response_data.get("statuses", [])

        if statuses and isinstance(statuses[0], dict):
            status = statuses[0]
            if "resting" in status:
                oid = status["resting"].get("oid", self._get_nonce())
                return Order(
                    id=str(oid),
                    symbol=symbol,
                    side=side,
                    order_type=OrderType.TRIGGER,
                    size=size,
                    price=trigger_price,
                    status=OrderStatus.PENDING,
                    filled_size=0.0,
                    avg_fill_price=trigger_price,
                )

        return Order(
            id=str(self._get_nonce()),
            symbol=symbol,
            side=side,
            order_type=OrderType.TRIGGER,
            size=size,
            price=trigger_price,
            status=OrderStatus.REJECTED,
        )

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        try:
            result = await asyncio.to_thread(
                self._exchange.cancel, symbol, int(order_id)
            )
            return result.get("status") == "ok"
        except Exception as e:
            logger.error(
                "Failed to cancel order",
                extra={"symbol": symbol, "order_id": order_id, "error": str(e)},
            )
            return False

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> bool:
        open_orders = await self.get_open_orders(symbol)
        for order in open_orders:
            await self.cancel_order(order.symbol, order.id)
        return True

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        result = await asyncio.to_thread(
            self._info.frontend_open_orders, self.query_address
        )

        orders: List[Order] = []
        for order_data in result:
            coin = order_data.get("coin")
            if not coin:
                continue
            if symbol and coin != symbol:
                continue

            ot_side = OrderSide.BUY if order_data.get("side") == "B" else OrderSide.SELL
            is_trigger = order_data.get("isTrigger", False)
            raw_ot = order_data.get("orderType", "")
            if is_trigger or (isinstance(raw_ot, dict) and "trigger" in raw_ot):
                ot_type = OrderType.TRIGGER
                price = float(
                    order_data.get("triggerPx", 0) or order_data.get("limitPx", 0)
                )
            elif raw_ot == "Limit" or raw_ot == "limit":
                ot_type = OrderType.LIMIT
                price = float(order_data.get("limitPx", 0))
            else:
                ot_type = OrderType.MARKET
                price = float(order_data.get("limitPx", 0))

            orders.append(
                Order(
                    id=str(order_data.get("oid", "")),
                    symbol=coin,
                    side=ot_side,
                    order_type=ot_type,
                    size=float(order_data.get("origSz", 0)),
                    price=price,
                    status=OrderStatus.PENDING,
                    filled_size=float(order_data.get("sz", 0)),
                    timestamp=datetime.fromtimestamp(
                        order_data.get("timestamp", 0) / 1000
                    ),
                )
            )
        return orders

    async def get_user_fills(self, start_time: int, end_time: int) -> list[dict]:
        return await asyncio.to_thread(
            self._info.user_fills_by_time, self.query_address, start_time, end_time
        )

    async def get_funding_history(
        self, start_time: int, end_time: int | None = None
    ) -> list[dict]:
        return await asyncio.to_thread(
            self._info.user_funding_history, self.query_address, start_time, end_time
        )

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
        await _attach_funding_to_last_candle(candles, symbol, funding_rates, self.get_funding_rate)
        return candles

    async def get_usdc_cross_margin_perps(
        self, min_volume_24h: float = 10_000_000
    ) -> list[str]:
        return await fetch_usdc_cross_margin_perps(self._info, min_volume_24h)

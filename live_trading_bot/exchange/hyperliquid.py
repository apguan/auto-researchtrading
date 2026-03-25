import json
import time
import hashlib
from datetime import datetime
from typing import Dict, List, Optional, Any
import httpx
from eth_account import Account
from eth_account.messages import encode_defunct

from .types import (
    Order,
    OrderSide,
    OrderType,
    OrderStatus,
    Position,
    PositionSide,
    AccountState,
    Ticker,
    Candle,
)
from config import get_settings


class HyperliquidClient:
    def __init__(self, private_key: str, dry_run: bool = False):
        self.settings = get_settings()
        self.private_key = private_key
        self.dry_run = dry_run

        self.account = Account.from_key(private_key)
        self.wallet_address = self.account.address

        self.base_url = self.settings.HYPERLIQUID_API_URL
        self.client = httpx.AsyncClient(timeout=self.settings.REQUEST_TIMEOUT_SECONDS)

        self._nonce_counter = int(time.time() * 1000)

    async def close(self):
        await self.client.aclose()

    def _get_nonce(self) -> int:
        self._nonce_counter += 1
        return self._nonce_counter

    def _sign_l1_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        connection_id = hashlib.sha256(b"mainnet").hexdigest()

        msg = json.dumps(
            {
                "source": self.wallet_address,
                "connectionId": connection_id,
                "payload": action,
            },
            separators=(",", ":"),
        )

        signed = self.account.sign_message(encode_defunct(text=msg))

        return {"r": hex(signed.r), "s": hex(signed.s), "v": signed.v}

    async def _request(
        self, endpoint: str, data: Dict[str, Any], requires_auth: bool = False
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/{endpoint}"

        if requires_auth:
            signature = self._sign_l1_action(data)
            data["signature"] = signature

        response = await self.client.post(url, json=data)
        response.raise_for_status()
        return response.json()

    async def get_account_state(self) -> AccountState:
        data = {"type": "clearinghouseState", "user": self.wallet_address}
        result = await self._request("info", data)

        positions = {}
        margin_summary = result.get("marginSummary", {})
        account_value = float(margin_summary.get("accountValue", 0))
        available_balance = float(margin_summary.get("availableBalance", 0))

        for pos_data in result.get("assetPositions", []):
            pos_info = pos_data.get("position", {})
            coin = pos_info.get("coin")
            if not coin:
                continue

            size = float(pos_info.get("szi", 0))
            if size == 0:
                continue

            side = PositionSide.LONG if size > 0 else PositionSide.SHORT
            entry_price = float(pos_info.get("entryPx", 0))
            mark_price = float(pos_info.get("markPx", 0))
            unrealized_pnl = float(pos_info.get("unrealizedPnl", 0))
            leverage = float(pos_info.get("leverage", {}).get("value", 1))
            margin_used = float(pos_info.get("marginUsed", 0))
            liq_price = pos_info.get("liquidationPx")

            positions[coin] = Position(
                symbol=coin,
                side=side,
                size=abs(size),
                entry_price=entry_price,
                current_price=mark_price,
                unrealized_pnl=unrealized_pnl,
                leverage=leverage,
                margin_used=margin_used,
                liquidation_price=float(liq_price) if liq_price else None,
            )

        return AccountState(
            wallet_address=self.wallet_address,
            total_equity=account_value,
            available_balance=available_balance,
            margin_used=account_value - available_balance,
            unrealized_pnl=sum(p.unrealized_pnl for p in positions.values()),
            positions=positions,
        )

    async def get_mid_price(self, symbol: str) -> float:
        data = {"type": "metaAndAssetCtxs"}
        result = await self._request("info", data)

        if isinstance(result, list) and len(result) > 1:
            meta = result[0]
            asset_ctxs = result[1]

            for i, coin_info in enumerate(meta.get("universe", [])):
                if coin_info.get("name") == symbol:
                    if i < len(asset_ctxs):
                        return float(asset_ctxs[i].get("markPx", 0))
        return 0.0

    async def get_all_mid_prices(self) -> Dict[str, float]:
        data = {"type": "metaAndAssetCtxs"}
        result = await self._request("info", data)

        prices = {}
        if isinstance(result, list) and len(result) > 1:
            meta = result[0]
            asset_ctxs = result[1]

            for i, coin_info in enumerate(meta.get("universe", [])):
                symbol = coin_info.get("name")
                if symbol and i < len(asset_ctxs):
                    prices[symbol] = float(asset_ctxs[i].get("markPx", 0))

        return prices

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        size: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        reduce_only: bool = False,
    ) -> Order:
        if self.dry_run:
            return Order(
                id=f"dry-run-{self._get_nonce()}",
                symbol=symbol,
                side=side,
                order_type=order_type,
                size=size,
                price=price,
                status=OrderStatus.FILLED,
                filled_size=size,
                avg_fill_price=price or await self.get_mid_price(symbol),
            )

        order_data = {
            "coin": symbol,
            "isBuy": side == OrderSide.BUY,
            "sz": size,
            "limitPx": price
            if order_type == OrderType.LIMIT
            else await self.get_mid_price(symbol)
            * (1.01 if side == OrderSide.BUY else 0.99),
            "reduceOnly": reduce_only,
            "orderType": "Ioc" if order_type == OrderType.MARKET else "Limit",
        }

        action = {"type": "order", "orders": [order_data], "grouping": "na"}

        result = await self._request("exchange", {"action": action}, requires_auth=True)

        response_data = result.get("response", {}).get("data", {})
        statuses = response_data.get("statuses", [[]])

        if statuses and statuses[0] == "Success":
            rest = response_data.get("rest", [])
            order_id = rest[0].get("oid") if rest else str(self._get_nonce())

            return Order(
                id=str(order_id),
                symbol=symbol,
                side=side,
                order_type=order_type,
                size=size,
                price=price,
                status=OrderStatus.FILLED,
                filled_size=size,
                avg_fill_price=price or await self.get_mid_price(symbol),
            )
        else:
            error_msg = statuses[0] if statuses else "Unknown error"
            return Order(
                id=str(self._get_nonce()),
                symbol=symbol,
                side=side,
                order_type=order_type,
                size=size,
                price=price,
                status=OrderStatus.REJECTED,
            )

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        if self.dry_run:
            return True

        action = {"type": "cancel", "cancels": [{"coin": symbol, "oid": int(order_id)}]}

        result = await self._request("exchange", {"action": action}, requires_auth=True)
        statuses = result.get("response", {}).get("data", {}).get("statuses", [[]])

        return statuses and statuses[0] == "Success"

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> bool:
        if self.dry_run:
            return True

        open_orders = await self.get_open_orders(symbol)

        for order in open_orders:
            await self.cancel_order(order.symbol, order.id)

        return True

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        data = {"type": "openOrders", "user": self.wallet_address}
        result = await self._request("info", data)

        orders = []
        for order_data in result:
            coin = order_data.get("coin")
            if symbol and coin != symbol:
                continue

            side = OrderSide.BUY if order_data.get("side") == "B" else OrderSide.SELL
            order_type = (
                OrderType.LIMIT
                if order_data.get("orderType") == "limit"
                else OrderType.MARKET
            )

            orders.append(
                Order(
                    id=str(order_data.get("oid", "")),
                    symbol=coin,
                    side=side,
                    order_type=order_type,
                    size=float(order_data.get("origSz", 0)),
                    price=float(order_data.get("limitPx", 0)),
                    status=OrderStatus.PENDING,
                    filled_size=float(order_data.get("sz", 0)),
                    timestamp=datetime.fromtimestamp(
                        order_data.get("timestamp", 0) / 1000
                    ),
                )
            )

        return orders

    async def get_funding_rate(self, symbol: str) -> float:
        data = {"type": "metaAndAssetCtxs"}
        result = await self._request("info", data)

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
        if end_time is None:
            end_time = int(time.time() * 1000)
        if start_time is None:
            start_time = end_time - (limit * 3600 * 1000)

        data = {
            "type": "candleSnapshot",
            "req": {
                "coin": symbol,
                "interval": interval,
                "startTime": start_time,
                "endTime": end_time,
            },
        }
        result = await self._request("info", data)

        candles = []
        for bar in result:
            candles.append(
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

        return sorted(candles, key=lambda x: x.timestamp)

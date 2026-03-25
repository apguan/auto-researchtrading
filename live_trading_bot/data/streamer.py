import asyncio
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Callable
import websockets

from exchange.types import Candle
from exchange.hyperliquid import HyperliquidClient
from data.bar_builder import BarBuilder
from config import get_settings
from monitoring.logger import get_logger

logger = get_logger(__name__)


class DataStreamer:
    def __init__(
        self,
        symbols: List[str],
        on_bar_callback: Optional[Callable] = None,
        on_tick_callback: Optional[Callable] = None,
    ):
        self.settings = get_settings()
        self.symbols = symbols
        self.on_bar_callback = on_bar_callback
        self.on_tick_callback = on_tick_callback

        self.ws_url = self.settings.HYPERLIQUID_WS_URL
        self.bar_builder = BarBuilder(symbols, interval_minutes=60)

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._reconnect_delay = self.settings.RECONNECT_DELAY_SECONDS
        self._subscription_ids: Dict[str, str] = {}

        self.latest_prices: Dict[str, float] = {}
        self.latest_volumes: Dict[str, float] = {}

    async def start(self, client: Optional[HyperliquidClient] = None):
        self._running = True

        if client:
            await self._load_historical_data(client)

        if self.on_bar_callback:
            self.bar_builder.add_bar_callback(self.on_bar_callback)

        while self._running:
            try:
                await self._connect_and_listen()
            except Exception as e:
                logger.error(f"WebSocket error", extra={"error": str(e)})
                if self._running:
                    logger.info(f"Reconnecting in {self._reconnect_delay}s...")
                    await asyncio.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(
                        self._reconnect_delay * 2,
                        self.settings.MAX_RECONNECT_DELAY_SECONDS,
                    )

    async def _load_historical_data(self, client: HyperliquidClient):
        logger.info("Loading historical data...")

        for symbol in self.symbols:
            try:
                candles = await client.get_recent_candles(
                    symbol=symbol, interval="1h", limit=self.settings.LOOKBACK_BARS
                )
                self.bar_builder.add_historical_candles(symbol, candles)

                if candles:
                    self.latest_prices[symbol] = candles[-1].close

                logger.info(
                    f"Loaded historical data",
                    extra={"symbol": symbol, "bars": len(candles)},
                )
            except Exception as e:
                logger.error(
                    f"Failed to load historical data",
                    extra={"symbol": symbol, "error": str(e)},
                )

    async def _connect_and_listen(self):
        logger.info(f"Connecting to WebSocket", extra={"url": self.ws_url})

        async with websockets.connect(self.ws_url) as ws:
            self._ws = ws
            self._reconnect_delay = self.settings.RECONNECT_DELAY_SECONDS

            await self._subscribe()
            logger.info("WebSocket connected and subscribed")

            async for message in ws:
                if not self._running:
                    break

                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError as e:
                    logger.error(f"JSON decode error", extra={"error": str(e)})
                except Exception as e:
                    logger.error(f"Message handling error", extra={"error": str(e)})

    async def _subscribe(self):
        for symbol in self.symbols:
            subscription = {"method": "subscribe", "subscription": {"type": "allMids"}}
            await self._ws.send(json.dumps(subscription))

            subscription = {
                "method": "subscribe",
                "subscription": {"type": "l2Book", "coin": symbol},
            }
            await self._ws.send(json.dumps(subscription))

            logger.debug(f"Subscribed to {symbol}")

    async def _handle_message(self, data: dict):
        channel = data.get("channel")

        if channel == "allMids":
            mids = data.get("data", {}).get("mids", {})
            for symbol, price_str in mids.items():
                if symbol in self.symbols:
                    price = float(price_str)
                    self.latest_prices[symbol] = price
                    self.bar_builder.on_tick(symbol, price)

                    if self.on_tick_callback:
                        await self._safe_callback(self.on_tick_callback, symbol, price)

        elif channel == "l2Book":
            book_data = data.get("data", {})
            symbol = book_data.get("coin")
            if symbol in self.symbols:
                levels = book_data.get("levels", [[], []])
                if levels and levels[0]:
                    best_bid = float(levels[0][0].get("px", 0))
                    best_ask = (
                        float(levels[1][0].get("px", 0))
                        if len(levels) > 1 and levels[1]
                        else best_bid
                    )
                    mid = (best_bid + best_ask) / 2

                    self.latest_prices[symbol] = mid
                    self.bar_builder.on_tick(symbol, mid)

        elif channel == "subscriptionResponse":
            logger.debug(f"Subscription response: {data}")

    async def _safe_callback(self, callback: Callable, *args):
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(*args)
            else:
                callback(*args)
        except Exception as e:
            logger.error(f"Callback error", extra={"error": str(e)})

    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.close()
        logger.info("Data streamer stopped")

    def get_latest_prices(self) -> Dict[str, float]:
        return self.latest_prices.copy()

    def get_history(self, symbol: str) -> List[Candle]:
        return self.bar_builder.get_history(symbol)

    def get_all_histories(self) -> Dict[str, List[Candle]]:
        return self.bar_builder.get_all_histories()

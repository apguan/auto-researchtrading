from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    TRIGGER = "trigger"


class OrderStatus(Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class PositionSide(Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


@dataclass
class Order:
    id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    size: float
    price: Optional[float]
    status: OrderStatus
    filled_size: float = 0.0
    avg_fill_price: float = 0.0
    timestamp: datetime | None = None
    client_order_id: Optional[str] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


@dataclass
class Position:
    symbol: str
    side: PositionSide
    size: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    leverage: float = 1.0
    margin_used: float = 0.0
    liquidation_price: Optional[float] = None
    timestamp: datetime | None = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)

    @property
    def notional_value(self) -> float:
        return abs(self.size) * self.current_price


def parse_user_state_positions(raw_state: dict) -> List[Position]:
    """Parse positions from a userState API response into Position objects."""
    positions: List[Position] = []
    for pos_data in raw_state.get("assetPositions", []):
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
        leverage_raw = pos_info.get("leverage", {})
        leverage_val = (
            float(leverage_raw.get("value", 1))
            if isinstance(leverage_raw, dict)
            else float(leverage_raw)
        )
        margin_used = float(pos_info.get("marginUsed", 0))
        liq_price = pos_info.get("liquidationPx")

        positions.append(Position(
            symbol=coin,
            side=side,
            size=abs(size),
            entry_price=entry_price,
            current_price=mark_price,
            unrealized_pnl=unrealized_pnl,
            leverage=leverage_val,
            margin_used=margin_used,
            liquidation_price=float(liq_price) if liq_price else None,
        ))
    return positions


@dataclass
class AccountState:
    wallet_address: str
    total_equity: float
    available_balance: float
    margin_used: float
    unrealized_pnl: float
    positions: Dict[str, Position]
    timestamp: datetime | None = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


@dataclass
class Trade:
    id: str
    order_id: str
    symbol: str
    side: OrderSide
    size: float
    price: float
    fee: float
    pnl: Optional[float]
    timestamp: datetime


@dataclass
class Ticker:
    symbol: str
    bid: float
    ask: float
    last: float
    volume_24h: float
    timestamp: datetime


@dataclass
class Candle:
    symbol: str
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    funding_rate: float = 0.0

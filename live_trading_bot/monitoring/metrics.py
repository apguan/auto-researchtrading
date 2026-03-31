from datetime import datetime, timezone
from typing import Dict, List, Optional
from dataclasses import dataclass, field
import math

from .logger import get_logger

logger = get_logger(__name__)


@dataclass
class TradeRecord:
    timestamp: datetime
    symbol: str
    side: str
    size: float
    price: float
    pnl: Optional[float] = None


@dataclass
class MetricsSnapshot:
    timestamp: datetime
    equity: float
    cash: float
    positions: Dict[str, float]
    unrealized_pnl: float

    trades: List[TradeRecord] = field(default_factory=list)
    daily_pnl: float = 0.0
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0

    peak_equity: float = 0.0
    max_drawdown: float = 0.0

    returns: List[float] = field(default_factory=list)


class MetricsTracker:
    def __init__(self):
        self.snapshots: List[MetricsSnapshot] = []
        self.trades: List[TradeRecord] = []
        self.peak_equity: float = 0.0
        self.max_drawdown: float = 0.0
        self.daily_start_equity: float = 0.0
        self.last_update: Optional[datetime] = None

    def record_trade(
        self,
        symbol: str,
        side: str,
        size: float,
        price: float,
        pnl: Optional[float] = None,
    ):
        trade = TradeRecord(
            timestamp=datetime.now(timezone.utc),
            symbol=symbol,
            side=side,
            size=size,
            price=price,
            pnl=pnl,
        )
        self.trades.append(trade)
        logger.debug(
            "Recorded trade",
            extra={
                "symbol": symbol,
                "side": side,
                "size": size,
                "price": price,
                "pnl": pnl,
            },
        )

    def update(
        self,
        equity: float,
        cash: float,
        positions: Dict[str, float],
        unrealized_pnl: float = 0.0,
    ):
        now = datetime.now(timezone.utc)

        if not self.snapshots or now.date() != self.snapshots[-1].timestamp.date():
            self.daily_start_equity = equity

        self.peak_equity = max(self.peak_equity, equity)

        if self.peak_equity > 0:
            drawdown = (self.peak_equity - equity) / self.peak_equity
            self.max_drawdown = max(self.max_drawdown, drawdown)

        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_trades = [t for t in self.trades if t.timestamp >= today_start]

        daily_pnl = sum(t.pnl for t in today_trades if t.pnl is not None)

        wins = sum(1 for t in today_trades if t.pnl and t.pnl > 0)
        losses = sum(1 for t in today_trades if t.pnl and t.pnl < 0)

        returns = []
        if len(self.snapshots) > 1:
            prev_equity = self.snapshots[-1].equity
            if prev_equity > 0:
                ret = (equity - prev_equity) / prev_equity
                returns = self.snapshots[-1].returns + [ret]
                returns = returns[-1000:]

        snapshot = MetricsSnapshot(
            timestamp=now,
            equity=equity,
            cash=cash,
            positions=positions.copy(),
            unrealized_pnl=unrealized_pnl,
            trades=today_trades,
            daily_pnl=daily_pnl,
            trade_count=len(today_trades),
            win_count=wins,
            loss_count=losses,
            peak_equity=self.peak_equity,
            max_drawdown=self.max_drawdown,
            returns=returns,
        )

        self.snapshots.append(snapshot)
        self.last_update = now

        if len(self.snapshots) > 10000:
            self.snapshots = self.snapshots[-5000:]

    def get_current_metrics(self) -> Optional[MetricsSnapshot]:
        if not self.snapshots:
            return None
        return self.snapshots[-1]

    def get_daily_pnl(self) -> float:
        if not self.snapshots:
            return 0.0
        return self.snapshots[-1].daily_pnl

    def get_trade_count_today(self) -> int:
        if not self.snapshots:
            return 0
        return self.snapshots[-1].trade_count

    def get_win_rate(self) -> float:
        if not self.snapshots:
            return 0.0

        snapshot = self.snapshots[-1]
        total = snapshot.win_count + snapshot.loss_count
        if total == 0:
            return 0.0
        return snapshot.win_count / total * 100

    def get_sharpe_ratio(self, periods_per_year: int = 8760) -> float:
        if not self.snapshots or not self.snapshots[-1].returns:
            return 0.0

        returns = self.snapshots[-1].returns
        if len(returns) < 10:
            return 0.0

        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        std_ret = math.sqrt(variance)

        if std_ret == 0:
            return 0.0

        return (mean_ret / std_ret) * math.sqrt(periods_per_year)

    def get_max_drawdown(self) -> float:
        return self.max_drawdown * 100

    def get_summary(self) -> dict:
        snapshot = self.get_current_metrics()
        if not snapshot:
            return {}

        return {
            "equity": snapshot.equity,
            "cash": snapshot.cash,
            "daily_pnl": snapshot.daily_pnl,
            "daily_pnl_pct": snapshot.daily_pnl / self.daily_start_equity * 100
            if self.daily_start_equity > 0
            else 0,
            "trade_count": snapshot.trade_count,
            "win_rate": self.get_win_rate(),
            "max_drawdown": self.get_max_drawdown(),
            "sharpe_ratio": self.get_sharpe_ratio(),
            "positions": snapshot.positions,
            "unrealized_pnl": snapshot.unrealized_pnl,
        }

    def reset_daily(self):
        self.daily_start_equity = 0.0
        logger.info("Daily metrics reset")

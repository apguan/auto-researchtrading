"""
Shared data types for the backtesting engine.

Zero project imports — only stdlib and pandas. Safe to import from
strategy.py without triggering prepare.py's module-level side effects.
"""

from __future__ import annotations

import pandas as pd
from dataclasses import dataclass, field


@dataclass
class BarData:
    symbol: str
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    funding_rate: float
    history: pd.DataFrame  # last LOOKBACK_BARS bars


@dataclass
class Signal:
    symbol: str
    target_position: float  # target USD notional (signed: +long, -short)
    order_type: str = "market"


@dataclass
class PortfolioState:
    cash: float
    positions: dict  # symbol -> signed USD notional
    entry_prices: dict  # symbol -> avg entry price
    equity: float = 0.0
    timestamp: int = 0


@dataclass
class BacktestResult:
    sharpe: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    num_trades: int = 0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    annual_turnover: float = 0.0
    backtest_seconds: float = 0.0
    equity_curve: list = field(default_factory=list)
    trade_log: list = field(default_factory=list)

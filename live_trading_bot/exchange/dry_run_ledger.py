"""File-backed state ledger for dry-run mode.

Replaces on-chain state (user_state, frontend_open_orders) for dry-run:
positions, entry prices, realized PnL. load() restores on startup,
save() persists after every fill.
"""

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional

from ..monitoring.logger import get_logger

logger = get_logger(__name__)


@dataclass
class _SimPosition:
    symbol: str
    is_long: bool
    size: float       # coin qty, always positive
    entry_price: float


@dataclass
class _LedgerState:
    initial_equity: float
    realized_pnl: float
    positions: Dict[str, Dict]  # symbol -> {"is_long", "size", "entry_price"}
    transactions: List[Dict] = field(default_factory=list)


class DryRunLedger:

    def __init__(self, path: str, initial_equity: float):
        self._path = Path(path)
        self._initial_equity = initial_equity
        self._realized_pnl: float = 0.0
        self._positions: Dict[str, _SimPosition] = {}
        self._transactions: List[Dict] = []

    def load(self) -> bool:
        if not self._path.exists():
            logger.info("No ledger file — starting fresh", extra={"path": str(self._path)})
            return False

        try:
            raw = json.loads(self._path.read_text())
            state = _LedgerState(**raw)

            self._initial_equity = state.initial_equity
            self._realized_pnl = state.realized_pnl
            self._transactions = raw.get("transactions", [])
            self._positions = {
                sym: _SimPosition(
                    symbol=sym,
                    is_long=p["is_long"],
                    size=p["size"],
                    entry_price=p["entry_price"],
                )
                for sym, p in state.positions.items()
            }

            logger.info(
                "Recovered dry-run state from ledger",
                extra={
                    "path": str(self._path),
                    "initial_equity": self._initial_equity,
                    "realized_pnl": round(self._realized_pnl, 4),
                    "positions": list(self._positions.keys()),
                },
            )
            return True
        except Exception:
            logger.warning("Failed to load ledger — starting fresh", extra={"path": str(self._path)}, exc_info=True)
            return False

    def save(self) -> None:
        state = _LedgerState(
            initial_equity=self._initial_equity,
            realized_pnl=self._realized_pnl,
            positions={
                sym: {"is_long": p.is_long, "size": p.size, "entry_price": p.entry_price}
                for sym, p in self._positions.items()
            },
            transactions=self._transactions,
        )
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(state), indent=2))
        tmp.replace(self._path)

    @property
    def initial_equity(self) -> float:
        return self._initial_equity

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl

    @property
    def positions(self) -> Dict[str, _SimPosition]:
        return self._positions

    @property
    def transactions(self) -> List[Dict]:
        return self._transactions

    def record_transaction(self, txn: dict) -> None:
        self._transactions.append(txn)
        if len(self._transactions) > 500:
            self._transactions = self._transactions[-500:]

    def add_realized_pnl(self, pnl: float) -> None:
        self._realized_pnl += pnl

    def open_position(self, symbol: str, is_long: bool, size: float, entry_price: float) -> None:
        self._positions[symbol] = _SimPosition(
            symbol=symbol, is_long=is_long, size=size, entry_price=entry_price,
        )

    def close_position(self, symbol: str) -> Optional[_SimPosition]:
        return self._positions.pop(symbol, None)

    def update_position(self, symbol: str, size: float, entry_price: float) -> None:
        pos = self._positions.get(symbol)
        if pos:
            pos.size = size
            pos.entry_price = entry_price

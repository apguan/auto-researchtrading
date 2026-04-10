"""Data classes for Hyperliquid vault operations."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class VaultDetails:
    name: str
    vault_address: str
    leader: str
    description: str
    apr: float
    total_equity: float = 0.0
    num_followers: int = 0
    is_closed: bool = False
    allow_deposits: bool = True
    always_close_on_withdraw: bool = False
    max_withdrawable: float = 0.0
    max_distributable: float = 0.0


@dataclass
class VaultFollower:
    user: str
    vault_equity: float
    pnl: float
    all_time_pnl: float
    days_following: int
    vault_entry_time: Optional[int]
    lockup_until: Optional[int]


@dataclass
class VaultEquity:
    vault_address: str
    equity: float

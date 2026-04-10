"""Facade combining vault queries and actions into a single manager."""

import sys
from pathlib import Path
from typing import Optional

from eth_account import Account
from eth_account.signers.local import LocalAccount
from hyperliquid.info import Info
from hyperliquid.utils.constants import MAINNET_API_URL

_LIVE_BOT_ROOT = Path(__file__).resolve().parent.parent
if str(_LIVE_BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(_LIVE_BOT_ROOT))

from exchange.types import Position

from .actions import VaultActions
from .queries import VaultQueries
from .types import VaultDetails, VaultFollower


class VaultManager:
    """High-level facade for vault operations.

    Combines read queries and write actions into a single interface.
    """

    def __init__(
        self,
        private_key: str,
        base_url: Optional[str] = None,
        vault_address: Optional[str] = None,
    ):
        base_url = base_url or MAINNET_API_URL

        self._wallet: LocalAccount = Account.from_key(private_key)
        self._info = Info(base_url=base_url, skip_ws=True)
        self._queries = VaultQueries(self._info)
        self._actions = VaultActions(
            wallet=self._wallet,
            base_url=base_url,
            account_address=self._wallet.address,
        )
        self._vault_address = vault_address

    def status(self) -> Optional[VaultDetails]:
        if not self._vault_address:
            return None
        return self._queries.get_vault_details(self._vault_address)

    def portfolio(self) -> list[Position]:
        if not self._vault_address:
            return []
        return self._queries.get_vault_positions(self._vault_address)

    def followers(self) -> list[VaultFollower]:
        if not self._vault_address:
            return []
        return self._queries.get_vault_followers(self._vault_address)

    def equity(self) -> float:
        if not self._vault_address:
            return 0.0
        state = self._info.user_state(self._vault_address)
        margin = state.get("marginSummary", {})
        return float(margin.get("accountValue", 0))

    def list_user_vaults(self) -> list[VaultDetails]:
        equities = self._queries.get_user_vault_equities(self._wallet.address)
        vaults: list[VaultDetails] = []
        for eq in equities:
            details = self._queries.get_vault_details(eq.vault_address, self._wallet.address)
            # Populate total_equity from user_vault_equities (already in USD)
            details.total_equity = eq.equity
            # Get follower count from the vaultDetails followers array
            followers = self._queries.get_vault_followers(eq.vault_address)
            details.num_followers = len(followers)
            vaults.append(details)
        return vaults

    def is_vault_mode(self) -> bool:
        return self._vault_address is not None

    def create(
        self, name: str, description: str, initial_usd: float
    ) -> dict:
        """Create a new vault. Returns raw API response."""
        return self._actions.create_vault(name, description, initial_usd)

    def deposit(self, usd: float) -> dict:
        """Deposit USD into the configured vault. Returns raw API response."""
        if not self._vault_address:
            raise ValueError("No vault address configured")
        return self._actions.deposit(self._vault_address, usd)

    def withdraw(self, usd: float) -> dict:
        """Withdraw USD from the configured vault. Returns raw API response."""
        if not self._vault_address:
            raise ValueError("No vault address configured")
        return self._actions.withdraw(self._vault_address, usd)

    @staticmethod
    def usd_to_micros(usd: float) -> int:
        """Convert USD to micro-USDC (6 decimals)."""
        return int(usd * 1_000_000)

    @staticmethod
    def micros_to_usd(micros: int) -> float:
        """Convert micro-USDC (6 decimals) to USD."""
        return micros / 1_000_000

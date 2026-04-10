"""Facade combining vault queries and actions into a single manager."""

from typing import Optional

from eth_account import Account
from eth_account.signers.local import LocalAccount
from hyperliquid.info import Info
from hyperliquid.utils.constants import MAINNET_API_URL

from .actions import VaultActions
from .queries import VaultQueries
from .types import VaultDetails, VaultFollower, VaultPosition


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

    def portfolio(self) -> list[VaultPosition]:
        if not self._vault_address:
            return []
        return self._queries.get_vault_positions(self._vault_address)

    def followers(self) -> list[VaultFollower]:
        if not self._vault_address:
            return []
        # Fetch vaultDetails WITHOUT user param to get the full follower list.
        # Passing a user param returns follower-specific state for that user only.
        raw = self._info.post(
            "/info",
            {
                "type": "vaultDetails",
                "vaultAddress": self._vault_address,
            },
        )
        return self._parse_followers(raw)

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
            # Get follower count from the raw vaultDetails response
            raw = self._info.post(
                "/info",
                {"type": "vaultDetails", "vaultAddress": eq.vault_address},
            )
            followers_list = raw.get("followers", [])
            details.num_followers = len(followers_list) if isinstance(followers_list, list) else 0
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

    @staticmethod
    def _parse_followers(raw: dict) -> list[VaultFollower]:
        """Parse follower list from a vaultDetails raw response."""
        followers: list[VaultFollower] = []
        for f in raw.get("followers", []):
            followers.append(
                VaultFollower(
                    user=f.get("user", ""),
                    vault_equity=float(f.get("vaultEquity", 0)),
                    pnl=float(f.get("pnl", 0)),
                    all_time_pnl=float(f.get("allTimePnl", 0)),
                    days_following=int(f.get("daysFollowing", 0)),
                    vault_entry_time=f.get("vaultEntryTime"),
                    lockup_until=f.get("lockupUntil"),
                )
            )
        return followers

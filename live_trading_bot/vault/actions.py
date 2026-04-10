"""Vault mutation operations — create, deposit, withdraw."""

import time
from typing import Optional

from eth_account.signers.local import LocalAccount
from hyperliquid.exchange import Exchange
from hyperliquid.utils.constants import MAINNET_API_URL
from hyperliquid.utils.signing import sign_l1_action


class VaultActions:
    """Wraps Hyperliquid Exchange API for vault write operations."""

    def __init__(
        self,
        wallet: LocalAccount,
        base_url: str,
        account_address: Optional[str] = None,
    ):
        self._exchange = Exchange(
            wallet=wallet,
            base_url=base_url,
            account_address=account_address,
        )
        self._base_url = base_url

    def create_vault(
        self, name: str, description: str, initial_usd: float
    ) -> dict:
        """Create a new vault on Hyperliquid.

        Args:
            name: Vault name (>= 3 chars).
            description: Vault description (>= 10 chars).
            initial_usd: Initial deposit in USD (>= 100).

        Returns:
            Raw API response dict.

        Raises:
            ValueError: If validation fails.
        """
        if len(name) < 3:
            raise ValueError("Vault name must be at least 3 characters")
        if len(description) < 10:
            raise ValueError("Vault description must be at least 10 characters")
        if initial_usd < 100:
            raise ValueError("Initial deposit must be at least 100 USD")

        initial_usd_micros = int(initial_usd * 1_000_000)
        timestamp = int(time.time() * 1000)
        action = {
            "type": "createVault",
            "name": name,
            "description": description,
            "initialUsd": initial_usd_micros,
            "nonce": timestamp,
        }
        is_mainnet = self._base_url == MAINNET_API_URL
        signature = sign_l1_action(
            self._exchange.wallet, action, None, timestamp, None, is_mainnet
        )
        payload = {
            "action": action,
            "signature": signature,
            "nonce": timestamp,
        }
        return self._exchange.post("/exchange", payload)

    def deposit(self, vault_address: str, usd: float) -> dict:
        """Deposit USD into a vault.

        Args:
            vault_address: Hex address of the vault.
            usd: Amount in USD to deposit.

        Returns:
            Raw API response dict.
        """
        micros = int(usd * 1_000_000)
        return self._exchange.vault_usd_transfer(
            vault_address, is_deposit=True, usd=micros
        )

    def withdraw(self, vault_address: str, usd: float) -> dict:
        """Withdraw USD from a vault.

        Args:
            vault_address: Hex address of the vault.
            usd: Amount in USD to withdraw.

        Returns:
            Raw API response dict.
        """
        micros = int(usd * 1_000_000)
        return self._exchange.vault_usd_transfer(
            vault_address, is_deposit=False, usd=micros
        )

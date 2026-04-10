"""Read-only vault info queries wrapping the Hyperliquid Info API."""

import sys
from pathlib import Path
from typing import Optional

from hyperliquid.info import Info

_LIVE_BOT_ROOT = Path(__file__).resolve().parent.parent
if str(_LIVE_BOT_ROOT) not in sys.path:
    sys.path.append(str(_LIVE_BOT_ROOT))

import importlib.util
_spec = importlib.util.spec_from_file_location("exchange.types", _LIVE_BOT_ROOT / "exchange" / "types.py")
_types_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_types_mod)
Position = _types_mod.Position
parse_user_state_positions = _types_mod.parse_user_state_positions

from .types import VaultDetails, VaultEquity, VaultFollower


class VaultQueries:
    """Wraps Hyperliquid Info API for vault read operations."""

    def __init__(self, info: Info):
        self._info = info

    def get_vault_details(
        self, vault_address: str, user: Optional[str] = None
    ) -> VaultDetails:
        """Fetch vault details from the API.

        Args:
            vault_address: Hex address of the vault.
            user: Optional user address — when provided, returns follower-specific state.
        """
        payload = {
            "type": "vaultDetails",
            "vaultAddress": vault_address,
        }
        if user is not None:
            payload["user"] = user

        raw = self._info.post("/info", payload)
        return self._parse_vault_details(raw)

    def get_user_vault_equities(self, user: str) -> list[VaultEquity]:
        raw = self._info.user_vault_equities(user)
        if not isinstance(raw, list):
            return []
        equities = []
        for item in raw:
            addr = item.get("vault", "")
            equity = float(item.get("equity", 0))
            equities.append(VaultEquity(vault_address=addr, equity=equity))
        return equities

    def get_user_vault_details(self, user: str) -> Optional[VaultDetails]:
        equities = self.get_user_vault_equities(user)
        if not equities:
            return None
        return self.get_vault_details(equities[0].vault_address, user)

    def get_vault_positions(self, vault_address: str) -> list[Position]:
        raw = self._info.user_state(vault_address)
        return parse_user_state_positions(raw)

    def is_vault(self, address: str) -> bool:
        raw = self._info.user_role(address)
        if isinstance(raw, dict):
            role = raw.get("role", "")
            return role == "vault"
        return False

    @staticmethod
    def _parse_vault_details(raw: dict) -> VaultDetails:
        """Parse a raw API response dict into a VaultDetails dataclass.

        Note: totalEquity and numFollowers are NOT in the vaultDetails API
        response. Equity must be fetched via userState(vault_address).marginSummary.
        Follower count comes from len(raw.get("followers", [])).
        """
        return VaultDetails(
            name=raw.get("name", ""),
            vault_address=raw.get("vaultAddress", raw.get("vault", "")),
            leader=raw.get("leader", ""),
            description=raw.get("description", ""),
            apr=float(raw.get("apr", 0)),
            is_closed=bool(raw.get("isClosed", False)),
            allow_deposits=bool(raw.get("allowDeposits", True)),
            always_close_on_withdraw=bool(
                raw.get("alwaysCloseOnWithdraw", False)
            ),
            max_withdrawable=float(raw.get("maxWithdrawable", 0)),
            max_distributable=float(raw.get("maxDistributable", 0)),
        )

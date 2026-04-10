#!/usr/bin/env python3
"""CLI tool for Hyperliquid vault management.

Usage:
    cd live_trading_bot
    uv run python -m vault.cli status
    uv run python -m vault.cli create --name "My Vault" --desc "Alpha seeking vault" --usd 500
    uv run python -m vault.cli deposit --usd 100
    uv run python -m vault.cli withdraw --usd 50
    uv run python -m vault.cli portfolio
    uv run python -m vault.cli followers
"""

import argparse
import os
import sys

from .manager import VaultManager


def _load_env():
    """Load required env vars. Returns (private_key, vault_address, base_url)."""
    private_key = os.environ.get("HYPERLIQUID_PRIVATE_KEY", "")
    if not private_key:
        print("Error: HYPERLIQUID_PRIVATE_KEY env var is required", file=sys.stderr)
        sys.exit(1)
    vault_address = os.environ.get("HYPERLIQUID_VAULT_ADDRESS", "")
    base_url = os.environ.get("HYPERLIQUID_API_URL", "https://api.hyperliquid.xyz")
    return private_key, vault_address, base_url


def _resolve_vault(args_vault: str | None, env_vault: str, require: bool = False) -> str:
    """Resolve vault address from CLI flag > env var."""
    address = args_vault or env_vault
    if require and not address:
        print(
            "Error: vault address required. Set --vault flag or HYPERLIQUID_VAULT_ADDRESS env var.",
            file=sys.stderr,
        )
        sys.exit(1)
    return address


def cmd_status(args):
    """Show vault details."""
    private_key, env_vault, base_url = _load_env()
    vault_address = _resolve_vault(args.vault, env_vault, require=True)

    manager = VaultManager(private_key, base_url, vault_address)
    vault = manager.status()

    if vault is None:
        print(f"Vault not found: {vault_address}", file=sys.stderr)
        sys.exit(1)

    # Equity comes from userState (already in USD), not from vaultDetails
    equity = manager.equity()

    print(f"Vault: {getattr(vault, 'name', 'N/A')}")
    print(f"Address: {getattr(vault, 'vault_address', vault_address)}")
    print(f"Leader: {getattr(vault, 'leader', 'N/A')}")
    print(f"Description: {getattr(vault, 'description', 'N/A')}")
    print(f"Equity: ${equity:,.2f}")
    print(f"APR: {getattr(vault, 'apr', 'N/A')}")
    print(f"Closed: {getattr(vault, 'is_closed', False)}")
    print(f"Deposits allowed: {getattr(vault, 'allow_deposits', True)}")


def cmd_create(args):
    """Create a new vault."""
    private_key, _, base_url = _load_env()

    name = args.name
    desc = args.desc
    initial_usd = args.usd

    if len(name) < 3:
        print("Error: vault name must be at least 3 characters", file=sys.stderr)
        sys.exit(1)

    if len(desc) < 10:
        print("Error: vault description must be at least 10 characters", file=sys.stderr)
        sys.exit(1)

    if initial_usd < 100:
        print("Error: initial deposit must be at least 100 USD", file=sys.stderr)
        sys.exit(1)

    print(f"Creating vault: {name}")
    print(f"Description: {desc}")
    print(f"Initial deposit: ${initial_usd:,.2f}")
    print("WARNING: Creating a vault costs 100 USDC (gas fee).")
    confirm = input("Proceed? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)

    manager = VaultManager(private_key, base_url, "")
    result = manager.create(name, desc, initial_usd)

    # createVault response: {"status": "ok", "response": {"type": "createVault", "data": "0x..."}}
    response = result.get("response", {})
    vault_address = response.get("data", "") if isinstance(response, dict) else ""

    if vault_address:
        print(f"Vault created successfully!")
        print(f"Address: {vault_address}")
    elif result.get("status") == "ok":
        print(f"Vault created: {result}")
    else:
        print(f"Error creating vault: {result}", file=sys.stderr)
        sys.exit(1)


def cmd_deposit(args):
    """Deposit USDC into a vault."""
    private_key, env_vault, base_url = _load_env()
    vault_address = _resolve_vault(args.vault, env_vault, require=True)

    amount = args.usd
    print(f"Depositing ${amount:,.2f} into vault {vault_address}")
    confirm = input("Confirm deposit? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)

    manager = VaultManager(private_key, base_url, vault_address)
    result = manager.deposit(amount)

    if isinstance(result, dict):
        status = result.get("status", result.get("success", "unknown"))
        print(f"Deposit result: {status}")
    else:
        print(f"Deposit submitted: {result}")


def cmd_withdraw(args):
    """Withdraw USDC from a vault."""
    private_key, env_vault, base_url = _load_env()
    vault_address = _resolve_vault(args.vault, env_vault, require=True)

    amount = args.usd

    # Show vault equity so user knows max withdrawable
    manager = VaultManager(private_key, base_url, vault_address)
    equity = manager.equity()
    print(f"Vault equity: ${equity:,.2f}")
    print(f"Withdrawal amount: ${amount:,.2f}")

    print(f"Withdrawing ${amount:,.2f} from vault {vault_address}")
    confirm = input("Confirm withdrawal? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)

    result = manager.withdraw(amount)

    if isinstance(result, dict):
        status = result.get("status", result.get("success", "unknown"))
        print(f"Withdrawal result: {status}")
    else:
        print(f"Withdrawal submitted: {result}")


def cmd_portfolio(args):
    """Show vault's open positions."""
    private_key, env_vault, base_url = _load_env()
    vault_address = _resolve_vault(args.vault, env_vault, require=True)

    manager = VaultManager(private_key, base_url, vault_address)
    positions = manager.portfolio()

    if not positions:
        print("No open positions.")
        return

    header = f"{'COIN':<12} {'SIDE':<8} {'SIZE':>14} {'ENTRY':>14} {'UNREALIZED PnL':>16} {'LEVERAGE':>10}"
    print(header)
    print("-" * len(header))

    for pos in positions:
        coin = getattr(pos, "coin", "?")
        size = getattr(pos, "size", 0)
        entry = getattr(pos, "entry_price", 0)
        # Info API returns unrealizedPnl in USD — no micros conversion needed
        pnl = float(getattr(pos, "unrealized_pnl", 0))
        leverage = getattr(pos, "leverage", 0)
        side = "LONG" if size >= 0 else "SHORT"

        print(f"{coin:<12} {side:<8} {size:>14.6f} {entry:>14.2f} {pnl:>16.2f} {leverage:>10.1f}x")


def cmd_followers(args):
    """List vault followers."""
    private_key, env_vault, base_url = _load_env()
    vault_address = _resolve_vault(args.vault, env_vault, require=True)

    manager = VaultManager(private_key, base_url, vault_address)
    followers = manager.followers()

    if not followers:
        print("No followers.")
        return

    header = f"{'USER':<44} {'EQUITY':>14} {'PnL':>14} {'DAYS':>8}"
    print(header)
    print("-" * len(header))

    for f in followers:
        user = getattr(f, "user", "?")
        # Info API returns vaultEquity and pnl in USD — no micros conversion needed
        equity = float(getattr(f, "vault_equity", 0))
        pnl = float(getattr(f, "pnl", getattr(f, "all_time_pnl", 0)))
        days = getattr(f, "days_following", 0)

        print(f"{user:<44} ${equity:>13.2f} ${pnl:>13.2f} {days:>8d}")


def cmd_list(args):
    """List all vaults the user has deposits in."""
    private_key, _, base_url = _load_env()

    manager = VaultManager(private_key, base_url, "")
    vaults = manager.list_user_vaults()

    if not vaults:
        print("No vault deposits found.")
        return

    header = f"{'NAME':<30} {'ADDRESS':<44} {'EQUITY':>14}"
    print(header)
    print("-" * len(header))

    for v in vaults:
        name = getattr(v, "name", "?")
        addr = getattr(v, "vault_address", "?")
        # Equity from user_vault_equities is already in USD
        equity = float(getattr(v, "total_equity", 0))
        print(f"{name:<30} {addr:<44} ${equity:>13.2f}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vault.cli",
        description="Hyperliquid vault management CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Show vault details")
    p_status.add_argument("--vault", default=None, help="Vault address (or set HYPERLIQUID_VAULT_ADDRESS)")

    p_create = sub.add_parser("create", help="Create a new vault")
    p_create.add_argument("--name", required=True, help="Vault name (min 3 chars)")
    p_create.add_argument("--desc", required=True, help="Vault description (min 10 chars)")
    p_create.add_argument("--usd", type=float, default=100, help="Initial deposit in USD (min 100, default 100)")

    p_deposit = sub.add_parser("deposit", help="Deposit USDC into vault")
    p_deposit.add_argument("--usd", type=float, required=True, help="Amount to deposit")
    p_deposit.add_argument("--vault", default=None, help="Vault address (or set HYPERLIQUID_VAULT_ADDRESS)")

    p_withdraw = sub.add_parser("withdraw", help="Withdraw USDC from vault")
    p_withdraw.add_argument("--usd", type=float, required=True, help="Amount to withdraw")
    p_withdraw.add_argument("--vault", default=None, help="Vault address (or set HYPERLIQUID_VAULT_ADDRESS)")

    p_portfolio = sub.add_parser("portfolio", help="Show vault's open positions")
    p_portfolio.add_argument("--vault", default=None, help="Vault address (or set HYPERLIQUID_VAULT_ADDRESS)")

    p_followers = sub.add_parser("followers", help="List vault followers")
    p_followers.add_argument("--vault", default=None, help="Vault address (or set HYPERLIQUID_VAULT_ADDRESS)")

    sub.add_parser("list", help="List all vaults you have deposits in")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    commands = {
        "status": cmd_status,
        "create": cmd_create,
        "deposit": cmd_deposit,
        "withdraw": cmd_withdraw,
        "portfolio": cmd_portfolio,
        "followers": cmd_followers,
        "list": cmd_list,
    }

    handler = commands.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    try:
        handler(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

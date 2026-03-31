#!/usr/bin/env python3
"""Query realized + unrealized PnL from Hyperliquid for preset time windows.

Usage:
    python pnl.py              # defaults to 'today'
    python pnl.py 1h           # last hour
    python pnl.py 24h          # last 24 hours
    python pnl.py today        # since midnight UTC
    python pnl.py 7d           # last 7 days

Sources realized PnL and funding directly from the exchange, so it captures
positions closed outside the bot (manual trades, liquidations, etc.).
"""

import asyncio
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

_PARENT_DIR = str(Path(__file__).resolve().parent.parent)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from live_trading_bot.config import get_private_key
from live_trading_bot.exchange.hyperliquid import HyperliquidClient


def parse_window(arg: str) -> tuple[int, int, str]:
    """Parse a time window string into (start_ms, end_ms, label)."""
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)

    arg = arg.strip().lower()
    if arg == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(start.timestamp() * 1000), end_ms, "Today (UTC)"
    elif arg.endswith("h"):
        hours = int(arg[:-1])
        start = now - timedelta(hours=hours)
        return int(start.timestamp() * 1000), end_ms, f"Last {hours}h"
    elif arg.endswith("d"):
        days = int(arg[:-1])
        start = now - timedelta(days=days)
        return int(start.timestamp() * 1000), end_ms, f"Last {days}d"
    elif arg.endswith("m") and not arg.endswith("mo"):
        minutes = int(arg[:-1])
        start = now - timedelta(minutes=minutes)
        return int(start.timestamp() * 1000), end_ms, f"Last {minutes}m"
    else:
        raise ValueError(f"Unknown window format: {arg}. Use 1h, 24h, today, 7d, etc.")


async def query_pnl(window: str = "today"):
    start_ms, end_ms, label = parse_window(window)

    client = HyperliquidClient(private_key=get_private_key())

    fills, funding, account = await asyncio.gather(
        client.get_user_fills(start_ms, end_ms),
        client.get_funding_history(start_ms, end_ms),
        client.get_account_state(),
    )

    await client.close()

    # Realized PnL by symbol
    realized_by_symbol = defaultdict(float)
    for fill in fills:
        coin = fill.get("coin", "?")
        closed_pnl = float(fill.get("closedPnl", 0))
        realized_by_symbol[coin] += closed_pnl

    # Funding PnL by symbol
    funding_by_symbol = defaultdict(float)
    for entry in funding:
        coin = entry.get("coin", entry.get("delta", {}).get("coin", "?"))
        delta = entry.get("delta", {})
        usdc = float(delta.get("usdc", 0)) if isinstance(delta, dict) else 0
        funding_by_symbol[coin] += usdc

    # Unrealized PnL from open positions
    unrealized_by_symbol = {}
    for symbol, pos in account.positions.items():
        unrealized_by_symbol[symbol] = pos.unrealized_pnl

    # Collect all symbols
    all_symbols = sorted(
        set(realized_by_symbol) | set(funding_by_symbol) | set(unrealized_by_symbol)
    )

    total_realized = sum(realized_by_symbol.values())
    total_funding = sum(funding_by_symbol.values())
    total_unrealized = sum(unrealized_by_symbol.values())
    total = total_realized + total_funding + total_unrealized

    # Print
    print(f"\n{'=' * 55}")
    print(f"  PnL Report — {label}")
    print(f"  Wallet: {account.wallet_address[:10]}...{account.wallet_address[-4:]}")
    print(f"  Equity: ${account.total_equity:.2f}")
    print(f"{'=' * 55}")

    if all_symbols:
        print(f"\n  {'Symbol':<8} {'Realized':>10} {'Funding':>10} {'Unrealized':>10}")
        print(f"  {'-' * 8} {'-' * 10} {'-' * 10} {'-' * 10}")
        for sym in all_symbols:
            r = realized_by_symbol.get(sym, 0)
            f = funding_by_symbol.get(sym, 0)
            u = unrealized_by_symbol.get(sym, 0)
            print(f"  {sym:<8} {r:>+10.2f} {f:>+10.2f} {u:>+10.2f}")

    print(
        f"\n  {'TOTAL':<8} {total_realized:>+10.2f} {total_funding:>+10.2f} {total_unrealized:>+10.2f}"
    )
    print(f"\n  Net PnL: ${total:+.2f}")
    print(f"  Fills: {len(fills)} | Funding events: {len(funding)}")
    print(f"{'=' * 55}\n")


if __name__ == "__main__":
    window = sys.argv[1] if len(sys.argv) > 1 else "today"
    asyncio.run(query_pnl(window))

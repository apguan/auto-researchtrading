#!/usr/bin/env python3
"""Emergency kill switch — closes ALL open positions and cancels ALL orders.

Standalone script that connects directly to Hyperliquid (no full bot init).
Records closures in the database for audit trail.

Usage:
    uv run python kill_switch.py              # interactive confirmation
    uv run python kill_switch.py --confirm    # skip confirmation (scripting)
    uv run python kill_switch.py --dry-run    # show what would close, no orders
    uv run python kill_switch.py --dry-run --confirm  # dry run, no prompts

Safety: refuses to run on Railway/Render/Heroku (local-only).
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root is on sys.path so live_trading_bot imports work
# (same pattern as pnl.py lines 21-23)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from live_trading_bot.config import get_private_key  # noqa: E402
from live_trading_bot.exchange import (  # noqa: E402
    OrderSide,
    OrderType,
    create_exchange,
)
from live_trading_bot.storage import (  # noqa: E402
    RiskEvent,
    Trade,
    create_repository,
)


# ── Constants ────────────────────────────────────────────────────────────────
TAKER_FEE_BPS = 0.0005
CLOUD_ENV_VARS = ("RAILWAY_ENVIRONMENT", "RENDER", "DYNO")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _check_local_only() -> None:
    """Abort if running on a cloud platform."""
    for var in CLOUD_ENV_VARS:
        if os.getenv(var):
            print("ABORTING: This script can only be run locally")
            sys.exit(1)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Emergency kill switch — close all positions and cancel all orders"
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Skip interactive confirmation prompt",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be closed without placing any orders",
    )
    return parser


def _print_banner(is_dry_run: bool) -> None:
    if is_dry_run:
        print("\n  DRY RUN — no orders will be placed\n")
    else:
        print("\n  *** LIVE MODE — real orders will be placed ***\n")


def _print_summary_header(
    wallet_address: str,
    equity: float,
    positions: dict,
    open_orders: list,
    is_dry_run: bool,
) -> None:
    short_wallet = f"{wallet_address[:10]}...{wallet_address[-4:]}"
    sep = "\u2550" * 57
    print(f"\n{sep}")
    print(f"  KILL SWITCH {'— DRY RUN ' if is_dry_run else '—'} Position Closure Report")
    print(f"  Wallet: {short_wallet}")
    print(f"  Equity: ${equity:,.2f}")
    print(f"{sep}")
    print(f"\n  Open positions: {len(positions)}")
    for sym, pos in positions.items():
        print(
            f"    {sym:<8} {pos.side.value:<6} size={pos.size:.4f}  "
            f"entry={pos.entry_price:,.2f}  pnl={pos.unrealized_pnl:+,.2f}"
        )
    print(f"\n  Open orders: {len(open_orders)}")
    for order in open_orders:
        print(
            f"    {order.symbol:<8} {order.side.value:<6} {order.order_type.value:<8} "
            f"size={order.size:.4f}"
        )
    print()


def _print_closure_report(results: list[dict]) -> None:
    if not results:
        print("\n  No positions were closed.\n")
        return

    sep = "\u2550" * 57
    print(f"\n{sep}")
    print(f"  KILL SWITCH {'— DRY RUN ' if results[0].get('dry_run') else '—'} Position Closure Report")
    print(f"  Wallet: {results[0]['wallet']}")
    print(f"  Equity: ${results[0]['equity']:,.2f}")
    print(f"{sep}")

    print(f"\n  {'Symbol':<8} {'Side':<8} {'Size':>10} {'Entry':>12} {'Fill':>12} {'PnL':>10}")
    print(f"  {'-'*8} {'-'*8} {'-'*10} {'-'*12} {'-'*12} {'-'*10}")

    total_pnl = 0.0
    total_fees = 0.0
    for r in results:
        pnl = r["pnl"]
        fee = r["fee"]
        total_pnl += pnl
        total_fees += fee
        pnl_sign = "+" if pnl >= 0 else ""
        print(
            f"  {r['symbol']:<8} {r['side']:<8} {r['size']:>10.4f} "
            f"{r['entry']:>12,.2f} {r['fill']:>12,.2f} {pnl_sign}${pnl:,.2f}"
        )

    count = len(results)
    pnl_sign = "+" if total_pnl >= 0 else ""
    print(
        f"\n  Closed {count} positions | "
        f"Total PnL: {pnl_sign}${total_pnl:,.2f} | "
        f"Fees: ${total_fees:,.2f}"
    )
    print(f"{sep}\n")


# ── Core logic ───────────────────────────────────────────────────────────────


async def _close_positions(client, positions: dict, is_dry_run: bool) -> list[dict]:
    """Close each open position with a reduce-only market order.

    Returns a list of result dicts for reporting.
    """
    results: list[dict] = []

    for sym, pos in positions.items():
        try:
            close_side = (
                OrderSide.SELL if pos.side.value == "long" else OrderSide.BUY
            )
            close_side_value = close_side.value

            if is_dry_run:
                fill_price = pos.current_price
                order_id = "dry-run-simulated"
            else:
                order = await client.place_order(
                    sym, close_side, pos.size, OrderType.MARKET, reduce_only=True
                )
                fill_price = order.avg_fill_price
                order_id = order.id

                # If fill came back zero, retry once after 1s
                if fill_price == 0.0:
                    print(f"  [retry] {sym}: fill price was 0, retrying in 1s...")
                    await asyncio.sleep(1)
                    try:
                        order = await client.place_order(
                            sym,
                            close_side,
                            pos.size,
                            OrderType.MARKET,
                            reduce_only=True,
                        )
                        fill_price = order.avg_fill_price
                        order_id = order.id
                    except Exception as retry_err:
                        print(f"  [error] {sym}: retry failed — {retry_err}")
                        continue

            # Compute PnL
            if pos.side.value == "long":
                pnl = (fill_price - pos.entry_price) * pos.size
            else:
                pnl = (pos.entry_price - fill_price) * pos.size

            fee = pos.size * fill_price * TAKER_FEE_BPS

            results.append(
                {
                    "symbol": sym,
                    "side": close_side_value,
                    "size": pos.size,
                    "entry": pos.entry_price,
                    "fill": fill_price,
                    "pnl": pnl,
                    "fee": fee,
                    "order_id": order_id,
                    "dry_run": is_dry_run,
                }
            )

            pnl_sign = "+" if pnl >= 0 else ""
            print(
                f"  Closed {sym}: {pos.size:.4f} {pos.side.value} @ "
                f"{fill_price:,.2f}  PnL: {pnl_sign}${pnl:,.2f}"
            )
        except Exception as err:
            print(f"  [error] {sym}: close failed — {err}")
            print(f"  Continuing with remaining positions...")

    return results


async def _record_to_db(
    db,
    results: list[dict],
    is_dry_run: bool,
    wallet_address: str,
    equity: float,
) -> None:
    """Insert Trade and RiskEvent records into the database."""
    await db.connect()

    for r in results:
        await db.insert_trade(
            Trade(
                id=None,
                timestamp=datetime.now(timezone.utc),
                symbol=r["symbol"],
                side=r["side"],
                size=r["size"],
                price=r["fill"],
                fee=r["fee"],
                pnl=r["pnl"],
                strategy_signal="kill_switch",
                order_id=r["order_id"],
                dry_run=is_dry_run,
                snapshot_id=None,
            )
        )

    closed_symbols = [r["symbol"] for r in results]
    await db.insert_risk_event(
        RiskEvent(
            id=None,
            timestamp=datetime.now(timezone.utc),
            event_type="manual_kill_switch",
            details=f"Killed {len(results)} positions: {', '.join(closed_symbols)}",
            action_taken="all_positions_closed",
        )
    )


async def main() -> None:
    # 1. Local-only safety check
    _check_local_only()

    # 2. Parse args
    parser = _build_parser()
    args = parser.parse_args()
    is_dry_run: bool = args.dry_run
    skip_confirm: bool = args.confirm

    # 3. Create exchange client
    client = create_exchange()

    db = None
    try:
        # 4. Fetch account state
        account_state = await client.get_account_state()

        # 5. Fetch open orders
        open_orders = await client.get_open_orders()

        # 6. Print summary of what's open
        _print_banner(is_dry_run)
        _print_summary_header(
            wallet_address=account_state.wallet_address,
            equity=account_state.total_equity,
            positions=account_state.positions,
            open_orders=open_orders,
            is_dry_run=is_dry_run,
        )

        if not account_state.positions and not open_orders:
            print("  Nothing to close — account is flat with no open orders.\n")
            return

        # 7. Get confirmation
        if not skip_confirm and not is_dry_run:
            try:
                answer = input("  Close all positions and cancel all orders? [yes/no] ")
                if answer.strip().lower() != "yes":
                    print("  Aborted.\n")
                    return
            except EOFError:
                print("  Aborted (no stdin available — use --confirm).\n")
                return

        # 8. Cancel ALL open orders
        if open_orders:
            print(f"  Cancelling {len(open_orders)} open orders...")
            if not is_dry_run:
                await client.cancel_all_orders()
                print("  All orders cancelled.")
            else:
                print("  [dry-run] Would cancel all orders.")
        else:
            print("  No open orders to cancel.")

        # 9. Re-fetch account state
        account_state = await client.get_account_state()

        if not account_state.positions:
            print("  No positions to close after order cancellation.\n")
            return

        # 10. Close each position
        print(f"\n  Closing {len(account_state.positions)} positions...")
        results = await _close_positions(
            client, account_state.positions, is_dry_run
        )

        # 11. Final re-fetch to confirm all flat
        if not is_dry_run:
            final_state = await client.get_account_state()
            remaining = final_state.positions
            if remaining:
                print(
                    f"\n  WARNING: {len(remaining)} position(s) still open: "
                    f"{', '.join(remaining.keys())}"
                )
            else:
                print("\n  All positions closed successfully.")
        else:
            print("\n  [dry-run] Skipping final verification.")

        # 12. Record to database
        if results:
            try:
                db = create_repository()
                await _record_to_db(
                    db,
                    results,
                    is_dry_run=is_dry_run,
                    wallet_address=account_state.wallet_address,
                    equity=account_state.total_equity,
                )
                print("  Recorded in database.")
            except Exception as db_err:
                print(f"  WARNING: database recording failed — {db_err}")
                print("  Positions were still closed. Check logs manually.")

        # 13. Print final summary
        for r in results:
            r["wallet"] = account_state.wallet_address
            r["equity"] = account_state.total_equity
        _print_closure_report(results)

    finally:
        await client.close()
        if db is not None:
            try:
                await db.close()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())

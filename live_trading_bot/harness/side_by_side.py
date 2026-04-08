#!/usr/bin/env python3
"""Run dry-run and live bot instances side-by-side, comparing signals.

Usage:
    cd live_trading_bot
    python harness/side_by_side.py --dry-runs 2 --live-runs 1
    python harness/side_by_side.py --duration 5m          # fixed duration (local testing)

Requires HYPERLIQUID_PRIVATE_KEY in environment or .env file.
Each instance gets its own DB and log file in a temp directory.

When --duration is omitted the harness runs until SIGTERM/SIGINT (suitable
for Railway deployment where the process should stay up indefinitely).
"""

import argparse
import asyncio
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path


def _stream_output(proc, prefix):
    """Read subprocess stdout line-by-line and print with prefix. Runs in a thread."""
    for line in iter(proc.stdout.readline, b""):
        text = line.decode(errors="replace").rstrip()
        if text:
            print(f"[{prefix}] {text}", flush=True)
    proc.stdout.close()


def _query_live_equity_sync() -> float | None:
    """Query the live Hyperliquid account equity. Returns None on failure.

    Used to auto-match dry-run starting capital to the real live account
    so that comparisons between dry and live aren't distorted by 10x
    different position sizes (the strategy is equity-aware).

    The script is invoked as `python live_trading_bot/harness/side_by_side.py`,
    which does NOT put the repo root on sys.path automatically. The lazy
    imports below need `live_trading_bot` to be importable as a top-level
    package, so we add the repo root to sys.path before the imports. This
    is the same trick the bot.py invocation handles via uv's package
    discovery, but for our standalone script we have to do it ourselves.
    """
    try:
        # repo_root = .../live_trading_bot/harness/../..  =  .../  (repo root)
        repo_root = str(Path(__file__).resolve().parent.parent.parent)
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)

        # Imported lazily so this script still loads if HL deps are missing
        from live_trading_bot.config import get_private_key
        from live_trading_bot.exchange.hyperliquid import HyperliquidClient

        async def _q() -> float:
            client = HyperliquidClient(private_key=get_private_key())
            try:
                state = await client.get_account_state()
                return float(state.total_equity)
            finally:
                await client.close()

        return asyncio.run(_q())
    except Exception as e:
        print(f"[harness] Failed to query live equity: {e}", flush=True)
        return None


def parse_duration(s: str) -> int:
    """Parse '5m', '300s', '1h' to seconds."""
    s = s.strip().lower()
    if s.endswith("m"):
        return int(s[:-1]) * 60
    elif s.endswith("h"):
        return int(s[:-1]) * 3600
    elif s.endswith("s"):
        return int(s[:-1])
    return int(s)


def extract_signals(db_path: str) -> list[dict]:
    """Extract signals from a bot's SQLite database."""
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT timestamp, symbol, target_position, current_position "
        "FROM signals ORDER BY timestamp, symbol"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def extract_trades(db_path: str) -> list[dict]:
    """Extract trades from a bot's SQLite database."""
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT timestamp, symbol, side, size, price "
        "FROM trades ORDER BY timestamp, symbol"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def signal_direction(target_pos: float) -> str:
    if target_pos > 1.0:
        return "LONG"
    elif target_pos < -1.0:
        return "SHORT"
    return "FLAT"


def compare_instances(instances: list[dict]):
    """Compare signals across all instances."""
    print("\n" + "=" * 70)
    print("COMPARISON RESULTS")
    print("=" * 70)

    for inst in instances:
        signals = inst["signals"]
        trades = inst["trades"]
        mode = "DRY-RUN" if inst["dry_run"] else "LIVE"
        print(
            f"\n  {inst['name']} ({mode}): "
            f"{len(signals)} signals, {len(trades)} trades"
        )
        by_symbol = defaultdict(list)
        for s in signals:
            by_symbol[s["symbol"]].append(s)
        for sym, sigs in sorted(by_symbol.items()):
            directions = [signal_direction(s["target_position"]) for s in sigs]
            print(f"    {sym}: {len(sigs)} signals — {', '.join(directions)}")

    if len(instances) < 2:
        print("\nNeed at least 2 instances to compare.")
        return

    print("\n" + "-" * 70)
    print("PAIRWISE SIGNAL AGREEMENT")
    print("-" * 70)

    for i in range(len(instances)):
        for j in range(i + 1, len(instances)):
            _compare_pair(instances[i], instances[j])


def _compare_pair(a: dict, b: dict):
    a_mode = "DRY" if a["dry_run"] else "LIVE"
    b_mode = "DRY" if b["dry_run"] else "LIVE"
    print(f"\n  {a['name']}({a_mode}) vs {b['name']}({b_mode}):")

    a_sigs = a["signals"]
    b_sigs = b["signals"]

    if not a_sigs and not b_sigs:
        print("    Both produced 0 signals.")
        return

    if not a_sigs or not b_sigs:
        print(f"    {a['name']}: {len(a_sigs)} signals, {b['name']}: {len(b_sigs)} signals")
        print("    Cannot compare — one instance produced no signals.")
        return

    def group_key(sig):
        ts = sig["timestamp"]
        return (ts[:16], sig["symbol"])

    a_by_key = {group_key(s): s for s in a_sigs}
    b_by_key = {group_key(s): s for s in b_sigs}

    all_keys = sorted(set(a_by_key.keys()) | set(b_by_key.keys()))
    agree = disagree = a_only = b_only = 0
    divergences = []

    for key in all_keys:
        sa = a_by_key.get(key)
        sb = b_by_key.get(key)

        if sa and sb:
            dir_a = signal_direction(sa["target_position"])
            dir_b = signal_direction(sb["target_position"])
            if dir_a == dir_b:
                agree += 1
            else:
                disagree += 1
                if len(divergences) < 5:
                    divergences.append(
                        f"    {key[0]} {key[1]}: {a['name']}={dir_a}({sa['target_position']:.0f}) "
                        f"vs {b['name']}={dir_b}({sb['target_position']:.0f})"
                    )
        elif sa:
            a_only += 1
        else:
            b_only += 1

    total = agree + disagree
    pct = (agree / total * 100) if total > 0 else 0

    print(f"    Direction agreement: {agree}/{total} ({pct:.0f}%)")
    print(f"    Signals only in {a['name']}: {a_only}")
    print(f"    Signals only in {b['name']}: {b_only}")
    if divergences:
        print("    First divergences:")
        for d in divergences:
            print(d)


def main():
    parser = argparse.ArgumentParser(
        description="Run dry-run and live bot instances side-by-side"
    )
    parser.add_argument(
        "--duration", default=None,
        help="How long to run (e.g. 5m, 1h). Omit to run until SIGTERM.",
    )
    parser.add_argument(
        "--dry-runs", type=int, default=2, help="Number of dry-run instances"
    )
    parser.add_argument(
        "--live-runs", type=int, default=1, help="Number of live instances"
    )
    parser.add_argument(
        "--interval", default=None,
        help="Bar interval (e.g. 1m, 15m). If omitted, inherits BAR_INTERVAL "
             "from the environment, falling back to bot.py's default. Pass "
             "explicitly only when you want to override the env var.",
    )
    args = parser.parse_args()

    duration_secs = parse_duration(args.duration) if args.duration else None
    bot_dir = Path(__file__).resolve().parent.parent
    bot_script = bot_dir / "bot.py"

    if not bot_script.exists():
        print(f"ERROR: bot.py not found at {bot_script}")
        sys.exit(1)

    work_dir = tempfile.mkdtemp(prefix="side_by_side_")
    mode_str = f"{duration_secs}s" if duration_secs else "indefinite (until SIGTERM)"
    print(f"Work directory: {work_dir}")
    print(f"Duration: {mode_str}")
    print(f"Instances: {args.dry_runs} dry-run + {args.live_runs} live")
    interval_display = args.interval or f"{os.environ.get('BAR_INTERVAL', 'bot.py default')} (from env)"
    print(f"Interval: {interval_display}")

    # Auto-match dry capital to live equity unless the user explicitly set
    # DRY_RUN_INITIAL_CAPITAL in the environment. The point: the strategy is
    # equity-aware (position sizes scale with equity), so dry and live must
    # start with the same capital to produce comparable behavior. Without
    # this, dry runs at the default $10k while live runs at whatever the
    # real account has — making any signal/PnL comparison meaningless.
    dry_capital_override: float | None = None
    if args.dry_runs > 0:
        if "DRY_RUN_INITIAL_CAPITAL" in os.environ:
            print(
                f"Dry capital: ${float(os.environ['DRY_RUN_INITIAL_CAPITAL']):.2f} "
                f"(from env DRY_RUN_INITIAL_CAPITAL — explicit override, "
                f"NOT auto-matched to live)"
            )
        else:
            live_equity = _query_live_equity_sync()
            if live_equity is not None and live_equity > 0:
                dry_capital_override = live_equity
                print(
                    f"Dry capital: ${dry_capital_override:.2f} "
                    f"(auto-matched to live account equity — set "
                    f"DRY_RUN_INITIAL_CAPITAL in env to override)"
                )
            else:
                print(
                    "Dry capital: bot.py default "
                    "(could not query live equity, see error above)"
                )
    print()

    instances = []

    for i in range(args.dry_runs + args.live_runs):
        is_dry = i < args.dry_runs
        name = f"dry_{i}" if is_dry else f"live_{i - args.dry_runs}"

        inst_dir = os.path.join(work_dir, name)
        os.makedirs(inst_dir, exist_ok=True)
        db_path = os.path.join(inst_dir, "bot.db")
        log_path = os.path.join(inst_dir, "bot.log")

        env = os.environ.copy()
        env["DRY_RUN"] = "true" if is_dry else "false"
        env["DB_PATH"] = db_path
        env["LOG_PATH"] = log_path
        # Only override BAR_INTERVAL if --interval was passed explicitly.
        # Otherwise inherit from the environment (e.g. Railway env var) so
        # the harness doesn't silently force 1m bars when the live bot is
        # configured for 15m.
        if args.interval:
            env["BAR_INTERVAL"] = args.interval
        # Set dry capital override on dry instances if auto-matching is on.
        # Live instances always use real equity from the exchange and ignore
        # this env var.
        if is_dry and dry_capital_override is not None:
            env["DRY_RUN_INITIAL_CAPITAL"] = str(dry_capital_override)
        env["ALERT_ON_TRADE"] = "true"
        env["ALERT_INSTANCE_NAME"] = name

        mode = "DRY-RUN" if is_dry else "LIVE"
        print(f"  Starting {name} ({mode})...")
        proc = subprocess.Popen(
            [sys.executable, str(bot_script)],
            cwd=str(bot_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        t = threading.Thread(target=_stream_output, args=(proc, name), daemon=True)
        t.start()

        instances.append({
            "name": name,
            "dry_run": is_dry,
            "db_path": db_path,
            "log_path": log_path,
            "proc": proc,
        })

    _shutdown_requested = False

    def _handle_sigterm(signum, frame):
        nonlocal _shutdown_requested
        _shutdown_requested = True
        print("\nSIGTERM received, shutting down...")

    signal.signal(signal.SIGTERM, _handle_sigterm)

    print("\nAll instances started.")
    print("(Signals may take a minute to appear after first bar completes)\n")

    try:
        elapsed = 0
        while True:
            if _shutdown_requested:
                break
            if duration_secs and elapsed >= duration_secs:
                break
            time.sleep(1)
            elapsed += 1
            # Check for early exits
            for inst in instances:
                rc = inst["proc"].poll()
                if rc is not None and rc != 0:
                    print(f"  WARNING: {inst['name']} exited (code {rc})")
            # Progress every 30s
            if elapsed % 30 == 0:
                mins = elapsed // 60
                secs = elapsed % 60
                counts = []
                for inst in instances:
                    sigs = extract_signals(inst["db_path"])
                    trades = extract_trades(inst["db_path"])
                    last = sigs[-1]["timestamp"][11:16] if sigs else "--:--"
                    counts.append(f"{inst['name']}={len(sigs)}sig/{len(trades)}trd(last:{last})")
                print(f"  [{mins}m{secs:02d}s] {', '.join(counts)}", flush=True)
    except KeyboardInterrupt:
        pass

    print("\nStopping all instances...")
    for inst in instances:
        if inst["proc"].poll() is None:
            inst["proc"].send_signal(signal.SIGINT)

    for inst in instances:
        try:
            inst["proc"].wait(timeout=15)
        except subprocess.TimeoutExpired:
            inst["proc"].kill()
            inst["proc"].wait()

    # No position cleanup — bot.py shutdown preserves stops and positions
    # across restarts.  Use `bot.py --close-all` for deliberate teardowns.

    print("\nAll instances stopped.\n")

    for inst in instances:
        inst["signals"] = extract_signals(inst["db_path"])
        inst["trades"] = extract_trades(inst["db_path"])

    compare_instances(instances)

    # Print log file locations for debugging
    print("\n" + "-" * 70)
    print("LOG FILES (for debugging):")
    for inst in instances:
        print(f"  {inst['name']}: {inst['log_path']}")
    print(f"\nWork directory: {work_dir}")
    print("=" * 70)


if __name__ == "__main__":
    main()

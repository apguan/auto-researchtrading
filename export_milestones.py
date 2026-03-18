#!/usr/bin/env python3
"""Export equity curves at key autoresearch milestones.

Checks out each milestone commit, runs the backtest, saves the equity CSV,
then restores the original branch.
"""
import csv
import subprocess
import sys
from datetime import datetime, timedelta

MILESTONES = [
    # (commit, label, description)
    ("d779d69", "baseline",  "Exp 0: Baseline momentum (score 2.7)"),
    ("edabd44", "exp15",     "Exp 15: 4/5 ensemble + cooldown (score 8.4)"),
    ("31600ce", "exp46",     "Exp 46: Remove strength scaling (score 13.5)"),
    ("61d4b77", "exp72",     "Exp 72: RSI period 8 (score 19.7)"),
    ("7245f22", "exp102",    "Exp 102: Final strategy (score 20.6)"),
]

def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  ERROR: {r.stderr.strip()}")
    return r

def export_equity_for_commit(commit, label, desc):
    """Checkout commit, run backtest, save equity CSV, return to original."""
    print(f"\n{'='*60}")
    print(f"  {desc}")
    print(f"  Commit: {commit}")
    print(f"{'='*60}")

    # Checkout the commit's strategy.py only (don't switch whole branch)
    run(f"git show {commit}:strategy.py > /tmp/strategy_milestone.py")

    # Copy milestone strategy in, keeping everything else current
    import shutil
    shutil.copy("/tmp/strategy_milestone.py", "strategy.py")

    # Run backtest with this strategy
    try:
        # Re-import with fresh module
        import importlib
        if 'strategy' in sys.modules:
            del sys.modules['strategy']
        from prepare import load_data, run_backtest
        from strategy import Strategy

        strategy = Strategy()
        data = load_data("val")
        result = run_backtest(strategy, data)

        # Write CSV
        outfile = f"equity_curve_{label}.csv"
        start = datetime(2024, 7, 1)
        timestamps = [start + timedelta(hours=i) for i in range(len(result.equity_curve))]

        with open(outfile, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "equity"])
            for ts, eq in zip(timestamps, result.equity_curve):
                w.writerow([ts.strftime("%Y-%m-%d %H:%M"), f"{eq:.2f}"])

        print(f"  ✓ Exported {len(result.equity_curve)} points → {outfile}")
        print(f"    Start: ${result.equity_curve[0]:,.2f}")
        print(f"    End:   ${result.equity_curve[-1]:,.2f}")
        print(f"    Return: {result.total_return_pct:.1f}%")
        print(f"    Sharpe: {result.sharpe:.2f}")
        return True

    except Exception as e:
        print(f"  ✗ Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    # Save current strategy.py
    run("cp strategy.py strategy.py.bak")

    try:
        for commit, label, desc in MILESTONES:
            export_equity_for_commit(commit, label, desc)
    finally:
        # Restore original strategy.py
        run("mv strategy.py.bak strategy.py")
        print("\n✓ Restored original strategy.py")

    print("\n✅ All milestone equity curves exported!")


if __name__ == "__main__":
    main()

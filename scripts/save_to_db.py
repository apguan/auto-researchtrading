#!/usr/bin/env python3
"""Parse backtest output and save experiment result to param_snapshots.

Usage:
    python scripts/save_to_db.py run.log "exp1: widened RSI bands"
    python scripts/save_to_db.py run.log "exp1: widened RSI bands" PASS
    python scripts/save_to_db.py run.log "exp1: widened RSI bands" FAIL
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from _env import load_env

load_env()


FIELDS = {
    "score": r"^score:\s+([-\d.]+)",
    "sharpe": r"^sharpe:\s+([-\d.]+)",
    "total_return_pct": r"^total_return_pct:\s+([-\d.]+)",
    "max_drawdown_pct": r"^max_drawdown_pct:\s+([-\d.]+)",
    "num_trades": r"^num_trades:\s+(\d+)",
    "win_rate_pct": r"^win_rate_pct:\s+([-\d.]+)",
    "profit_factor": r"^profit_factor:\s+([-\d.]+)",
}


def parse_run_log(path: str) -> dict:
    text = open(path).read()
    metrics = {}
    for name, pattern in FIELDS.items():
        m = re.search(pattern, text, re.MULTILINE)
        if not m:
            print(f"WARNING: could not parse '{name}' from {path}", file=sys.stderr)
            metrics[name] = 0.0
        else:
            val = float(m.group(1))
            metrics[name] = int(val) if name == "num_trades" else val
    return metrics


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/save_to_db.py <run.log> [description] [PASS|FAIL]", file=sys.stderr)
        sys.exit(1)

    log_path = sys.argv[1]
    description = sys.argv[2] if len(sys.argv) > 2 else ""
    status = sys.argv[3] if len(sys.argv) > 3 else "PASS"

    metrics = parse_run_log(log_path)
    print(f"Parsed: {metrics}", file=sys.stderr)

    from strategy import save_experiment_to_db

    ok = save_experiment_to_db(
        score=metrics["score"],
        sharpe=metrics["sharpe"],
        total_return_pct=metrics["total_return_pct"],
        max_drawdown_pct=metrics["max_drawdown_pct"],
        num_trades=int(metrics["num_trades"]),
        win_rate_pct=metrics["win_rate_pct"],
        profit_factor=metrics["profit_factor"],
        description=description,
        status=status,
    )

    if ok:
        print(f"Saved to DB: score={metrics['score']:.4f} sharpe={metrics['sharpe']:.4f}")
    else:
        print("Failed to save to DB (check SUPABASE_DB_URL)", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

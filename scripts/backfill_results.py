#!/usr/bin/env python3
"""Backfill experiment results from results.tsv into param_snapshots."""

import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import psycopg2

if "SUPABASE_DB_URL" not in os.environ:
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

RESULTS_FILE = REPO_ROOT / "results.tsv"

ACTIVE_PARAMS = (
    "SHORT_WINDOW", "MED_WINDOW", "MED2_WINDOW", "LONG_WINDOW",
    "EMA_FAST", "EMA_SLOW", "RSI_PERIOD", "RSI_BULL", "RSI_BEAR",
    "RSI_OVERBOUGHT", "RSI_OVERSOLD", "MACD_FAST", "MACD_SLOW",
    "MACD_SIGNAL", "BB_PERIOD",
    "BASE_POSITION_PCT", "VOL_LOOKBACK", "TARGET_VOL", "ATR_LOOKBACK",
    "ATR_STOP_MULT", "TAKE_PROFIT_PCT", "BASE_THRESHOLD",
    "COOLDOWN_BARS", "MIN_VOTES", "OBV_MA_PERIOD",
)


def extract_params_from_commit(sha: str) -> dict[str, float]:
    result = subprocess.run(
        ["git", "show", f"{sha}:strategy.py"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    if result.returncode != 0:
        return {}

    code = result.stdout
    params = {}
    for param in ACTIVE_PARAMS:
        pm = re.search(rf"^{param}\s*=\s*([\d.]+)", code, re.MULTILINE)
        if pm:
            params[param] = float(pm.group(1))
        else:
            params[param] = 0.0
    return params


def parse_results() -> list[dict]:
    rows = []
    text = RESULTS_FILE.read_text()
    for line in text.strip().split("\n")[1:]:
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        rows.append({
            "commit": parts[0],
            "score": float(parts[1]),
            "sharpe": float(parts[2]),
            "max_dd": float(parts[3]),
            "status": parts[4],
            "description": parts[5],
        })
    return rows


def main():
    db_url = os.environ.get("SUPABASE_DB_URL", "")
    if not db_url:
        print("ERROR: SUPABASE_DB_URL not set", file=sys.stderr)
        sys.exit(1)

    results = parse_results()
    print(f"Found {len(results)} experiments in results.tsv")

    now = datetime.now(timezone.utc).isoformat()

    conn = psycopg2.connect(db_url)
    try:
        cur = conn.cursor()

        # DB schema columns in order (excluding id, previous_snapshot_id, and unused param columns)
        insert_cols = [
            "run_date", "sweep_name", "period",
            "sharpe", "total_return_pct", "max_drawdown_pct",
            "profit_factor", "win_rate_pct", "num_trades", "ret_dd_ratio",
            "is_best",
        ]
        # Param columns that exist in DB
        param_col_names = [p.lower() for p in ACTIVE_PARAMS]
        all_insert_cols = insert_cols + param_col_names + [
            "symbol", "is_active", "description", "score", "status",
        ]
        placeholders = ", ".join(["%s"] * len(all_insert_cols))
        col_str = ", ".join(all_insert_cols)

        inserted = 0
        skipped = 0
        for i, row in enumerate(results, 1):
            sha = row["commit"]

            cur.execute(
                "SELECT id FROM param_snapshots "
                "WHERE description = %s AND score = %s AND status = %s "
                "LIMIT 1",
                (row["description"], row["score"], row["status"]),
            )
            if cur.fetchone():
                skipped += 1
                continue

            params = extract_params_from_commit(sha)
            if not params:
                print(f"  [{i}/{len(results)}] SKIP {sha[:7]}: no strategy.py", file=sys.stderr)
                skipped += 1
                continue

            ret_dd_ratio = row["score"] / row["max_dd"] if row["max_dd"] > 0 else 0.0

            values = [
                now,
                "autoresearch",
                "1h",
                row["sharpe"],
                0.0,
                row["max_dd"],
                0.0,
                0.0,
                0,
                ret_dd_ratio,
                False,
            ]
            for p in ACTIVE_PARAMS:
                values.append(params.get(p, 0.0))
            values.extend(["ALL", False, row["description"], row["score"], row["status"]])

            assert len(values) == len(all_insert_cols), \
                f"Mismatch: {len(values)} values vs {len(all_insert_cols)} cols"

            cur.execute(
                f"INSERT INTO param_snapshots ({col_str}) VALUES ({placeholders})",
                values,
            )
            inserted += 1
            tag = "PASS" if row["status"] == "PASS" else "   "
            print(f"  [{i}/{len(results)}] {tag} {sha[:7]} score={row['score']:.3f} {row['description'][:60]}")

        conn.commit()
        print(f"\nDone: {inserted} inserted, {skipped} skipped, {len(results) - inserted - skipped} failed")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

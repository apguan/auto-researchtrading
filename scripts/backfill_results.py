#!/usr/bin/env python3
"""Backfill experiment results from results.tsv into param_snapshots."""

import os
import re
import subprocess
import sys
from pathlib import Path

import psycopg2

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

RESULTS_FILE = REPO_ROOT / "results.tsv"


def extract_params_from_commit(sha: str) -> tuple[dict[str, float], list[str]]:
    result = subprocess.run(
        ["git", "show", f"{sha}:strategy.py"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    if result.returncode != 0:
        return {}, []

    code = result.stdout
    m = re.search(r"ACTIVE_PARAMS\s*=\s*\(([^)]+)\)", code, re.DOTALL)
    if not m:
        return {}, []

    active_params = [
        p.strip().strip('"').strip("'")
        for p in m.group(1).split(",")
        if p.strip() and not p.strip().startswith("#")
    ]

    params = {}
    for param in active_params:
        pm = re.search(rf"^{param}\s*=\s*([\d.]+)", code, re.MULTILINE)
        if pm:
            params[param] = float(pm.group(1))
        else:
            params[param] = 0.0
    return params, active_params


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

    conn = psycopg2.connect(db_url)
    try:
        cur = conn.cursor()

        cur.execute("DELETE FROM param_snapshots WHERE run_date >= '2026-04-01'")
        deleted = cur.rowcount
        conn.commit()
        print(f"Deleted {deleted} existing rows from today")

        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'param_snapshots' ORDER BY ordinal_position"
        )
        db_columns = {row[0] for row in cur.fetchall()}

        inserted = 0
        for row in results:
            sha = row["commit"]
            params, commit_active_params = extract_params_from_commit(sha)
            if not params:
                print(f"  SKIP {sha}: could not extract params", file=sys.stderr)
                continue

            filtered_params = [
                p for p in commit_active_params if p.lower() in db_columns
            ]
            param_cols = ", ".join(p.lower() for p in filtered_params)
            all_cols = (
                "run_date, sweep_name, period, "
                "sharpe, total_return_pct, max_drawdown_pct, "
                "profit_factor, win_rate_pct, num_trades, ret_dd_ratio, "
                "is_best, previous_snapshot_id, "
                + param_cols
                + ", symbol, is_active, description, score, status"
            )
            num_vals = 12 + len(filtered_params) + 2 + 3
            placeholders = ", ".join(["%s"] * num_vals)

            ret_dd_ratio = row["score"] / row["max_dd"] if row["max_dd"] > 0 else 0.0

            values = [
                "2026-04-02T00:00:00+00:00",
                "autoresearch",
                "1h",
                row["sharpe"],
                row["score"],
                row["max_dd"],
                0.0, 0.0, 0,
                ret_dd_ratio,
                False,
                None,
            ]
            for c in filtered_params:
                values.append(params[c])
            values.extend(["ALL", False, row["description"], row["score"], row["status"]])

            cur.execute(
                f"INSERT INTO param_snapshots ({all_cols}) VALUES ({placeholders})",
                values,
            )
            inserted += 1
            tag = "PASS" if row["status"] == "PASS" else "FAIL"
            print(f"  {inserted:3d}. {sha[:8]} score={row['score']:.2f} {tag} {row['description']}")

        conn.commit()
        print(f"\nInserted {inserted}/{len(results)} experiments")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

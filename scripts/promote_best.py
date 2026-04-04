#!/usr/bin/env python3
"""Promote the highest-scoring PASS experiment from this run's results.tsv.

Applies the winning strategy.py to feat/auto_tuning, then marks it active in DB.

Usage:
    python scripts/promote_best.py
"""

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from _env import load_env

load_env()

HARNESS_BRANCH = "feat/auto_tuning"


def _git(*args):
    r = subprocess.run(
        ["git"] + list(args),
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def _find_best_from_tsv():
    tsv_path = REPO_ROOT / "results.tsv"
    if not tsv_path.exists():
        return None

    best = None
    for line in tsv_path.read_text().splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        commit_hash = parts[0]
        score = float(parts[1])
        status = parts[4].strip()
        desc = parts[5]
        if status != "PASS":
            continue
        if best is None or score > best[1]:
            best = (commit_hash, score, desc)

    return best


def _apply_to_harness(commit_hash, score, description):
    current_branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if not current_branch:
        print("ERROR: could not determine current ref", file=sys.stderr)
        return False

    strategy_content = _git("show", f"{commit_hash}:strategy.py")
    if strategy_content is None:
        print(f"ERROR: could not read strategy.py from {commit_hash}", file=sys.stderr)
        return False

    if _git("checkout", HARNESS_BRANCH) is None:
        print(f"ERROR: could not checkout {HARNESS_BRANCH}", file=sys.stderr)
        return False

    try:
        (REPO_ROOT / "strategy.py").write_text(strategy_content)

        if _git("diff", "--quiet", "strategy.py") is not None:
            print("strategy.py unchanged — harness already has this version")
            return True

        _git("add", "strategy.py")
        result = _git("commit", "-m",
            f"promote {commit_hash}: {description} (score={score:.4f})")
        if result is None:
            print("ERROR: git commit failed", file=sys.stderr)
            return False

        print(f"Applied {commit_hash} to {HARNESS_BRANCH}")
        return True
    finally:
        _git("checkout", current_branch)


def _mark_active_in_db(commit_hash, score, description):
    db_url = os.environ.get("SUPABASE_DB_URL", "")
    if not db_url:
        print("SUPABASE_DB_URL not set — skipping DB update", file=sys.stderr)
        return

    import psycopg2

    today_start = (
        datetime.now(timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .isoformat()
    )

    try:
        with psycopg2.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE param_snapshots SET is_active = FALSE, is_best = FALSE "
                    "WHERE is_active = TRUE AND period = '1h'"
                )
                cur.execute(
                    "UPDATE param_snapshots SET is_active = TRUE, is_best = TRUE "
                    "WHERE id = ("
                    "  SELECT id FROM param_snapshots "
                    "  WHERE description = %s AND score = %s AND status = 'PASS' "
                    "  AND run_date >= %s "
                    "  ORDER BY id DESC LIMIT 1"
                    ")",
                    (description, score, today_start),
                )
                if cur.rowcount > 0:
                    conn.commit()
                    print(f"Marked best in DB: {description} (score={score:.4f})")
                else:
                    print("No matching DB row for today's best", file=sys.stderr)
    except Exception as e:
        print(f"DB update failed: {e}", file=sys.stderr)


def main():
    best = _find_best_from_tsv()
    if not best:
        print("No PASS experiments in results.tsv — nothing to promote", file=sys.stderr)
        sys.exit(0)

    commit_hash, score, description = best
    print(f"Best PASS: {commit_hash} score={score:.4f} ({description})")

    if _apply_to_harness(commit_hash, score, description):
        _mark_active_in_db(commit_hash, score, description)


if __name__ == "__main__":
    main()

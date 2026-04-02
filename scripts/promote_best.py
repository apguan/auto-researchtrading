#!/usr/bin/env python3
"""Promote the highest-scoring PASS experiment to is_active=TRUE.

Run once after all autoresearch experiments finish.
Deactivates all previous active rows, then activates only the best one.

Usage:
    python scripts/promote_best.py
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    import psycopg2

    db_url = os.environ.get("SUPABASE_DB_URL", "")
    if not db_url:
        print("ERROR: SUPABASE_DB_URL not set", file=sys.stderr)
        sys.exit(1)

    try:
        with psycopg2.connect(db_url) as conn:
            with conn.cursor() as cur:
                today_start = datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ).isoformat()
                cur.execute(
                    "SELECT id, score, description, run_date "
                    "FROM param_snapshots "
                    "WHERE sweep_name = 'autoresearch' AND status = 'PASS' "
                    "AND period = '1h' AND run_date >= %s "
                    "ORDER BY score DESC LIMIT 1",
                    (today_start,),
                )
                row = cur.fetchone()
                if not row:
                    print("No PASS experiments found — nothing to promote", file=sys.stderr)
                    sys.exit(0)

                best_id, best_score, best_desc, best_date = row
                print(f"Best experiment: id={best_id} score={best_score:.4f} date={best_date} desc={best_desc}")

                cur.execute(
                    "UPDATE param_snapshots SET is_active = FALSE, is_best = FALSE "
                    "WHERE is_active = TRUE AND period = '1h'"
                )
                deactivated = cur.rowcount
                print(f"Deactivated {deactivated} previous active row(s)")

                cur.execute(
                    "UPDATE param_snapshots SET is_active = TRUE, is_best = TRUE "
                    "WHERE id = %s",
                    (best_id,),
                )
                conn.commit()
                print(f"Promoted experiment {best_id} (score={best_score:.4f}) to active")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

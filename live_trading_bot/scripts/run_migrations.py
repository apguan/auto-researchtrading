#!/usr/bin/env python3
"""Run migrations from data_pipeline/migrations/ against Supabase.

Executes each .sql file in order by numeric prefix (001_, 002_, ...).
Idempotent — each migration should include its own guards (e.g. IF NOT EXISTS).

Usage:
    uv run python -m live_trading_bot.scripts.run_migrations
"""

import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from live_trading_bot.config import get_settings

MIGRATIONS_DIR = _REPO_ROOT / "data_pipeline" / "migrations"


async def run():
    settings = get_settings()
    if not settings.SUPABASE_DB_URL:
        print("ERROR: SUPABASE_DB_URL not set")
        sys.exit(1)

    import asyncpg

    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not sql_files:
        print("No migrations found")
        return

    conn = await asyncpg.connect(settings.SUPABASE_DB_URL)
    try:
        for f in sql_files:
            print(f"Running {f.name}...")
            sql = f.read_text()
            await conn.execute(sql)
            print(f"  OK")
        print(f"\nDone — {len(sql_files)} migration(s) applied")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())

"""Shared utilities for autoresearch scripts."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_env():
    """Load .env file into os.environ if SUPABASE_DB_URL is not already set."""
    if "SUPABASE_DB_URL" in os.environ:
        return
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)

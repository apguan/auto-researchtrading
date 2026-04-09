"""Shared utilities for data_pipeline cron scripts.

Centralises duplicated logic from cron scripts:
- Path setup (auto-runs on import)
- Logging configuration
- Candle data download with cache fallback
- Period string computation
- Best-result selection
"""

import sys
import os
import time
import logging
from pathlib import Path
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Path constants — auto-setup on import
# ---------------------------------------------------------------------------
PIPELINE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE_ROOT.parent
LIVE_BOT_ROOT = REPO_ROOT / "live_trading_bot"
LOG_DIR = PIPELINE_ROOT / "logs"# Ensure pipeline root and live_trading_bot are importable; exclude bare repo root
# so `import strategies` resolves to live_trading_bot, not repo-level modules.
for _p in (PIPELINE_ROOT, LIVE_BOT_ROOT):
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)
sys.path = [p for p in sys.path if Path(p).resolve() != REPO_ROOT]
# Append (not insert) REPO_ROOT so `import constants` resolves, while
# `import backtest` still hits data_pipeline/backtest/ (PIPELINE_ROOT is at front).
sys.path.append(str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging(name: str, log_prefix: str) -> logging.Logger:
    """Configure console (INFO) + file (DEBUG) logging. Returns configured logger."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    log_file = LOG_DIR / f"{log_prefix}_{date_str}.log"

    log = logging.getLogger(name)
    if log.handlers:  # already configured (e.g. re-import in interactive session)
        return log
    log.setLevel(logging.DEBUG)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    )
    log.addHandler(ch)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(fh)

    return log


# ---------------------------------------------------------------------------
# Data download
# ---------------------------------------------------------------------------
def download_data(
    hours_back: int = 1300,
    interval: str = "15m",
    logger: logging.Logger | None = None,
) -> dict:
    """Download candle data with cache fallback.

    Validates that downloaded data covers the requested range; if not, clears
    stale cache entries and retries once before falling back to whatever is cached.
    """
    from backtest.backtest_interval import (
        download_all_data,
        load_data,
        cache_data_dir,
    )

    _info = logger.info if logger else lambda *a, **kw: None
    _warn = logger.warning if logger else lambda *a, **kw: None
    _err = logger.error if logger else lambda *a, **kw: None

    needed_start_ms = int(time.time() * 1000) - (hours_back * 3600 * 1000)

    def _covers(data: dict) -> bool:
        if not data:
            return False
        for sym, df in data.items():
            if df["timestamp"].min() > needed_start_ms + 3600 * 1000:
                return False
        return True

    # Primary: fresh download
    try:
        _info("Downloading %s candles (hours_back=%d)...", interval, hours_back)
        data = download_all_data(hours_back=hours_back, interval=interval)
        if data and _covers(data):
            for sym, df in data.items():
                _info("  %s: %d bars", sym, len(df))
            return data
        # Download succeeded but range is insufficient — clear stale cache & retry
        if data:
            _warn("Cached data has insufficient range — forcing re-download")
            try:
                from backtest.backtest_interval import SYMBOLS

                cdir = cache_data_dir(interval)
                for sym in SYMBOLS:
                    fp = os.path.join(cdir, f"{sym}_{interval}.parquet")
                    if os.path.exists(fp):
                        os.remove(fp)
            except Exception:
                pass
            data = download_all_data(hours_back=hours_back, interval=interval)
            if data:
                for sym, df in data.items():
                    _info("  %s: %d bars (re-downloaded)", sym, len(df))
                return data
    except Exception as exc:
        _err("download_all_data failed: %s — trying cache fallback", exc)

    # Fallback: load from cache
    try:
        cdir = cache_data_dir(interval)
        data = load_data(interval=interval, data_dir=cdir)
        if data:
            for sym, df in data.items():
                _info("  %s: %d bars (cached)", sym, len(df))
            return data
    except Exception as exc:
        _err("Cache fallback also failed: %s", exc)

    return {}


# ---------------------------------------------------------------------------
# Period computation
# ---------------------------------------------------------------------------
def compute_period(data: dict) -> str:
    """Return 'YYYY-MM-DD_YYYY-MM-DD' from data timestamps."""
    starts, ends = [], []
    for df in data.values():
        ts = df["timestamp"]
        starts.append(int(ts.iloc[0]))
        ends.append(int(ts.iloc[-1]))
    if not starts:
        return ""
    fmt = "%Y-%m-%d"
    s = datetime.fromtimestamp(min(starts) / 1000, tz=timezone.utc).strftime(fmt)
    e = datetime.fromtimestamp(max(ends) / 1000, tz=timezone.utc).strftime(fmt)
    return f"{s}_{e}"


# ---------------------------------------------------------------------------
# Best result selection
# ---------------------------------------------------------------------------
def find_best_result(results: list[dict]) -> dict | None:
    """Select best result: positive return, positive DD, >= 10 trades, highest score."""
    valid = [
        r
        for r in results
        if r.get("total_return_pct", 0) > 0
        and r.get("max_drawdown_pct", 0) > 0
        and r.get("num_trades", 0) >= 10
    ]
    if not valid:
        return None
    valid.sort(key=lambda x: x.get("_score", 0), reverse=True)
    return valid[0]


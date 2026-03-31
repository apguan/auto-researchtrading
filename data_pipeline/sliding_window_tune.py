#!/usr/bin/env python3
"""Sliding Window Parameter Optimization

Runs daily_tune-style optimisation for each day over the past N days.
Downloads 15m candle data once (enough for all windows), slices into daily
windows, and runs full optimisation in parallel across workers.

Results are saved to JSON and the database as inactive snapshots (backtest
reference only — they will not become active parameters).

Usage:
    python sliding_window_tune.py
    python sliding_window_tune.py --workers 4
    python sliding_window_tune.py --days 7
    python sliding_window_tune.py --subsample 4
"""

import sys
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from concurrent.futures import as_completed
from dotenv import load_dotenv

from common import (
    setup_logging,
    compute_period,
    find_best_result,
    save_snapshots_to_db,
    run_optimization_pipeline,
    RESULTS_DIR,
)
from data_pipeline.pool import (
    get_pool,
    initialize as pool_initialize,
    shutdown as pool_shutdown,
    optimal_worker_count,
)

logger = logging.getLogger("sliding_window_tune")  # noqa: F821

WINDOW_DAYS = 30
HOURS_BACK = 1080
INTERVAL = "15m"


def slice_data_for_window(
    full_data: dict, window_end_ms: int, lookback_hours: int
) -> dict:
    start_ms = window_end_ms - (lookback_hours * 3600 * 1000)
    sliced = {}
    for symbol, df in full_data.items():
        mask = (df["timestamp"] >= start_ms) & (df["timestamp"] <= window_end_ms)
        window_df = df.loc[mask].copy().reset_index(drop=True)
        if len(window_df) > 0:
            sliced[symbol] = window_df
    return sliced


def _run_window_worker(args: tuple) -> dict:
    """Subprocess worker: run optimisation for one window."""
    window_date_str, sliced_data, window_idx, subsample_factor = args

    # Per-worker file logger for diagnostics
    _worker_log_path = os.path.join(
        os.path.dirname(__file__), "logs", f"worker_{window_date_str}.log"
    )
    os.makedirs(os.path.dirname(_worker_log_path), exist_ok=True)
    _wlog = logging.getLogger(f"swt.worker.{window_date_str}")
    _wlog.setLevel(logging.DEBUG)
    _wlog.handlers.clear()
    _wlog.propagate = False
    _fh = logging.FileHandler(_worker_log_path, mode="w")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    _wlog.addHandler(_fh)

    _wlog.info(
        "Worker START window=%s idx=%d bars=%d subsample=%d",
        window_date_str,
        window_idx,
        sum(len(df) for df in sliced_data.values()),
        subsample_factor,
    )

    old_stdout = sys.stdout
    _stdout_path = os.path.join(
        os.path.dirname(__file__), "logs", f"worker_{window_date_str}_stdout.log"
    )
    _stdout_file = open(_stdout_path, "w")
    sys.stdout = _stdout_file
    try:
        _wlog.info("Calling run_optimization_pipeline...")
        t_entry = time.time()
        _, all_results, _ = run_optimization_pipeline(
            sliced_data,
            n_workers=1,  # parallelism is at window level, not within
            subsample=subsample_factor,
            skip_oos=True,
            logger=_wlog,
        )
        _wlog.info(
            "Pipeline returned %d results in %.1fs",
            len(all_results),
            time.time() - t_entry,
        )

        for r in all_results:
            r.pop("equity_curve", None)
            r.pop("trade_log", None)
            r["window_date"] = window_date_str
            r["window_idx"] = window_idx

        best = find_best_result(all_results)
        period = compute_period(sliced_data)

        _wlog.info("Worker DONE best_score=%.2f", best.get("_score", 0) if best else 0)

        return {
            "window_date": window_date_str,
            "window_idx": window_idx,
            "num_results": len(all_results),
            "total_bars": sum(len(df) for df in sliced_data.values()),
            "results": all_results,
            "best": best,
            "period": period,
        }
    except Exception as exc:
        _wlog.exception("Worker FAILED: %s", exc)
        return {
            "window_date": window_date_str,
            "window_idx": window_idx,
            "num_results": 0,
            "total_bars": sum(len(df) for df in sliced_data.values()),
            "results": [],
            "best": None,
            "error": str(exc),
        }
    finally:
        sys.stdout = old_stdout
        _stdout_file.close()
        for h in _wlog.handlers[:]:
            h.close()
            _wlog.removeHandler(h)


def save_results_json(all_window_results: list[dict]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filepath = RESULTS_DIR / f"sliding_window_{date_str}.json"

    output = []
    for wr in all_window_results:
        entry = {
            "window_date": wr["window_date"],
            "window_idx": wr["window_idx"],
            "total_bars": wr["total_bars"],
            "num_results": wr["num_results"],
            "error": wr.get("error"),
            "best": None,
            "top5": [],
        }
        if wr.get("best"):
            b = wr["best"]
            entry["best"] = {
                k: b.get(k, 0) if k != "sweep_name" else b.get(k, "")
                for k in (
                    "sweep_name",
                    "params",
                    "sharpe",
                    "total_return_pct",
                    "max_drawdown_pct",
                    "_score",
                    "profit_factor",
                    "win_rate_pct",
                    "num_trades",
                )
            }
        sorted_results = sorted(
            wr["results"], key=lambda x: x.get("_score", 0), reverse=True
        )
        for r in sorted_results[:5]:
            entry["top5"].append(
                {
                    k: r.get(k, 0)
                    if k not in ("sweep_name", "params")
                    else r.get(k, "")
                    for k in (
                        "sweep_name",
                        "params",
                        "sharpe",
                        "total_return_pct",
                        "max_drawdown_pct",
                        "_score",
                        "profit_factor",
                        "win_rate_pct",
                        "num_trades",
                    )
                }
            )
        output.append(entry)

    tmp_path = filepath.with_suffix(".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(output, fh, indent=2)
            fh.write("\n")
        os.rename(tmp_path, filepath)
        logger.info("Results saved to: %s", filepath)
    except Exception as exc:
        logger.error("Failed to write JSON: %s", exc)
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def print_summary(
    all_window_results: list[dict], elapsed: float, n_workers: int
) -> None:
    logger.info("=" * 70)
    logger.info("SLIDING WINDOW TUNE SUMMARY")
    logger.info("=" * 70)
    logger.info("Windows:    %d", len(all_window_results))
    logger.info("Workers:    %d", n_workers)
    logger.info("Wall time:  %.1fs", elapsed)

    total_results = sum(wr["num_results"] for wr in all_window_results)
    logger.info("Total optimisation results: %d", total_results)

    for wr in sorted(all_window_results, key=lambda x: x["window_idx"]):
        best = wr.get("best")
        if best:
            bp = best.get("params", {})
            logger.info(
                "  Window %s: %d results, best Score=%.2f Sharpe=%.4f Return=%.2f%% DD=%.2f%% Trades=%d",
                wr["window_date"],
                wr["num_results"],
                best.get("_score", 0),
                best.get("sharpe", 0),
                best.get("total_return_pct", 0),
                best.get("max_drawdown_pct", 0),
                best.get("num_trades", 0),
            )
            from backtest.tune_15m import DEFAULTS

            changed = {k: v for k, v in bp.items() if DEFAULTS.get(k) != v}
            if changed:
                logger.info(
                    "    Changed: %s",
                    ", ".join(f"{k}={v}" for k, v in sorted(changed.items())),
                )
        else:
            logger.info(
                "  Window %s: %d results, no viable best",
                wr["window_date"],
                wr["num_results"],
            )

    logger.info("=" * 70)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Sliding Window Parameter Optimization"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=WINDOW_DAYS,
        help=f"Days back (default: {WINDOW_DAYS})",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=optimal_worker_count(),
        help=f"Parallel workers (default: {optimal_worker_count()})",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=HOURS_BACK,
        help=f"Lookback hours per window (default: {HOURS_BACK})",
    )
    parser.add_argument(
        "--subsample", type=int, default=1, help="Take every Nth bar (default=1)"
    )
    args = parser.parse_args()

    t_start = time.time()
    _env_path = os.path.join(
        os.path.dirname(__file__), os.pardir, "live_trading_bot", ".env"
    )
    load_dotenv(_env_path)
    setup_logging("sliding_window_tune", "sliding_window_tune")

    logger.info("=" * 70)
    logger.info(
        "SLIDING WINDOW PARAMETER OPTIMIZATION — %s",
        datetime.now(timezone.utc).isoformat(),
    )
    logger.info("=" * 70)
    logger.info(
        "Config: %d days, %d workers, %dh lookback per window",
        args.days,
        args.workers,
        args.lookback,
    )

    try:
        from backtest.backtest_interval import download_all_data

        total_hours = args.days * 24 + args.lookback

        logger.info("Step 1/4: Downloading fresh data (%d hours back)...", total_hours)
        t0 = time.time()
        full_data = download_all_data(hours_back=total_hours, interval=INTERVAL)
        if not full_data:
            logger.error("No data available after download — aborting")
            sys.exit(1)

        total_bars = sum(len(df) for df in full_data.values())
        symbols = list(full_data.keys())
        logger.info(
            "Data ready: %d bars across %d symbols %s (%.1fs)",
            total_bars,
            len(full_data),
            symbols,
            time.time() - t0,
        )

        logger.info("Step 2/4: Preparing %d daily windows...", args.days)
        now = datetime.now(timezone.utc)
        tasks = []
        for i in range(args.days):
            window_end = now - timedelta(days=i)
            window_end = window_end.replace(hour=0, minute=0, second=0, microsecond=0)
            window_end_ms = int(window_end.timestamp() * 1000)
            window_date_str = window_end.strftime("%Y-%m-%d")

            sliced = slice_data_for_window(full_data, window_end_ms, args.lookback)
            window_bars = sum(len(df) for df in sliced.values())
            logger.info(
                "  Window %2d (%s): %d bars across %d symbols",
                i,
                window_date_str,
                window_bars,
                len(sliced),
            )
            tasks.append((window_date_str, sliced, i, args.subsample))

        logger.info(
            "Step 3/4: Running optimisation in parallel (%d workers)...", args.workers
        )
        t0 = time.time()
        all_window_results = []

        pool_initialize(args.workers)
        _pool = get_pool()

        if _pool is not None:
            futures = {_pool.submit(_run_window_worker, task): task for task in tasks}
            completed = 0
            for future in as_completed(futures):
                task = futures[future]
                window_date = task[0]
                window_idx = task[2]
                try:
                    result = future.result()
                    all_window_results.append(result)
                    completed += 1
                    best_score = (
                        result["best"].get("_score", 0) if result.get("best") else 0
                    )
                    logger.info(
                        "  [%2d/%d] Window %s done: %d results, best Score=%.2f",
                        completed,
                        len(tasks),
                        window_date,
                        result["num_results"],
                        best_score,
                    )

                    best = result.get("best")
                    if best:
                        snap = {
                            "symbol": "PORTFOLIO",
                            "params": best.get("params", {}),
                            "score": best.get("_score", 0),
                            "sweep_name": f"sliding_window_{window_date}",
                            "period": result.get("period", ""),
                            "sharpe": best.get("sharpe", 0),
                            "total_return_pct": best.get("total_return_pct", 0),
                            "max_drawdown_pct": best.get("max_drawdown_pct", 0),
                            "profit_factor": best.get("profit_factor", 0),
                            "win_rate_pct": best.get("win_rate_pct", 0),
                            "num_trades": best.get("num_trades", 0),
                            "ret_dd_ratio": best.get("_ret_dd", 0),
                        }
                        n = save_snapshots_to_db([snap], is_active=False)
                        if n > 0:
                            logger.info(
                                "    -> saved to DB (inactive), score=%.2f",
                                best.get("_score", 0),
                            )

                except Exception as exc:
                    logger.error(
                        "  Window %s (idx=%d) FAILED: %s", window_date, window_idx, exc
                    )
                    all_window_results.append(
                        {
                            "window_date": window_date,
                            "window_idx": window_idx,
                            "num_results": 0,
                            "total_bars": 0,
                            "results": [],
                            "best": None,
                            "error": str(exc),
                        }
                    )
        else:
            logger.warning("Pool not available — running windows sequentially")
            for task in tasks:
                window_date = task[0]
                window_idx = task[2]
                try:
                    result = _run_window_worker(task)
                    all_window_results.append(result)
                except Exception as exc:
                    logger.error(
                        "  Window %s (idx=%d) FAILED: %s", window_date, window_idx, exc
                    )
                    all_window_results.append(
                        {
                            "window_date": window_date,
                            "window_idx": window_idx,
                            "num_results": 0,
                            "total_bars": 0,
                            "results": [],
                            "best": None,
                            "error": str(exc),
                        }
                    )

        pool_shutdown()

        all_window_results.sort(key=lambda x: x["window_idx"])
        logger.info(
            "Optimisation complete: %d windows (%.1fs)",
            len(all_window_results),
            time.time() - t0,
        )

        logger.info("Step 4/4: Saving final JSON summary...")
        save_results_json(all_window_results)

        db_count = sum(1 for wr in all_window_results if wr.get("best"))
        logger.info(
            "Saved %d windows to DB (incremental), JSON summary written",
            db_count,
        )

        print_summary(all_window_results, time.time() - t_start, args.workers)

    except Exception:
        logger.exception("Fatal error in sliding_window_tune")
        sys.exit(1)


if __name__ == "__main__":
    main()

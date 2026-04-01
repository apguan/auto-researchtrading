#!/usr/bin/env python3
"""Autonomous Iterative 1h Parameter Tuning Loop.

Downloads 1h candle data, runs the full tune_1h.py tuning pipeline
iteratively, adapts sweep ranges based on results, and repeats until
convergence or max iterations.

Each iteration:
  1. Run tune_1h pipeline (sweeps -> stepwise -> random search -> adaptive grid)
    2. Optional validation (walk-forward + configurable stability)
  3. Analyze results, check convergence
  4. Adapt sweep ranges around winners for next iteration
  5. Git commit iteration results

Usage:
    python data_pipeline/auto_tune_1h.py --dry-run --max-iterations 3
    python data_pipeline/auto_tune_1h.py --max-iterations 8 --workers 4
    python data_pipeline/auto_tune_1h.py --max-iterations 5 --update-strategy
"""

import sys
import os
import re
import time
import json
import logging
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Path setup — same pattern as tune_1h.py
# ---------------------------------------------------------------------------
_this_dir = Path(__file__).resolve().parent
_pipeline_root = _this_dir  # data_pipeline/
_repo_root = _this_dir.parent  # auto-researchtrading/
for _p in (_pipeline_root, _repo_root):
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from data_pipeline.backtest import backtest_interval
from data_pipeline.backtest.tune_1h import (
    SINGLE_SWEEPS,
    SECONDARY_SWEEPS,
    DEFAULTS,
    INT_PARAMS,
    BAR_COUNT_PARAMS,
    score_result,
    annualize_sharpe,
    run_sweep,
    revalidate,
    forward_stepwise_accumulate,
    random_search_phase,
    build_adaptive_grid,
    run_walk_forward,
    test_stability,
    save_results,
    load_latest_results,
    reset_params,
    set_params,
    subsample_data,
    pool_initialize,
    pool_shutdown,
    print_results,
    print_walk_forward,
    print_stability,
    _RESULTS_DIR,
)
from data_pipeline.pool import optimal_worker_count

LOG_DIR = _pipeline_root / "logs"
STRATEGY_PATH = _repo_root / "strategy.py"

# Deep copy of original sweep definitions (we'll mutate copies)
_ORIGINAL_SINGLE_SWEEPS = [
    (name, dict(grid) if isinstance(grid, dict) else list(grid))
    for name, grid in SINGLE_SWEEPS
]
_ORIGINAL_SECONDARY_SWEEPS = [
    (name, dict(grid) if isinstance(grid, dict) else list(grid))
    for name, grid in SECONDARY_SWEEPS
]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    log_file = LOG_DIR / f"auto_tune_1h_{date_str}.log"

    log = logging.getLogger("auto_tune_1h")
    if log.handlers:
        return log
    log.setLevel(logging.DEBUG)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
        )
    )
    log.addHandler(ch)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(fh)

    return log


logger = logging.getLogger("auto_tune_1h")


# ---------------------------------------------------------------------------
# Data download
# ---------------------------------------------------------------------------
def ensure_data(skip_symbols: set[str] = set()) -> dict:
    """Download 1h data if not cached, then load it."""
    from constants import INTERVAL_SYMBOLS

    target_symbols = [s for s in INTERVAL_SYMBOLS["1h"] if s not in skip_symbols]

    cache_dir = backtest_interval.cache_data_dir("1h")
    missing = []
    for symbol in target_symbols:
        parquet_path = os.path.join(cache_dir, f"{symbol}_1h.parquet")
        if not os.path.exists(parquet_path):
            missing.append(symbol)

    if missing:
        logger.info("Downloading 1h data for: %s", missing)
        data = backtest_interval.download_all_data(
            hours_back=8760, interval="1h"
        )
        if not data:
            raise RuntimeError("Data download returned no data.")
    else:
        data = backtest_interval.load_data(interval="1h", data_dir=cache_dir)

    if not data:
        raise RuntimeError(
            "No 1h data available. Download failed and no cache found."
        )

    if skip_symbols:
        data = {s: d for s, d in data.items() if s not in skip_symbols}

    total_bars = sum(len(df) for df in data.values())
    logger.info(
        "Loaded %d bars across %d symbols: %s",
        total_bars,
        len(data),
        list(data.keys()),
    )
    return data


# ---------------------------------------------------------------------------
# Sweep range adaptation
# ---------------------------------------------------------------------------
def adapt_sweep_ranges(
    current_sweeps: list[tuple[str, Any]],
    original_sweeps: list[tuple[str, Any]],
    best_params: dict,
    shrink_factor: float = 0.5,
    exploration_width: float = 0.1,
) -> list[tuple[str, Any]]:
    """Narrow sweep ranges around best values while keeping exploration budget.

    For dict-based sweeps (single param): center range on best value,
    shrink by shrink_factor, but keep at least exploration_width of original range.
    For list-based sweeps (paired params): keep as-is.
    """
    # Build name-keyed lookup into original sweeps
    orig_by_name: dict[str, Any] = {
        name: grid for name, grid in original_sweeps
    }

    adapted = []
    for name, grid in current_sweeps:
        if not isinstance(grid, dict):
            # Paired sweep — keep as-is
            adapted.append((name, grid))
            continue

        new_grid = {}
        for param, values in grid.items():
            best_val = best_params.get(param)
            if best_val is None:
                # Fall back to middle of current range
                best_val = values[len(values) // 2]

            # Get original range for reference
            orig_values = values
            orig_grid = orig_by_name.get(name)
            if orig_grid is not None and isinstance(orig_grid, dict):
                orig_values = orig_grid.get(param, values)

            orig_min = min(orig_values)
            orig_max = max(orig_values)
            original_range = orig_max - orig_min

            if original_range == 0:
                # Degenerate — add some spread
                if param in INT_PARAMS:
                    new_grid[param] = [int(best_val), int(best_val) + 1]
                else:
                    new_grid[param] = [best_val * 0.95, best_val, best_val * 1.05]
                continue

            # New range: shrink around best, but keep exploration budget
            new_half_range = original_range * shrink_factor / 2
            min_exploration = original_range * exploration_width
            new_half_range = max(new_half_range, min_exploration)

            new_min = best_val - new_half_range
            new_max = best_val + new_half_range

            n_points = max(4, min(7, len(values)))
            if param in INT_PARAMS:
                raw = np.linspace(new_min, new_max, n_points)
                new_values = sorted(set(max(1, int(round(v))) for v in raw))
                best_rounded = int(round(best_val))
                if best_rounded not in new_values:
                    new_values.append(best_rounded)
                    new_values.sort()

                if len(new_values) < 2:
                    new_values = sorted(
                        set([max(1, best_rounded - 1), best_rounded, best_rounded + 1])
                    )
            else:
                raw = np.linspace(new_min, new_max, n_points)
                new_values = sorted(set(float(round(v, 6)) for v in raw))
                best_rounded = float(round(best_val, 6))
                if best_rounded not in new_values:
                    new_values.append(best_rounded)
                    new_values.sort()

                if len(new_values) < 2:
                    new_values = sorted(
                        set([best_val * 0.95, best_val, best_val * 1.05])
                    )

            new_grid[param] = new_values

        adapted.append((name, new_grid))

    return adapted


# ---------------------------------------------------------------------------
# Convergence check
# ---------------------------------------------------------------------------
def check_convergence(
    score_history: list[float],
    epsilon: float = 0.02,
    consecutive_stale: int = 3,
) -> bool:
    """Return True if improvement has plateaued.

    Checks the last `consecutive_stale` iterations: if all improvements
    are below `epsilon` (relative), declare convergence.
    """
    if len(score_history) < consecutive_stale + 1:
        return False

    recent = score_history[-(consecutive_stale + 1):]
    improvements = []
    for i in range(1, len(recent)):
        prev = recent[i - 1]
        if prev != 0:
            rel_imp = (recent[i] - prev) / abs(prev)
            improvements.append(rel_imp)
        else:
            improvements.append(float("inf") if recent[i] > 0 else 0.0)

    if not improvements:
        return True

    return all(imp < epsilon for imp in improvements)


def should_validate_iteration(
    iteration: int,
    max_iterations: int,
    validate_every: int,
) -> bool:
    """Return True when this iteration should run walk-forward validation.

    validate_every=0 means skip intermediate validation and only validate the
    final scheduled iteration. Convergence-triggered final validation is handled
    separately in main().
    """
    if iteration == max_iterations:
        return True
    if validate_every <= 0:
        return False
    return iteration % validate_every == 0


def run_validation(
    full_data: dict,
    best_params: dict,
    validation_folds: int,
    stability_mode: str,
    stability_subsample: int,
    dry_run: bool = False,
) -> tuple[dict | None, dict | None]:
    """Run the iteration validation step and return validation artifacts."""
    if dry_run:
        logger.info(
            "  [DRY-RUN] Would run walk-forward validation (%d folds) and stability=%s",
            validation_folds,
            stability_mode,
        )
        return None, None

    logger.info("Pool shut down — running walk-forward (%d folds)", validation_folds)

    wf_result = None
    stab_result = None

    wf = run_walk_forward(full_data, best_params, n_folds=max(1, validation_folds))
    if "error" not in wf:
        print_walk_forward(wf)
        wf_result = wf
    else:
        logger.warning("Walk-forward failed: %s", wf.get("error", "unknown"))

    if stability_mode != "off":
        if stability_mode == "fast":
            sample_factor = max(1, stability_subsample)
            stability_data = (
                subsample_data(full_data, sample_factor)
                if sample_factor > 1
                else full_data
            )
            logger.info(
                "Running fast stability check (subsample=%d)",
                sample_factor,
            )
        else:
            stability_data = full_data
            logger.info("Running full stability check")

        stab = test_stability(stability_data, best_params)
        print_stability(stab)
        stab_result = stab

    return wf_result, stab_result


# ---------------------------------------------------------------------------
# Run one tuning iteration
# ---------------------------------------------------------------------------
def run_tuning_iteration(
    full_data: dict,
    single_sweeps: list[tuple[str, Any]],
    secondary_sweeps: list[tuple[str, Any]],
    best_params_accumulator: dict,
    workers: int,
    subsample: int,
    revalidate_top: int,
    validation_folds: int,
    stability_mode: str,
    stability_subsample: int,
    validate: bool,
    dry_run: bool = False,
) -> tuple[dict, float, list[dict], dict | None, dict | None]:
    """Run the full tune_1h pipeline with given sweep ranges.

    Returns (best_params, best_score, all_phase_results, wf_result, stab_result).
    """
    if dry_run:
        total_combos = 0
        for _, grid in single_sweeps:
            if isinstance(grid, dict):
                c = 1
                for v in grid.values():
                    c *= len(v)
            else:
                c = len(grid)
            total_combos += c
        for _, grid in secondary_sweeps:
            if isinstance(grid, dict):
                c = 1
                for v in grid.values():
                    c *= len(v)
            else:
                c = len(grid)
            total_combos += c
        logger.info(
            "  [DRY-RUN] Would run ~%d sweep combos + stepwise + random search + adaptive grid%s%s",
            total_combos,
            f" + walk-forward ({validation_folds} folds)" if validate else "",
            f" + stability ({stability_mode})" if validate and stability_mode != "off" else "",
        )
        return best_params_accumulator, 0.0, [], None, None

    pool_initialize(workers)

    screen_data = subsample_data(full_data, subsample) if subsample > 1 else full_data
    all_phase_results: list[dict] = []
    best_overall_result = None
    selected_score = 0.0

    # ---- Phase 1: Single-parameter sweeps ----
    logger.info("Phase 1: Single-parameter sweeps (%d sweeps)", len(single_sweeps))
    for name, grid in single_sweeps:
        results = run_sweep(
            screen_data, name, grid, n_workers=workers, subsample_factor=subsample
        )
        if subsample > 1 and results:
            results = revalidate(full_data, results, top_n=revalidate_top)
        if results:
            print_results(results, name)
            all_phase_results.extend(results)
            results.sort(key=lambda x: x["_score"], reverse=True)
            if best_overall_result is None or results[0]["_score"] > best_overall_result["_score"]:
                best_overall_result = results[0]

    # ---- Phase 2: Secondary sweeps ----
    logger.info("Phase 2: Secondary sweeps (%d sweeps)", len(secondary_sweeps))
    for name, grid in secondary_sweeps:
        results = run_sweep(
            screen_data, name, grid, n_workers=workers, subsample_factor=subsample
        )
        if subsample > 1 and results:
            results = revalidate(full_data, results, top_n=revalidate_top)
        if results:
            print_results(results, name)
            all_phase_results.extend(results)
            results.sort(key=lambda x: x["_score"], reverse=True)
            if best_overall_result is None or results[0]["_score"] > best_overall_result["_score"]:
                best_overall_result = results[0]

    # ---- Forward stepwise ----
    stepwise_score = 0.0
    if best_overall_result is not None and all_phase_results:
        selected_score = best_overall_result["_score"]
        logger.info("Forward stepwise accumulation (on full data)")
        stepwise_params, stepwise_score = forward_stepwise_accumulate(
            full_data, all_phase_results, initial_params=best_params_accumulator
        )
        if best_overall_result and stepwise_score < best_overall_result["_score"]:
            logger.info(
                "Stepwise %.2f < best single %.2f — merging single winner into accumulator",
                stepwise_score,
                best_overall_result["_score"],
            )
            best_params_accumulator.update(best_overall_result["params"])
            selected_score = best_overall_result["_score"]
        else:
            logger.info("Stepwise %.2f — keeping combined params", stepwise_score)
            best_params_accumulator = stepwise_params.copy()
            selected_score = stepwise_score
    elif best_overall_result is not None:
        selected_score = best_overall_result["_score"]

    # ---- Random search ----
    if all_phase_results:
        n_random = 20 if subsample > 1 else 60
        logger.info("Random search phase (%d iterations, on full data)", n_random)
        random_params, random_score = random_search_phase(
            full_data, best_params_accumulator, all_phase_results, n_iterations=n_random
        )
        if random_score > selected_score:
            logger.info(
                "Random search improved: %.2f -> %.2f",
                selected_score,
                random_score,
            )
            best_params_accumulator = random_params
            selected_score = random_score

    # ---- Adaptive multi-parameter grid ----
    logger.info("Adaptive multi-parameter grid")
    grid = build_adaptive_grid(best_params_accumulator)
    total_combos = 1
    for v in grid.values():
        total_combos *= len(v)
    logger.info("Grid: %d combos over %d params", total_combos, len(grid))

    results = run_sweep(
        screen_data, "ADAPTIVE_MULTI", grid, n_workers=workers, subsample_factor=subsample
    )
    if results:
        if subsample > 1:
            top_n = min(10, len(results))
            validated = revalidate(full_data, results, top_n=top_n)
            if validated:
                print_results(validated, "ADAPTIVE_MULTI (full)", top_n=len(validated))
                all_phase_results.extend(validated)
                adaptive_best = max(validated, key=lambda x: x["_score"])
                if adaptive_best["_score"] > selected_score:
                    logger.info(
                        "Adaptive grid improved: %.2f -> %.2f",
                        selected_score,
                        adaptive_best["_score"],
                    )
                    best_params_accumulator.update(adaptive_best["params"])
                    selected_score = adaptive_best["_score"]
        else:
            best = print_results(results, "ADAPTIVE_MULTI")
            if best:
                all_phase_results.extend(results)
                if best["_score"] > selected_score:
                    logger.info(
                        "Adaptive grid improved: %.2f -> %.2f",
                        selected_score,
                        best["_score"],
                    )
                    best_params_accumulator.update(best["params"])
                    selected_score = best["_score"]

    # ---- Validation (sequential, pool must be down) ----
    pool_shutdown()
    if validate:
        wf_result, stab_result = run_validation(
            full_data,
            best_params_accumulator,
            validation_folds=validation_folds,
            stability_mode=stability_mode,
            stability_subsample=stability_subsample,
            dry_run=dry_run,
        )
    else:
        logger.info("Skipping iteration validation on this round")
        wf_result = None
        stab_result = None

    return (
        best_params_accumulator,
        selected_score,
        all_phase_results,
        wf_result,
        stab_result,
    )


# ---------------------------------------------------------------------------
# Git commit
# ---------------------------------------------------------------------------
def git_commit(message: str, dry_run: bool = False) -> bool:
    """Stage auto-tune code and logs, then commit. Returns True on success."""
    if dry_run:
        logger.info("[DRY-RUN] Would commit: %s", message)
        return True

    try:
        add_result = subprocess.run(
            [
                "git",
                "add",
                "-A",
                "--",
                "data_pipeline/auto_tune_1h.py",
                "data_pipeline/logs/",
                "data_pipeline/tuning_results/",
                "strategy.py",
            ],
            cwd=str(_repo_root),
            capture_output=True,
            check=False,
        )
        if add_result.returncode != 0:
            stderr = add_result.stderr.decode().strip()
            logger.warning("Git add failed: %s", stderr)
            return False

        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(_repo_root),
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            logger.info("Committed: %s", message)
            return True
        else:
            stderr = result.stderr.decode().strip()
            if "nothing to commit" in stderr:
                logger.info("Nothing to commit")
                return True
            logger.warning("Git commit failed: %s", stderr)
            return False
    except Exception as exc:
        logger.warning("Git commit error: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Update strategy.py with best params
# ---------------------------------------------------------------------------
def update_strategy_py(best_params: dict, dry_run: bool = False) -> bool:
    """Update strategy.py param values. Only changes params that differ."""
    if not STRATEGY_PATH.exists():
        logger.error("strategy.py not found at %s", STRATEGY_PATH)
        return False

    content = STRATEGY_PATH.read_text(encoding="utf-8")
    original_content = content
    changes = {}

    for param, new_val in best_params.items():
        if param not in DEFAULTS:
            continue
        if DEFAULTS[param] == new_val:
            continue

        pattern = rf"^{param}\s*=\s*.+$"
        match = re.search(pattern, content, re.MULTILINE)
        if match:
            old_line = match.group(0)
            if isinstance(new_val, float):
                new_line = f"{param} = {new_val}"
            else:
                new_line = f"{param} = {new_val}"
            content = content.replace(old_line, new_line)
            changes[param] = new_val

    if not changes:
        logger.info("No param changes needed in strategy.py")
        return False

    logger.info("Strategy.py changes: %s", changes)

    if dry_run:
        logger.info("[DRY-RUN] Would update strategy.py with %d param changes", len(changes))
        return True

    STRATEGY_PATH.write_text(content, encoding="utf-8")

    verify = STRATEGY_PATH.read_text(encoding="utf-8")
    verified = {}
    for param, expected_val in changes.items():
        m = re.search(rf"^{param}\s*=\s*(.+)$", verify, re.MULTILINE)
        if m:
            actual = m.group(1).strip()
            verified[param] = (str(expected_val), actual)
            if actual != str(expected_val):
                logger.error(
                    "VERIFICATION FAILED for %s: expected %s, got %s — reverting strategy.py",
                    param,
                    expected_val,
                    actual,
                )
                STRATEGY_PATH.write_text(original_content, encoding="utf-8")
                return False

    logger.info("Updated strategy.py with %d param changes (all verified)", len(changes))
    return True


# ---------------------------------------------------------------------------
# Iteration summary
# ---------------------------------------------------------------------------
def print_iteration_summary(
    iteration: int,
    max_iterations: int,
    best_params: dict,
    best_score: float,
    prev_score: float | None,
    wf_result: dict | None,
    stab_result: dict | None,
    elapsed: float,
):
    """Print a clear summary for the current iteration."""
    print(f"\n{'=' * 90}")
    print(f"AUTO TUNE 1H — Iteration {iteration}/{max_iterations}")
    print(f"{'=' * 90}")

    if prev_score is not None and prev_score != 0:
        improvement = (best_score - prev_score) / abs(prev_score) * 100
        direction = "+" if improvement >= 0 else ""
        print(f"Best score:     {best_score:.2f} ({direction}{improvement:.1f}% from iter {iteration - 1})")
    else:
        print(f"Best score:     {best_score:.2f} (baseline)")

    wf_pass = wf_result.get("pass") if wf_result else None
    stab_pass = stab_result.get("pass") if stab_result else None
    print(f"Walk-forward:   {'PASS' if wf_pass else 'FAIL' if wf_pass is not None else 'N/A'}")
    print(f"Stability:      {'STABLE' if stab_pass else 'UNSTABLE' if stab_pass is not None else 'N/A'}")

    changed = {k: v for k, v in best_params.items() if v != DEFAULTS.get(k)}
    if changed:
        param_strs = []
        for k, v in sorted(changed.items()):
            default_val = DEFAULTS.get(k)
            param_strs.append(f"{k}={default_val}->{v}")
        print(f"Params changed: {', '.join(param_strs)}")
    else:
        print("Params changed: (none — using defaults)")

    print(f"Wall time:      {elapsed:.0f}s")
    print(f"{'=' * 90}")


def print_final_summary(
    score_history: list[float],
    best_params: dict,
    total_elapsed: float,
    converged: bool,
):
    """Print the final summary after all iterations."""
    print(f"\n{'=' * 90}")
    print("AUTO TUNE 1H — FINAL SUMMARY")
    print(f"{'=' * 90}")

    print(f"Total iterations: {len(score_history)}")
    print(f"Converged:        {'YES' if converged else 'NO (hit max iterations)'}")
    print(f"Total wall time:  {total_elapsed:.0f}s ({total_elapsed / 60:.1f}min)")
    print()

    print(f"{'Iter':>5} {'Score':>10} {'Change':>10}")
    print("-" * 30)
    for i, score in enumerate(score_history):
        if i > 0 and score_history[i - 1] != 0:
            change = (score - score_history[i - 1]) / abs(score_history[i - 1]) * 100
            print(f"{i + 1:>5} {score:>10.2f} {change:>+9.1f}%")
        else:
            print(f"{i + 1:>5} {score:>10.2f} {'(baseline)':>10}")

    print()
    print("Best parameters (vs defaults):")
    changed = {k: v for k, v in best_params.items() if v != DEFAULTS.get(k)}
    if changed:
        for k, v in sorted(changed.items()):
            print(f"  {k:>25}: {DEFAULTS.get(k)} -> {v}")
    else:
        print("  (none — defaults are optimal)")
    print(f"{'=' * 90}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Autonomous Iterative 1h Parameter Tuning Loop"
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=8,
        help="Maximum tuning iterations (default: 8)",
    )
    parser.add_argument(
        "--epsilon",
        type=float,
        default=0.02,
        help="Min relative improvement to continue (default: 0.02 = 2%%)",
    )
    parser.add_argument(
        "--consecutive-stale",
        type=int,
        default=3,
        help="Stop after this many consecutive iterations below epsilon (default: 3)",
    )
    parser.add_argument(
        "--shrink-factor",
        type=float,
        default=0.5,
        help="Shrink sweep range by this fraction each iteration (default: 0.5)",
    )
    parser.add_argument(
        "--exploration-width",
        type=float,
        default=0.1,
        help="Keep this fraction of original range for exploration (default: 0.1)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=optimal_worker_count(),
        help=f"Parallel workers (default: {optimal_worker_count()})",
    )
    parser.add_argument(
        "--subsample",
        type=int,
        default=1,
        help="Take every Nth bar for screening (default: 1)",
    )
    parser.add_argument(
        "--revalidate-top",
        type=int,
        default=2,
        help="Re-validate top N per sweep on full data (default: 2)",
    )
    parser.add_argument(
        "--validation-folds",
        type=int,
        default=5,
        help="Walk-forward folds per validation run (default: 5)",
    )
    parser.add_argument(
        "--validate-every",
        type=int,
        default=1,
        help="Run validation every N iterations; 0 = final/converged only (default: 1)",
    )
    parser.add_argument(
        "--stability-mode",
        choices=["off", "fast", "full"],
        default="fast",
        help="Stability validation mode (default: fast)",
    )
    parser.add_argument(
        "--stability-subsample",
        type=int,
        default=4,
        help="Use every Nth bar for fast stability checks (default: 4)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print plan without executing tuning",
    )
    parser.add_argument(
        "--update-strategy",
        action="store_true",
        default=False,
        help="Update strategy.py with final best params",
    )
    parser.add_argument(
        "--skip-symbols",
        type=str,
        default="",
        help="Comma-separated symbols to skip (default: none)",
    )
    args = parser.parse_args()

    setup_logging()

    t_total_start = time.time()

    logger.info("=" * 70)
    logger.info(
        "AUTONOMOUS 1H PARAMETER TUNING — %s",
        datetime.now(timezone.utc).isoformat(),
    )
    logger.info("=" * 70)
    logger.info(
        "Config: max_iter=%d, epsilon=%.3f, stale=%d, shrink=%.2f, explore=%.2f",
        args.max_iterations,
        args.epsilon,
        args.consecutive_stale,
        args.shrink_factor,
        args.exploration_width,
    )
    logger.info(
        "Workers=%d, subsample=%d, dry_run=%s, update_strategy=%s",
        args.workers,
        args.subsample,
        args.dry_run,
        args.update_strategy,
    )
    logger.info(
        "Validation: folds=%d, every=%d, stability=%s, stability_subsample=%d",
        args.validation_folds,
        args.validate_every,
        args.stability_mode,
        args.stability_subsample,
    )

    # ---- Step 1: Load data ----
    logger.info("Step 1: Loading 1h data...")
    skip_symbols = set(
        s.strip().upper() for s in args.skip_symbols.split(",") if s.strip()
    )
    if args.dry_run:
        from constants import INTERVAL_SYMBOLS

        target_symbols = [s for s in INTERVAL_SYMBOLS["1h"] if s not in skip_symbols]
        logger.info(
            "[DRY-RUN] Would download/load 1h data for: %s",
            target_symbols,
        )
        full_data = {}
    else:
        full_data = ensure_data(skip_symbols=skip_symbols)

    # ---- Step 2: Initialize ----
    best_params_accumulator = DEFAULTS.copy()

    # Try loading previous results
    prev_params, prev_file, _ = load_latest_results()
    if prev_params:
        logger.info("Loaded previous best from: %s", prev_file)
        best_params_accumulator = prev_params.copy()
        for k, v in DEFAULTS.items():
            if k not in best_params_accumulator:
                best_params_accumulator[k] = v
    else:
        logger.info("No previous results — starting from defaults")

    # Current sweep ranges (start with originals)
    current_single = [
        (name, dict(grid) if isinstance(grid, dict) else list(grid))
        for name, grid in _ORIGINAL_SINGLE_SWEEPS
    ]
    current_secondary = [
        (name, dict(grid) if isinstance(grid, dict) else list(grid))
        for name, grid in _ORIGINAL_SECONDARY_SWEEPS
    ]

    score_history: list[float] = []
    converged = False
    previous_results_file = prev_file
    best_params = best_params_accumulator.copy()
    best_score = 0.0

    # ---- Step 3: Iterative loop ----
    for iteration in range(1, args.max_iterations + 1):
        t_iter_start = time.time()

        logger.info("\n" + "#" * 90)
        logger.info(
            "# ITERATION %d/%d%s",
            iteration,
            args.max_iterations,
            " (DRY-RUN)" if args.dry_run else "",
        )
        logger.info("#" * 90)

        # Print current sweep ranges
        logger.info("Current sweep ranges:")
        for name, grid in current_single:
            if isinstance(grid, dict):
                for k, v in grid.items():
                    logger.info("  %s: %s", k, v)
            else:
                logger.info("  %s: %d combos", name, len(grid))
        for name, grid in current_secondary:
            if isinstance(grid, dict):
                for k, v in grid.items():
                    logger.info("  %s: %s", k, v)
            else:
                logger.info("  %s: %d combos", name, len(grid))

        validate_this_iteration = should_validate_iteration(
            iteration,
            args.max_iterations,
            args.validate_every,
        )
        if validate_this_iteration:
            logger.info(
                "Validation scheduled for iteration %d (%d folds)",
                iteration,
                args.validation_folds,
            )
        else:
            logger.info("Validation deferred for iteration %d", iteration)

        # Run tuning (wrapped for resilience — survive phase crashes)
        try:
            (
                best_params,
                best_score,
                all_results,
                wf_result,
                stab_result,
            ) = run_tuning_iteration(
                full_data=full_data,
                single_sweeps=current_single,
                secondary_sweeps=current_secondary,
                best_params_accumulator=best_params_accumulator,
                workers=args.workers,
                subsample=args.subsample,
                revalidate_top=args.revalidate_top,
                validation_folds=args.validation_folds,
                stability_mode=args.stability_mode,
                stability_subsample=args.stability_subsample,
                validate=validate_this_iteration,
                dry_run=args.dry_run,
            )
        except Exception as exc:
            iter_elapsed = time.time() - t_iter_start
            logger.error(
                "Iteration %d CRASHED after %.0fs: %s — skipping, continuing with previous best",
                iteration,
                iter_elapsed,
                exc,
            )
            git_commit(
                f"tune_1h: iteration {iteration} CRASHED — {exc}",
                dry_run=args.dry_run,
            )
            # Reset pool in case it's in a bad state
            pool_shutdown()
            reset_params()
            continue

        iter_elapsed = time.time() - t_iter_start

        # Track score
        score_history.append(best_score)
        prev_score = score_history[-2] if len(score_history) >= 2 else None

        converged_this_iteration = check_convergence(
            score_history,
            args.epsilon,
            args.consecutive_stale,
        )

        if converged_this_iteration and wf_result is None:
            logger.info("Running final validation before stopping on convergence")
            wf_result, stab_result = run_validation(
                full_data,
                best_params,
                validation_folds=args.validation_folds,
                stability_mode=args.stability_mode,
                stability_subsample=args.stability_subsample,
                dry_run=args.dry_run,
            )

        if not args.dry_run:
            results_file = save_results(
                best_params,
                best_score,
                all_results,
                wf_result,
                stab_result,
                previous_file=previous_results_file,
            )
            previous_results_file = results_file or previous_results_file
        reset_params()

        # Print iteration summary
        print_iteration_summary(
            iteration=iteration,
            max_iterations=args.max_iterations,
            best_params=best_params,
            best_score=best_score,
            prev_score=prev_score,
            wf_result=wf_result,
            stab_result=stab_result,
            elapsed=iter_elapsed,
        )

        # Git commit
        git_msg = (
            f"tune_1h: iteration {iteration} — score={best_score:.2f}"
            + (f" ({'PASS' if wf_result and wf_result.get('pass') else 'FAIL'})" if wf_result else "")
        )
        git_commit(git_msg, dry_run=args.dry_run)

        # Check convergence
        if converged_this_iteration:
            logger.info(
                "CONVERGED: improvement below %.1f%% for %d consecutive iterations",
                args.epsilon * 100,
                args.consecutive_stale,
            )
            converged = True
            break

        # Adapt sweep ranges for next iteration
        if iteration < args.max_iterations:
            logger.info("Adapting sweep ranges for iteration %d...", iteration + 1)
            current_single = adapt_sweep_ranges(
                current_single,
                _ORIGINAL_SINGLE_SWEEPS,
                best_params,
                shrink_factor=args.shrink_factor,
                exploration_width=args.exploration_width,
            )
            current_secondary = adapt_sweep_ranges(
                current_secondary,
                _ORIGINAL_SECONDARY_SWEEPS,
                best_params,
                shrink_factor=args.shrink_factor,
                exploration_width=args.exploration_width,
            )
            best_params_accumulator = best_params.copy()

            logger.info("Adapted sweep ranges:")
            for name, grid in current_single:
                if isinstance(grid, dict):
                    for k, v in grid.items():
                        logger.info("  %s: %s", k, v)
            for name, grid in current_secondary:
                if isinstance(grid, dict):
                    for k, v in grid.items():
                        logger.info("  %s: %s", k, v)

    # ---- Final summary ----
    total_elapsed = time.time() - t_total_start
    print_final_summary(score_history, best_params, total_elapsed, converged)

    # ---- Update strategy.py ----
    if args.update_strategy and not args.dry_run:
        logger.info("Updating strategy.py with best params...")
        update_strategy_py(best_params, dry_run=False)
        git_commit(
            f"tune_1h: apply final best params to strategy.py",
            dry_run=False,
        )
    elif args.update_strategy and args.dry_run:
        update_strategy_py(best_params, dry_run=True)

    # ---- Save loop summary ----
    loop_summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "iterations": len(score_history),
        "converged": converged,
        "score_history": score_history,
        "best_params": {
            k: v for k, v in best_params.items() if v != DEFAULTS.get(k)
        },
        "config": {
            "max_iterations": args.max_iterations,
            "epsilon": args.epsilon,
            "consecutive_stale": args.consecutive_stale,
            "shrink_factor": args.shrink_factor,
            "exploration_width": args.exploration_width,
            "workers": args.workers,
            "subsample": args.subsample,
            "validation_folds": args.validation_folds,
            "validate_every": args.validate_every,
            "stability_mode": args.stability_mode,
            "stability_subsample": args.stability_subsample,
        },
    }
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = _RESULTS_DIR / "auto_tune_1h_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(loop_summary, f, indent=2)
        f.write("\n")
    logger.info("Loop summary saved to: %s", summary_path)

    logger.info("Done. Total time: %.1fs", total_elapsed)


if __name__ == "__main__":
    main()

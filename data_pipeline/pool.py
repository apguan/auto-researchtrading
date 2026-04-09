"""Singleton process-pool manager for data_pipeline.

Centralises all pool creation, lifecycle, and configuration so that every
pipeline script shares one ``loky.ProcessPoolExecutor``.

Workers are pure functions — they receive args and return results.
**No worker function should import or interact with this module.**

Usage (entry-point scripts only)::

    from data_pipeline.pool import initialize, get_pool, shutdown

    initialize(n_workers=4)
    pool = get_pool()
    future = pool.submit(my_worker_func, args)
    ...
    shutdown()
"""

import logging
import multiprocessing as mp

from loky import ProcessPoolExecutor

log = logging.getLogger("data_pipeline.pool")

# ---------------------------------------------------------------------------
# Module state — private
# ---------------------------------------------------------------------------
_pool: ProcessPoolExecutor | None = None
_pool_workers: int = 0


# ---------------------------------------------------------------------------
# Worker-count heuristics
# ---------------------------------------------------------------------------
def optimal_worker_count(mem_per_worker_gb: float = 1.0) -> int:
    """Derive worker count from CPU count and available memory.

    Falls back to ``mp.cpu_count()`` when ``psutil`` is not installed.
    """
    cpu = mp.cpu_count() or 4
    try:
        import psutil

        avail_gb = psutil.virtual_memory().available / (1024**3)
        max_by_mem = max(1, int(avail_gb / mem_per_worker_gb))
        return min(cpu, max_by_mem)
    except ImportError:
        return cpu


# ---------------------------------------------------------------------------
# Pool lifecycle
# ---------------------------------------------------------------------------
def initialize(n_workers: int | None = None) -> ProcessPoolExecutor:
    """Create (or return) the singleton process pool.

    Idempotent — calling with the same worker count returns the existing pool.
    Calling with a *different* worker count shuts down the old pool first.

    Args:
        n_workers: Number of worker processes.
            ``None`` → ``optimal_worker_count()``.

    Returns:
        The singleton ``ProcessPoolExecutor``.
    """
    global _pool, _pool_workers

    if n_workers is None:
        n_workers = optimal_worker_count()

    if _pool is not None:
        if _pool_workers == n_workers:
            return _pool
        # Worker count changed — recreate
        shutdown()

    _pool_workers = n_workers
    _pool = ProcessPoolExecutor(max_workers=n_workers)
    log.info("Pool initialized: %d workers", n_workers)
    return _pool


def get_pool() -> ProcessPoolExecutor | None:
    """Return the singleton pool, or ``None`` if not initialised."""
    return _pool


def shutdown() -> None:
    """Shut down the singleton pool and release all workers."""
    global _pool, _pool_workers

    if _pool is not None:
        log.info("Pool shutting down...")
        _pool.shutdown(wait=True)
        _pool = None
        _pool_workers = 0

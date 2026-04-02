# Config

## Strategy Parameters

The live bot uses the single strategy from repo-root `strategy.py`. Parameters are
loaded from the database via `load_params_from_db()` (triggered by the
`LOAD_PARAMS_FROM_DB` env var). The `Settings` class provides hardcoded defaults
from `constants.py` STRATEGY_DEFAULTS["1h"] as fallback.


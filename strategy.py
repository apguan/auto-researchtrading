"""
3-class XGBoost direction strategy (from trading_algo.ipynb research).

Uses rolling simple returns, quantile-based labels for training, and a classifier on the
last WINDOW_SIZE returns. Fits once per symbol when history is long enough.

Trading rule: expected next return from class probabilities and empirical mean return per
class is compared to the same return quantiles used for labeling — if expected return is
above the upper-quantile return level, go long; if below the lower-quantile level, go
short; else flat.

If XGBoost cannot load (e.g. missing OpenMP on macOS), falls back to
sklearn.ensemble.HistGradientBoostingClassifier with similar hyperparameters.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

from prepare import Signal, PortfolioState, BarData

ACTIVE_SYMBOLS = ["BTC", "ETH", "SOL"]
SYMBOL_WEIGHTS = {"BTC": 1, "ETH": 0, "SOL": 0}

# Match trading_algo.ipynb defaults (hourly bars here; same window length)
WINDOW_SIZE = 24
LOWER_QUANTILE = 0.03
UPPER_QUANTILE = 0.97

# Training set size for the one-time fit per symbol (rolling windows over returns)
MIN_TRAINING_SAMPLES = 50 # require at least this many (X, y) rows or skip fit
MAX_TRAINING_SAMPLES = 24 * 20  # keep at most this many most-recent rows

# XGB (aligned with notebook; keep modest for harness runtime)
XGB_N_ESTIMATORS = 500
XGB_MAX_DEPTH = 3
XGB_LEARNING_RATE = 0.05
XGB_SUBSAMPLE = 0.8
XGB_COLSAMPLE = 0.8

BASE_POSITION_PCT = 0.08


def _label_int(ret: float, buy_threshold: float, sell_threshold: float) -> int:
    """Training labels: 0=high return, 1=mid, 2=low return (vs quantile cutoffs)."""
    if ret > buy_threshold:
        return 0
    if ret < sell_threshold:
        return 2
    return 1


def _build_dataset(
    returns: np.ndarray,
    buy_threshold: float,
    sell_threshold: float,
    min_samples: int,
    max_samples: int,
) -> tuple[np.ndarray, np.ndarray] | tuple[None, None]:
    if len(returns) < WINDOW_SIZE + 2:
        return None, None
    X_list = []
    y_list = []
    for t in range(WINDOW_SIZE, len(returns) - 1):
        X_list.append(returns[t - WINDOW_SIZE : t])
        y_list.append(_label_int(returns[t], buy_threshold, sell_threshold))
    if len(X_list) < min_samples:
        return None, None
    X_arr = np.asarray(X_list, dtype=np.float64)
    y_arr = np.asarray(y_list, dtype=np.int32)
    if len(X_arr) > max_samples:
        X_arr = X_arr[-max_samples:]
        y_arr = y_arr[-max_samples:]
    return X_arr, y_arr


def _fit_direction_classifier(X: np.ndarray, y: np.ndarray) -> Any:
    try:
        import xgboost as xgb

        model = xgb.XGBClassifier(
            n_estimators=XGB_N_ESTIMATORS,
            max_depth=XGB_MAX_DEPTH,
            learning_rate=XGB_LEARNING_RATE,
            subsample=XGB_SUBSAMPLE,
            colsample_bytree=XGB_COLSAMPLE,
            objective="multi:softprob",
            num_class=3,
            eval_metric="mlogloss",
            n_jobs=1,
            random_state=42,
        )
        model.fit(X, y)
        return model
    except Exception:
        model = HistGradientBoostingClassifier(
            max_iter=XGB_N_ESTIMATORS,
            max_depth=XGB_MAX_DEPTH,
            learning_rate=XGB_LEARNING_RATE,
            random_state=42,
        )
        model.fit(X, y)
        return model


def _class_mean_returns(
    returns: np.ndarray, buy_threshold: float, sell_threshold: float
) -> tuple[float, float, float]:
    """Mean simple return for each training label bucket (0=high, 1=mid, 2=low)."""
    r = returns.astype(np.float64)
    high = r > buy_threshold
    low = r < sell_threshold
    lab = np.where(high, 0, np.where(low, 2, 1)).astype(np.int32)
    fallback = float(np.mean(r)) if len(r) else 0.0
    out = []
    for c in (0, 1, 2):
        m = r[lab == c]
        out.append(float(m.mean()) if m.size else fallback)
    return out[0], out[1], out[2]


def _target_from_expected_return(
    exp_ret: float,
    buy_threshold: float,
    sell_threshold: float,
    equity: float,
    symbol: str,
) -> float:
    """Above upper-quantile return level -> long; below lower-quantile -> short; else flat."""
    w = SYMBOL_WEIGHTS.get(symbol, 0.33)
    size = equity * BASE_POSITION_PCT * w
    if exp_ret > buy_threshold:
        return size
    if exp_ret < sell_threshold:
        return -size
    return 0.0


class Strategy:
    def __init__(
        self,
        *,
        min_training_samples: int | None = None,
        max_training_samples: int | None = None,
    ) -> None:
        self._models: dict[str, Any] = {s: None for s in ACTIVE_SYMBOLS}
        self._min_training_samples = (
            min_training_samples
            if min_training_samples is not None
            else MIN_TRAINING_SAMPLES
        )
        self._max_training_samples = (
            max_training_samples
            if max_training_samples is not None
            else MAX_TRAINING_SAMPLES
        )
        if self._min_training_samples > self._max_training_samples:
            raise ValueError(
                "min_training_samples must be <= max_training_samples"
            )

    def on_bar(self, bar_data: dict[str, BarData], portfolio: PortfolioState) -> list[Signal]:
        signals: list[Signal] = []
        equity = portfolio.equity if portfolio.equity > 0 else portfolio.cash

        for symbol in ACTIVE_SYMBOLS:
            if symbol not in bar_data:
                continue
            bd = bar_data[symbol]
            hist = bd.history
            if len(hist) < WINDOW_SIZE + 2:
                continue

            closes = hist["close"].values.astype(np.float64)
            returns = np.diff(closes) / closes[:-1]

            buy_threshold = float(np.quantile(returns, UPPER_QUANTILE))
            sell_threshold = float(np.quantile(returns, LOWER_QUANTILE))

            if self._models[symbol] is None:
                X_train, y_train = _build_dataset(
                    returns,
                    buy_threshold,
                    sell_threshold,
                    min_samples=self._min_training_samples,
                    max_samples=self._max_training_samples,
                )
                if X_train is not None:
                    print(
                        f"[Strategy] {symbol} dataset: X.shape={X_train.shape}, "
                        f"y.shape={y_train.shape}, WINDOW_SIZE={WINDOW_SIZE}, "
                        f"min/max training samples={self._min_training_samples}/"
                        f"{self._max_training_samples}, "
                        f"live feat row shape=(1, {WINDOW_SIZE})"
                    )
                    self._models[symbol] = _fit_direction_classifier(X_train, y_train)

            model = self._models[symbol]
            if model is None:
                continue

            feat = returns[-WINDOW_SIZE:].reshape(1, -1)
            m0, m1, m2 = _class_mean_returns(returns, buy_threshold, sell_threshold)
            proba = model.predict_proba(feat)[0]
            exp_ret = float(np.dot(proba, np.array([m0, m1, m2], dtype=np.float64)))
            target = _target_from_expected_return(
                exp_ret, buy_threshold, sell_threshold, equity, symbol
            )
            current = portfolio.positions.get(symbol, 0.0)

            if abs(target - current) > 1.0:
                signals.append(Signal(symbol=symbol, target_position=target))

        return signals

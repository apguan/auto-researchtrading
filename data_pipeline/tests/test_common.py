import os
import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import common  # noqa: E402


def _inject_mock_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


class TestFindBestResult:
    def test_empty_list_returns_none(self):
        assert common.find_best_result([]) is None

    def test_all_negative_returns_returns_none(self):
        results = [
            {
                "total_return_pct": -5,
                "max_drawdown_pct": 2,
                "num_trades": 20,
                "_score": 1,
            },
            {
                "total_return_pct": -1,
                "max_drawdown_pct": 1,
                "num_trades": 30,
                "_score": 2,
            },
        ]
        assert common.find_best_result(results) is None

    def test_num_trades_less_than_10_returns_none(self):
        results = [
            {
                "total_return_pct": 10,
                "max_drawdown_pct": 2,
                "num_trades": 9,
                "_score": 5,
            }
        ]
        assert common.find_best_result(results) is None

    def test_zero_drawdown_returns_none(self):
        results = [
            {
                "total_return_pct": 10,
                "max_drawdown_pct": 0,
                "num_trades": 20,
                "_score": 5,
            }
        ]
        assert common.find_best_result(results) is None

    def test_negative_drawdown_returns_none(self):
        results = [
            {
                "total_return_pct": 10,
                "max_drawdown_pct": -1,
                "num_trades": 20,
                "_score": 5,
            }
        ]
        assert common.find_best_result(results) is None

    def test_returns_highest_score_among_valid(self):
        results = [
            {
                "total_return_pct": 5,
                "max_drawdown_pct": 1,
                "num_trades": 15,
                "_score": 3.0,
            },
            {
                "total_return_pct": 8,
                "max_drawdown_pct": 2,
                "num_trades": 20,
                "_score": 7.5,
            },
            {
                "total_return_pct": 3,
                "max_drawdown_pct": 0.5,
                "num_trades": 12,
                "_score": 2.0,
            },
        ]
        best = common.find_best_result(results)
        assert best is not None
        assert best["_score"] == 7.5

    def test_ignores_invalid_mixed_with_valid(self):
        results = [
            {
                "total_return_pct": -5,
                "max_drawdown_pct": 2,
                "num_trades": 20,
                "_score": 99,
            },
            {
                "total_return_pct": 10,
                "max_drawdown_pct": 0,
                "num_trades": 20,
                "_score": 50,
            },
            {
                "total_return_pct": 10,
                "max_drawdown_pct": 2,
                "num_trades": 5,
                "_score": 80,
            },
            {
                "total_return_pct": 6,
                "max_drawdown_pct": 1,
                "num_trades": 11,
                "_score": 4.0,
            },
        ]
        best = common.find_best_result(results)
        assert best is not None
        assert best["_score"] == 4.0

    def test_missing_keys_uses_defaults(self):
        results = [{"_score": 100, "max_drawdown_pct": 2, "num_trades": 20}]
        assert common.find_best_result(results) is None


class TestComputePeriod:
    def test_empty_dict_returns_empty_string(self):
        assert common.compute_period({}) == ""

    def test_single_symbol_correct_format(self):
        start_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp() * 1000)
        df = pd.DataFrame({"timestamp": [start_ms, end_ms]})
        result = common.compute_period({"BTC": df})
        assert result == "2024-01-01_2024-01-02"

    def test_multiple_symbols_uses_min_start_max_end(self):
        s_a = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        e_a = int(datetime(2024, 1, 5, tzinfo=timezone.utc).timestamp() * 1000)
        s_b = int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp() * 1000)
        e_b = int(datetime(2024, 1, 8, tzinfo=timezone.utc).timestamp() * 1000)
        data = {
            "BTC": pd.DataFrame({"timestamp": [s_a, e_a]}),
            "ETH": pd.DataFrame({"timestamp": [s_b, e_b]}),
        }
        result = common.compute_period(data)
        assert result == "2024-01-01_2024-01-08"


MOCK_DEFAULTS = {
    "SHORT_WINDOW": 24,
    "MED_WINDOW": 48,
    "EMA_FAST": 28,
    "EMA_SLOW": 104,
}


@pytest.fixture
def mock_db():
    mock_pg = MagicMock()
    mock_tune = types.ModuleType("backtest.tune_15m")
    setattr(mock_tune, "DEFAULTS", MOCK_DEFAULTS)

    _inject_mock_module("backtest")
    sys.modules["backtest.tune_15m"] = mock_tune
    sys.modules["psycopg2"] = mock_pg

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_cur.fetchone.return_value = None
    mock_pg.connect.return_value = mock_conn

    yield mock_pg, mock_conn, mock_cur

    sys.modules.pop("backtest.tune_15m", None)
    sys.modules.pop("backtest", None)
    sys.modules.pop("psycopg2", None)


class TestSaveSnapshotsToDb:
    def test_coerces_numpy_types_to_plain_float(self, mock_db):
        _, _, mock_cur = mock_db

        np_snap = {
            "symbol": "BTC",
            "params": {"SHORT_WINDOW": 30},
            "score": 3.5,
            "sweep_name": "daily_tune",
            "period": "2024-01-01_2024-01-31",
            "sharpe": np.float64(2.1),
            "total_return_pct": np.float64(15.3),
            "max_drawdown_pct": np.float64(1.2),
            "profit_factor": np.float64(1.8),
            "win_rate_pct": np.float64(55.0),
            "num_trades": np.int64(42),
            "ret_dd_ratio": np.float64(12.75),
        }

        with patch.dict(os.environ, {"SUPABASE_DB_URL": "postgres://fake"}):
            result = common.save_snapshots_to_db([np_snap], is_active=False)

        assert result == 1
        insert_calls = [
            c for c in mock_cur.execute.call_args_list if "INSERT" in c[0][0]
        ]
        assert len(insert_calls) == 1
        values = insert_calls[0][0][1]
        for i, v in enumerate(values):
            assert not isinstance(v, (np.integer, np.floating)), (
                f"value at index {i} is {type(v).__name__}: {v}"
            )

    def test_is_active_false_skips_deactivate_and_sets_prev_id_none(self, mock_db):
        _, _, mock_cur = mock_db
        snap = {
            "symbol": "BTC",
            "params": {"SHORT_WINDOW": 30},
            "score": 3.5,
            "sweep_name": "daily_tune",
            "period": "2024-01-01_2024-01-31",
        }

        with patch.dict(os.environ, {"SUPABASE_DB_URL": "postgres://fake"}):
            common.save_snapshots_to_db([snap], is_active=False)

        sqls = [c[0][0] for c in mock_cur.execute.call_args_list]
        assert not any("UPDATE" in s and "is_active" in s for s in sqls)
        assert not any("SELECT" in s and "is_active" in s for s in sqls)
        insert_calls = [
            c for c in mock_cur.execute.call_args_list if "INSERT" in c[0][0]
        ]
        values = insert_calls[0][0][1]
        assert values[12] is None

    def test_returns_zero_when_db_url_not_set(self, mock_db):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUPABASE_DB_URL", None)
            result = common.save_snapshots_to_db([{"symbol": "BTC", "params": {}}])
        assert result == 0

import os
import sys
from datetime import datetime, timezone

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import common  # noqa: E402


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





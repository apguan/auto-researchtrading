import os
import pytest
from unittest.mock import patch, MagicMock
from live_trading_bot.config.settings import Settings, _load_active_db_params, _apply_db_params


class TestLoadActiveDbParams:
    def test_no_db_url_returns_empty(self):
        with patch.dict(os.environ, {"SUPABASE_DB_URL": ""}, clear=False):
            os.environ.pop("SUPABASE_DB_URL", None)
            result = _load_active_db_params()
            assert result == {}

    def test_db_unreachable_returns_empty(self):
        import psycopg2

        with patch.dict(os.environ, {"SUPABASE_DB_URL": "postgresql://fake:fake@localhost/fake"}):
            with patch("psycopg2.connect", side_effect=Exception("connection refused")):
                result = _load_active_db_params()
                assert result == {}

    def test_no_active_row_returns_empty(self):
        with patch.dict(os.environ, {"SUPABASE_DB_URL": "postgresql://fake:fake@localhost/fake"}):
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = None
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

            with patch("psycopg2.connect", return_value=mock_conn):
                result = _load_active_db_params()
                assert result == {}

    def test_active_row_returns_params(self):
        from constants import PARAM_COLUMNS, INT_PARAMS

        row = ["BTC,ETH"]
        for name in PARAM_COLUMNS:
            if name == "RSI_BULL":
                row.append(50)
            elif name == "BASE_POSITION_PCT":
                row.append(0.10)
            elif name == "COOLDOWN_BARS":
                row.append(5)
            elif name == "ATR_STOP_MULT":
                row.append(4.0)
            else:
                row.append(1 if name in INT_PARAMS else 0.5)

        with patch.dict(os.environ, {"SUPABASE_DB_URL": "postgresql://fake:fake@localhost/fake"}):
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = tuple(row)
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

            with patch("psycopg2.connect", return_value=mock_conn):
                result = _load_active_db_params()
                assert len(result) == len(PARAM_COLUMNS) + 1  # +1 for TRADING_PAIRS from symbol column
                assert result["TRADING_PAIRS"] == ["BTC", "ETH"]
                assert result["RSI_BULL"] == 50
                assert result["BASE_POSITION_PCT"] == 0.10
                assert result["COOLDOWN_BARS"] == 5
                assert result["ATR_STOP_MULT"] == 4.0

    def test_int_params_cast_correctly(self):
        from constants import INT_PARAMS, PARAM_COLUMNS

        row = ["BTC,ETH"]
        for name in PARAM_COLUMNS:
            row.append(42 if name in INT_PARAMS else 3.14)

        with patch.dict(os.environ, {"SUPABASE_DB_URL": "postgresql://fake:fake@localhost/fake"}):
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = tuple(row)
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

            with patch("psycopg2.connect", return_value=mock_conn):
                result = _load_active_db_params()
                for name in INT_PARAMS:
                    assert isinstance(result[name], int), f"{name} should be int, got {type(result[name])}"
                    assert result[name] == 42
                for name in PARAM_COLUMNS:
                    if name not in INT_PARAMS:
                        assert isinstance(result[name], float)
                        assert result[name] == 3.14


class TestApplyDbParams:
    def test_no_params_no_change(self):
        s = Settings()
        orig_rsi = s.RSI_BULL
        orig_atr = s.ATR_STOP_MULT

        with patch("live_trading_bot.config.settings._load_active_db_params", return_value={}):
            _apply_db_params(s)

        assert s.RSI_BULL == orig_rsi
        assert s.ATR_STOP_MULT == orig_atr

    def test_db_params_override_defaults(self):
        s = Settings()

        db_overrides = {
            "RSI_BULL": 55.0,
            "RSI_BEAR": 45.0,
            "ATR_STOP_MULT": 7.0,
            "COOLDOWN_BARS": 2,
            "BASE_POSITION_PCT": 0.05,
        }

        with patch("live_trading_bot.config.settings._load_active_db_params", return_value=db_overrides):
            _apply_db_params(s)

        assert s.RSI_BULL == 55.0
        assert s.RSI_BEAR == 45.0
        assert s.ATR_STOP_MULT == 7.0
        assert s.COOLDOWN_BARS == 2
        assert s.BASE_POSITION_PCT == 0.05

    def test_unknown_db_param_ignored(self):
        s = Settings()
        orig = s.MAX_LEVERAGE

        with patch("live_trading_bot.config.settings._load_active_db_params", return_value={"NOT_A_REAL_PARAM": 999}):
            _apply_db_params(s)

        assert s.MAX_LEVERAGE == orig
        assert not hasattr(s, "NOT_A_REAL_PARAM")


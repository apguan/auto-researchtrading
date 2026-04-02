from live_trading_bot.execution.signal_state import SignalState


class TestSignalState:
    def test_update_signal_stores_target(self):
        s = SignalState()
        s.update_signal("BTC", 1000.0, 50.0, 50000.0, None)
        assert s.get_target("BTC") == 1000.0

    def test_get_direction_long(self):
        s = SignalState()
        s.update_signal("BTC", 1000.0, 50.0, 50000.0, None)
        assert s.get_direction("BTC") == 1

    def test_get_direction_short(self):
        s = SignalState()
        s.update_signal("BTC", -1000.0, 50.0, 50000.0, None)
        assert s.get_direction("BTC") == -1

    def test_get_direction_flat(self):
        s = SignalState()
        s.update_signal("BTC", 0.0, 50.0, 50000.0, None)
        assert s.get_direction("BTC") == 0

    def test_update_peak_trough_increments(self):
        s = SignalState()
        s.update_signal("BTC", 1000.0, 50.0, 50000.0, None)
        s.update_peak_trough("BTC", 51000.0)
        assert s.peak_prices["BTC"] == 51000.0
        s.update_peak_trough("BTC", 49000.0)
        assert s.trough_prices["BTC"] == 49000.0

    def test_update_peak_trough_unknown_symbol_ignored(self):
        s = SignalState()
        s.update_peak_trough("DOGE", 100.0)

    def test_clear_signal_removes_all_tracking(self):
        s = SignalState()
        s.update_signal("BTC", 1000.0, 50.0, 50000.0, None)
        s.clear_signal("BTC")
        assert s.get_target("BTC") == 0.0
        assert s.get_direction("BTC") == 0
        assert "BTC" not in s.signal_atr
        assert "BTC" not in s.signal_entry

    def test_update_signal_resets_peak_trough(self):
        s = SignalState()
        s.update_signal("BTC", 1000.0, 50.0, 50000.0, None)
        s.update_peak_trough("BTC", 60000.0)
        assert s.peak_prices["BTC"] == 60000.0
        s.update_signal("BTC", 2000.0, 60.0, 55000.0, None)
        assert s.peak_prices["BTC"] == 55000.0
        assert s.trough_prices["BTC"] == 55000.0

    def test_get_target_unknown_symbol(self):
        s = SignalState()
        assert s.get_target("DOGE") == 0.0

    def test_get_direction_unknown_symbol(self):
        s = SignalState()
        assert s.get_direction("DOGE") == 0

    def test_is_in_cooldown_blocks_then_allows(self):
        s = SignalState()
        assert s.is_in_cooldown("BTC", 3) is False

        s.bar_count = 10
        s.record_exit("BTC", 10)

        for bar in (10, 11, 12):
            s.bar_count = bar
            assert s.is_in_cooldown("BTC", 3) is True

        s.bar_count = 13
        assert s.is_in_cooldown("BTC", 3) is False

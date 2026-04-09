import json
import pytest

from live_trading_bot.exchange.dry_run_ledger import DryRunLedger


@pytest.fixture
def ledger(tmp_path):
    return DryRunLedger(
        path=str(tmp_path / "state.json"),
        initial_equity=10_000.0,
    )


class TestLedgerPersistence:
    def test_save_creates_file(self, ledger, tmp_path):
        ledger.open_position("BTC", True, 0.1, 50000.0)
        ledger.save()

        raw = json.loads((tmp_path / "state.json").read_text())
        assert raw["initial_equity"] == 10_000.0
        assert raw["realized_pnl"] == 0.0
        assert "BTC" in raw["positions"]

    def test_load_restores_positions(self, ledger, tmp_path):
        ledger.open_position("BTC", True, 0.1, 50000.0)
        ledger.open_position("ETH", False, 1.0, 3000.0)
        ledger.add_realized_pnl(150.0)
        ledger.save()

        fresh = DryRunLedger(
            path=str(tmp_path / "state.json"),
            initial_equity=10_000.0,
        )
        recovered = fresh.load()

        assert recovered is True
        assert "BTC" in fresh.positions
        assert fresh.positions["BTC"].is_long is True
        assert fresh.positions["BTC"].size == 0.1
        assert fresh.positions["BTC"].entry_price == 50000.0
        assert "ETH" in fresh.positions
        assert fresh.positions["ETH"].is_long is False
        assert fresh.realized_pnl == pytest.approx(150.0)

    def test_load_returns_false_when_no_file(self, ledger):
        assert ledger.load() is False

    def test_load_returns_false_on_corrupt_file(self, ledger, tmp_path):
        (tmp_path / "state.json").write_text("not json{{{")

        assert ledger.load() is False
        assert ledger.realized_pnl == 0.0
        assert ledger.positions == {}

    def test_pnl_persists_across_restarts(self, ledger, tmp_path):
        ledger.open_position("BTC", True, 0.1, 50000.0)
        ledger.close_position("BTC")
        ledger.add_realized_pnl(200.0)
        ledger.save()

        fresh = DryRunLedger(
            path=str(tmp_path / "state.json"),
            initial_equity=10_000.0,
        )
        fresh.load()

        assert fresh.realized_pnl == pytest.approx(200.0)
        assert fresh.positions == {}

    def test_save_is_atomic(self, ledger, tmp_path):
        ledger.open_position("BTC", True, 0.1, 50000.0)
        ledger.save()

        state_file = tmp_path / "state.json"
        tmp_file = tmp_path / "state.json.tmp"

        assert state_file.exists()
        assert not tmp_file.exists()

    def test_close_position_returns_old_position(self, ledger):
        ledger.open_position("BTC", True, 0.1, 50000.0)
        old = ledger.close_position("BTC")

        assert old is not None
        assert old.symbol == "BTC"
        assert "BTC" not in ledger.positions

    def test_close_nonexistent_returns_none(self, ledger):
        assert ledger.close_position("NONEXISTENT") is None

    def test_update_position_modifies_in_place(self, ledger):
        ledger.open_position("BTC", True, 0.1, 50000.0)
        ledger.update_position("BTC", 0.2, 51000.0)

        assert ledger.positions["BTC"].size == 0.2
        assert ledger.positions["BTC"].entry_price == 51000.0

    def test_initial_equity_preserved_across_load(self, ledger, tmp_path):
        ledger.open_position("BTC", True, 0.1, 50000.0)
        ledger.save()

        fresh = DryRunLedger(
            path=str(tmp_path / "state.json"),
            initial_equity=50_000.0,
        )
        fresh.load()

        assert fresh.initial_equity == 10_000.0

    def test_multiple_positions_survive_restart(self, ledger, tmp_path):
        for sym, is_long in [("BTC", True), ("ETH", False), ("SOL", True)]:
            ledger.open_position(sym, is_long, 0.5, 100.0)
        ledger.save()

        fresh = DryRunLedger(
            path=str(tmp_path / "state.json"),
            initial_equity=10_000.0,
        )
        fresh.load()

        assert len(fresh.positions) == 3
        assert fresh.positions["ETH"].is_long is False


class TestTransactions:
    def test_record_transaction_appends(self, ledger):
        txn = {"timestamp": "2026-01-01T00:00:00+00:00", "symbol": "BTC", "action": "open_long"}
        ledger.record_transaction(txn)

        assert len(ledger.transactions) == 1
        assert ledger.transactions[0] == txn

    def test_transactions_persist_across_save_load(self, ledger, tmp_path):
        ledger.open_position("BTC", True, 0.1, 50000.0)
        ledger.record_transaction({
            "timestamp": "2026-01-01T00:00:00+00:00",
            "symbol": "BTC",
            "action": "open_long",
            "side": "buy",
            "size": 0.1,
            "price": 50000.0,
            "pnl": 0.0,
            "realized_pnl_cumulative": 0.0,
        })
        ledger.save()

        fresh = DryRunLedger(
            path=str(tmp_path / "state.json"),
            initial_equity=10_000.0,
        )
        fresh.load()

        assert len(fresh.transactions) == 1
        assert fresh.transactions[0]["symbol"] == "BTC"
        assert fresh.transactions[0]["action"] == "open_long"

    def test_transaction_cap_at_500(self, ledger):
        for i in range(501):
            ledger.record_transaction({"seq": i})

        assert len(ledger.transactions) == 500
        assert ledger.transactions[0]["seq"] == 1
        assert ledger.transactions[-1]["seq"] == 500

    def test_close_position_and_add_realized_pnl_still_work(self, ledger):
        ledger.open_position("BTC", True, 0.1, 50000.0)
        old = ledger.close_position("BTC")
        assert old is not None
        assert old.symbol == "BTC"
        assert "BTC" not in ledger.positions

        ledger.add_realized_pnl(42.5)
        assert ledger.realized_pnl == pytest.approx(42.5)

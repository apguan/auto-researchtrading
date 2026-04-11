from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from live_trading_bot.kill_switch import _record_to_db
from live_trading_bot.storage.models import RiskEventType


@pytest.mark.asyncio
async def test_record_to_db_uses_snapshot_id_and_kill_switch_risk_event():
    db = AsyncMock()
    settings = SimpleNamespace(active_snapshot_id=123)
    results = [
        {
            "symbol": "BTC",
            "side": "sell",
            "size": 0.1,
            "fill": 51000.0,
            "fee": 2.55,
            "pnl": 100.0,
            "order_id": "order-1",
        },
        {
            "symbol": "ETH",
            "side": "buy",
            "size": 0.5,
            "fill": 3000.0,
            "fee": 0.75,
            "pnl": -25.0,
            "order_id": "order-2",
        },
    ]

    with patch("live_trading_bot.kill_switch.get_settings", return_value=settings):
        await _record_to_db(
            db,
            results,
            is_dry_run=False,
            wallet_address="0xTestWallet",
            equity=12345.67,
        )

    db.connect.assert_awaited_once()
    assert db.insert_trade.await_count == 2
    first_trade = db.insert_trade.await_args_list[0].args[0]
    second_trade = db.insert_trade.await_args_list[1].args[0]
    assert first_trade.symbol == "BTC"
    assert first_trade.side == "sell"
    assert first_trade.snapshot_id == 123
    assert second_trade.symbol == "ETH"
    assert second_trade.side == "buy"
    assert second_trade.snapshot_id == 123

    db.insert_risk_event.assert_awaited_once()
    risk_event = db.insert_risk_event.await_args.args[0]
    assert risk_event.event_type == RiskEventType.MANUAL_KILL_SWITCH.value
    assert "0xTestWallet" in risk_event.details
    assert "BTC" in risk_event.details
    assert "ETH" in risk_event.details
    assert "12,345.67" in risk_event.details

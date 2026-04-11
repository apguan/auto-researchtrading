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
        }
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
    db.insert_trade.assert_awaited_once()
    trade = db.insert_trade.await_args.args[0]
    assert trade.symbol == "BTC"
    assert trade.side == "sell"
    assert trade.snapshot_id == 123

    db.insert_risk_event.assert_awaited_once()
    risk_event = db.insert_risk_event.await_args.args[0]
    assert risk_event.event_type == RiskEventType.MANUAL_KILL_SWITCH.value
    assert "0xTestWallet" in risk_event.details
    assert "BTC" in risk_event.details

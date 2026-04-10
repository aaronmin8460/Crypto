import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pandas as pd

from app.config.settings import AppSettings
from app.services.bot import TradingBot
from app.services.persistence import Persistence
from app.services.strategy import SignalResult


def sample_bars_df() -> pd.DataFrame:
    rows = []
    for i in range(60):
        rows.append(
            {
                "Date": datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc) + pd.Timedelta(hours=i),
                "Open": 100.0 + i,
                "High": 101.0 + i,
                "Low": 99.0 + i,
                "Close": 100.0 + i,
                "Volume": 1.0,
            }
        )
    return pd.DataFrame(rows)


def broker_timestamp() -> str:
    local_now = datetime.now().astimezone().replace(microsecond=0)
    return local_now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def broker_timestamp_minutes_ago(minutes: int) -> str:
    local_now = datetime.now().astimezone().replace(microsecond=0) - pd.Timedelta(minutes=minutes)
    return local_now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def valid_order(
    symbol: str = "BTC/USD",
    side: str = "buy",
    status: str = "accepted",
    submitted_at: str | None = None,
) -> dict[str, str]:
    return {
        "id": str(uuid4()),
        "symbol": symbol,
        "side": side,
        "status": status,
        "submitted_at": submitted_at or broker_timestamp(),
        "filled_avg_price": "200.0",
        "filled_qty": "0.01",
        "notional": "200.0",
    }


def test_bot_run_once_places_buy_after_broker_confirms(tmp_path):
    settings = AppSettings(
        alpaca_api_key="test-key",
        alpaca_secret_key="test-secret",
        default_symbols=["BTC/USD"],
        cooldown_seconds_per_symbol=900,
        persistence_db_path=str(tmp_path / "bot_state.db"),
        trading_enabled=True,
    )

    data_service = AsyncMock()
    trading_service = AsyncMock()
    confirmed_order = valid_order()
    trading_service.get_account.return_value = {"cash": "200", "equity": "200", "status": "ACTIVE"}
    trading_service.list_positions.return_value = []
    trading_service.list_orders.side_effect = [[], [confirmed_order]]
    trading_service.submit_market_buy_notional.return_value = confirmed_order

    bot = TradingBot(settings, data_service, trading_service)
    bot.data_service.fetch_bars = AsyncMock(return_value=sample_bars_df())

    with patch("app.services.bot.evaluate_signal", return_value=SignalResult(signal="BUY", reason="crossed up")):
        result = asyncio.run(bot.run_once())

    assert result["results"][0]["signal"] == "BUY"
    assert result["results"][0]["reason"] == "buy order confirmed by broker"
    assert result["results"][0]["broker_order_accepted"] is True
    assert result["results"][0]["order"]["id"] == confirmed_order["id"]
    assert bot.state.last_order_by_symbol["BTC/USD"]["source"] == "broker"
    assert bot.state.last_order_by_symbol["BTC/USD"]["confirmation_path"] == "broker.list_orders"
    assert not bot.state.can_trade("BTC/USD")


def test_reconcile_broker_state_restores_positions_and_open_orders(tmp_path):
    settings = AppSettings(
        alpaca_api_key="test-key",
        alpaca_secret_key="test-secret",
        default_symbols=["BTC/USD"],
        persistence_db_path=str(tmp_path / "bot_state.db"),
        trading_enabled=True,
    )
    data_service = AsyncMock()
    trading_service = AsyncMock()
    trading_service.get_account.return_value = {"cash": "500", "equity": "500", "status": "ACTIVE"}
    trading_service.list_positions.return_value = [
        {
            "symbol": "BTC/USD",
            "market_value": "500",
            "current_price": "50",
            "avg_entry_price": "45",
            "qty": "10",
        }
    ]
    broker_order = valid_order(status="accepted")
    trading_service.list_orders.return_value = [broker_order]

    persistence = Persistence(settings)
    bot = TradingBot(settings, data_service, trading_service, persistence=persistence)

    summary = asyncio.run(bot.reconcile_broker_state())

    assert "BTC/USD" in bot.state.open_orders
    assert bot.state.open_orders["BTC/USD"]["id"] == broker_order["id"]
    assert bot.state.position_entry_price["BTC/USD"] == 45.0
    assert bot.state.total_portfolio_exposure_usd == 500.0
    assert bot.state.confirmed_positions == 1
    assert bot.state.confirmed_open_orders == 1
    assert summary["broker_state_consistent"] is True


def test_open_order_prevents_duplicate_buy():
    settings = AppSettings(
        alpaca_api_key="test-key",
        alpaca_secret_key="test-secret",
        default_symbols=["BTC/USD"],
        trading_enabled=True,
    )
    data_service = AsyncMock()
    trading_service = AsyncMock()
    trading_service.get_account.return_value = {"cash": "200", "equity": "200", "status": "ACTIVE"}
    trading_service.list_positions.return_value = []
    trading_service.list_orders.return_value = [
        valid_order(status="new", submitted_at=broker_timestamp_minutes_ago(60))
    ]
    trading_service.submit_market_buy_notional.return_value = valid_order()

    bot = TradingBot(settings, data_service, trading_service)
    bot.data_service.fetch_bars = AsyncMock(return_value=sample_bars_df())

    with patch("app.services.bot.evaluate_signal", return_value=SignalResult(signal="BUY", reason="crossed up")):
        result = asyncio.run(bot.run_once())

    assert result["results"][0]["reason"] == "open order pending"
    assert result["results"][0]["signal"] == "HOLD"
    trading_service.submit_market_buy_notional.assert_not_awaited()

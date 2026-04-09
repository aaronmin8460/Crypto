import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from app.config.settings import AppSettings
from app.services.bot import TradingBot
from app.services.persistence import Persistence


def test_bot_run_once_places_buy_and_cools_down():
    settings = AppSettings(
        alpaca_api_key="test-key",
        alpaca_secret_key="test-secret",
        default_symbols=["BTC/USD"],
        cooldown_seconds_per_symbol=900,
        trading_enabled=True,
    )

    data_service = AsyncMock()
    trading_service = AsyncMock()
    trading_service.get_account.return_value = {"cash": "200", "status": "ACTIVE"}
    trading_service.list_positions.return_value = []
    trading_service.submit_market_buy_notional.return_value = {"id": "order123"}

    bot = TradingBot(settings, data_service, trading_service)
    bot.data_service.fetch_bars = AsyncMock()
    bot.data_service.fetch_bars.return_value = AsyncMock()

    async def fake_fetch(*args, **kwargs):
        import pandas as pd

        now = datetime.now(timezone.utc)
        bars = [
            {"Date": now, "Close": 100.0}
            for _ in range(59)
        ]
        bars.append({"Date": now, "Close": 200.0})
        return pd.DataFrame(bars)

    bot.data_service.fetch_bars.side_effect = fake_fetch

    result = asyncio.run(bot.run_once())

    assert result["results"][0]["signal"] == "BUY"
    assert result["results"][0]["order"]["id"] == "order123"
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
    trading_service.list_positions.return_value = [
        {
            "symbol": "BTC/USD",
            "market_value": "500",
            "current_price": "50",
            "avg_entry_price": "45",
            "qty": "10",
        }
    ]
    trading_service.list_orders.return_value = [
        {"id": "open1", "symbol": "BTC/USD", "status": "new", "side": "buy"}
    ]

    persistence = Persistence(settings)
    bot = TradingBot(settings, data_service, trading_service, persistence=persistence)

    asyncio.run(bot.reconcile_broker_state())

    assert "BTC/USD" in bot.state.open_orders
    assert bot.state.position_entry_price["BTC/USD"] == 45.0
    assert bot.state.total_portfolio_exposure_usd == 500.0


def test_open_order_prevents_duplicate_buy():
    settings = AppSettings(
        alpaca_api_key="test-key",
        alpaca_secret_key="test-secret",
        default_symbols=["BTC/USD"],
        trading_enabled=True,
    )
    data_service = AsyncMock()
    trading_service = AsyncMock()
    trading_service.get_account.return_value = {"cash": "200", "status": "ACTIVE"}
    trading_service.list_positions.return_value = []
    trading_service.list_orders.return_value = [{"symbol": "BTC/USD", "status": "new", "side": "buy"}]
    trading_service.submit_market_buy_notional.return_value = {"id": "order123"}

    bot = TradingBot(settings, data_service, trading_service)

    async def fake_fetch(*args, **kwargs):
        import pandas as pd
        now = datetime.now(timezone.utc)
        bars = [{"Date": now, "Close": 100.0} for _ in range(60)]
        return pd.DataFrame(bars)

    bot.data_service.fetch_bars.side_effect = fake_fetch

    result = asyncio.run(bot.run_once())

    assert result["results"][0]["reason"] == "open order pending"
    assert result["results"][0]["signal"] == "HOLD"

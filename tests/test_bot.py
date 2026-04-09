import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from app.config.settings import AppSettings
from app.services.bot import TradingBot


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

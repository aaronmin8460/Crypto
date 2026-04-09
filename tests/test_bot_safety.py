from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import pandas as pd

from app.config.settings import AppSettings
from app.services.alpaca_crypto_data import AlpacaCryptoData
from app.services.alpaca_trading import AlpacaTrading
from app.services.bot import TradingBot
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


def test_live_trading_safety_gate():
    settings = AppSettings(broker_mode="live", trading_enabled=True, allow_live_trading=False)

    assert settings.is_live_mode is True
    assert settings.trading_allowed is False
    assert settings.alpaca_base_url == "https://api.alpaca.markets"
    assert settings.paper_trading is False


def test_no_order_submitted_when_trading_disabled():
    settings = AppSettings(broker_mode="paper", trading_enabled=False)
    bot = TradingBot(settings, AsyncMock(spec=AlpacaCryptoData), AsyncMock(spec=AlpacaTrading))
    bot.state.daily_order_date = date.today()
    bot.state.last_cash = 1000.0

    bot.data_service.fetch_bars = AsyncMock(return_value=sample_bars_df())
    bot.trading_service.get_account.return_value = {"cash": "1000", "status": "ACTIVE"}
    bot.trading_service.list_positions.return_value = []
    bot.trading_service.submit_market_buy_notional = AsyncMock()

    with patch("app.services.bot.evaluate_signal", return_value=SignalResult(signal="BUY", reason="crossed up")):
        result = asyncio.run(bot.run_once())

    assert result["results"][0]["reason"] == "trading disabled"
    bot.trading_service.submit_market_buy_notional.assert_not_awaited()


def test_cooldown_enforcement_blocks_trade():
    settings = AppSettings(broker_mode="paper", trading_enabled=True)
    bot = TradingBot(settings, AsyncMock(spec=AlpacaCryptoData), AsyncMock(spec=AlpacaTrading))
    bot.state.daily_order_date = date.today()
    bot.state.last_cash = 1000.0
    bot.state.cooldowns["BTC/USD"] = datetime.now(timezone.utc) + pd.Timedelta(seconds=600)

    bot.trading_service.get_account.return_value = {"cash": "1000", "status": "ACTIVE"}
    bot.trading_service.list_positions.return_value = []

    with patch("app.services.bot.evaluate_signal", return_value=SignalResult(signal="BUY", reason="crossed up")):
        result = asyncio.run(bot.run_once())

    assert result["results"][0]["reason"] == "cooldown active"


def test_daily_order_limit_reached():
    settings = AppSettings(broker_mode="paper", trading_enabled=True, max_daily_orders=1)
    bot = TradingBot(settings, AsyncMock(spec=AlpacaCryptoData), AsyncMock(spec=AlpacaTrading))
    bot.state.daily_order_date = date.today()
    bot.state.daily_order_count = 1
    bot.state.last_cash = 1000.0

    bot.trading_service.get_account.return_value = {"cash": "1000", "status": "ACTIVE"}
    bot.trading_service.list_positions.return_value = []

    with patch("app.services.bot.evaluate_signal", return_value=SignalResult(signal="BUY", reason="crossed up")):
        result = asyncio.run(bot.run_once())

    assert result["results"][0]["reason"] == "daily order limit reached"


def test_max_daily_loss_halts_trading():
    settings = AppSettings(broker_mode="paper", trading_enabled=True, max_daily_loss_usd=150)
    bot = TradingBot(settings, AsyncMock(spec=AlpacaCryptoData), AsyncMock(spec=AlpacaTrading))
    bot.state.daily_order_date = date.today()
    bot.state.daily_realized_pnl = -151.0
    bot.state.last_cash = 1000.0

    bot.trading_service.get_account.return_value = {"cash": "1000", "status": "ACTIVE"}
    bot.trading_service.list_positions.return_value = []

    with patch("app.services.bot.evaluate_signal", return_value=SignalResult(signal="BUY", reason="crossed up")):
        result = asyncio.run(bot.run_once())

    assert bot.state.halted_reason == "max daily loss exceeded"
    assert result["results"][0]["reason"] == "halted by max daily loss"


def test_bot_halt_and_resume_changes_state():
    settings = AppSettings(broker_mode="paper", trading_enabled=True)
    bot = TradingBot(settings, AsyncMock(spec=AlpacaCryptoData), AsyncMock(spec=AlpacaTrading))

    asyncio.run(bot.halt("manual stop"))
    assert bot.state.halted_reason == "manual stop"

    asyncio.run(bot.resume())
    assert bot.state.halted_reason is None


def test_bot_start_is_idempotent():
    settings = AppSettings(broker_mode="paper", trading_enabled=True)
    bot = TradingBot(settings, AsyncMock(spec=AlpacaCryptoData), AsyncMock(spec=AlpacaTrading))
    bot._task = None

    with patch("app.services.bot.asyncio.create_task", return_value=AsyncMock()) as create_task:
        asyncio.run(bot.start())
        asyncio.run(bot.start())

    assert create_task.call_count == 1

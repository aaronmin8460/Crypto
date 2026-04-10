from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
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

    bot.data_service.fetch_bars = AsyncMock(return_value=sample_bars_df())
    bot.trading_service.get_account.return_value = {"cash": "1000", "equity": "1000", "status": "ACTIVE"}
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
    bot.state.cooldowns["BTC/USD"] = datetime.now(timezone.utc) + pd.Timedelta(seconds=600)

    bot.data_service.fetch_bars = AsyncMock(return_value=sample_bars_df())
    bot.trading_service.get_account.return_value = {"cash": "1000", "equity": "10000", "status": "ACTIVE"}
    bot.trading_service.list_positions.return_value = []
    bot.trading_service.submit_market_buy_notional = AsyncMock(return_value={"filled_avg_price": "100.0"})

    with patch("app.services.bot.evaluate_signal", return_value=SignalResult(signal="BUY", reason="crossed up")):
        result = asyncio.run(bot.run_once())

    assert result["results"][0]["reason"] == "cooldown active"


def test_daily_order_limit_reached():
    settings = AppSettings(broker_mode="paper", trading_enabled=True, max_daily_orders=1)
    bot = TradingBot(settings, AsyncMock(spec=AlpacaCryptoData), AsyncMock(spec=AlpacaTrading))
    bot.state.daily_order_date = date.today()
    bot.state.daily_order_count = 1

    bot.data_service.fetch_bars = AsyncMock(return_value=sample_bars_df())
    bot.trading_service.get_account.return_value = {"cash": "1000", "equity": "1000", "status": "ACTIVE"}
    bot.trading_service.list_positions.return_value = []

    with patch("app.services.bot.evaluate_signal", return_value=SignalResult(signal="BUY", reason="crossed up")):
        result = asyncio.run(bot.run_once())

    assert result["results"][0]["reason"] == "daily order limit reached"


def test_max_daily_loss_halts_trading():
    settings = AppSettings(broker_mode="paper", trading_enabled=True, max_daily_loss_usd=150)
    bot = TradingBot(settings, AsyncMock(spec=AlpacaCryptoData), AsyncMock(spec=AlpacaTrading))
    bot.state.daily_order_date = date.today()
    bot.state.day_peak_equity = 10151.0
    bot.state.last_equity = 10151.0
    bot.state.current_equity_drawdown_usd = 0.0
    bot.state.max_intraday_drawdown_usd = 0.0
    bot.state.daily_equity_drawdown_usd = 0.0

    bot.data_service.fetch_bars = AsyncMock(return_value=sample_bars_df())
    bot.trading_service.get_account.return_value = {"cash": "1000", "equity": "10000", "status": "ACTIVE"}
    bot.trading_service.list_positions.return_value = []
    bot.trading_service.submit_market_buy_notional = AsyncMock(return_value={"filled_avg_price": "100.0"})

    with patch("app.services.bot.evaluate_signal", return_value=SignalResult(signal="BUY", reason="crossed up")):
        result = asyncio.run(bot.run_once())

    assert bot.state.halted_reason == "max daily loss exceeded"
    assert result["results"][0]["reason"] == "trading halted: max daily loss exceeded"


def test_record_equity_change_initializes_drawdown_state():
    state = TradingBot(AppSettings(broker_mode="paper", trading_enabled=True), AsyncMock(), AsyncMock()).state
    state.record_equity_change(Decimal("100000"))

    assert state.day_peak_equity == 100000.0
    assert state.current_equity_drawdown_usd == 0.0
    assert state.max_intraday_drawdown_usd == 0.0
    assert state.daily_equity_drawdown_usd == 0.0


def test_record_equity_change_updates_peak_and_drawdown():
    state = TradingBot(AppSettings(broker_mode="paper", trading_enabled=True), AsyncMock(), AsyncMock()).state
    state.record_equity_change(Decimal("100000"))
    state.record_equity_change(Decimal("101000"))

    assert state.day_peak_equity == 101000.0
    assert state.current_equity_drawdown_usd == 0.0
    assert state.max_intraday_drawdown_usd == 0.0

    state.record_equity_change(Decimal("100200"))
    assert state.day_peak_equity == 101000.0
    assert state.current_equity_drawdown_usd == 800.0
    assert state.max_intraday_drawdown_usd == 800.0

    state.record_equity_change(Decimal("100500"))
    assert state.current_equity_drawdown_usd == 500.0
    assert state.max_intraday_drawdown_usd == 800.0


def test_resume_does_not_clear_daily_loss_latch():
    settings = AppSettings(broker_mode="paper", trading_enabled=True, max_daily_loss_usd=150)
    bot = TradingBot(settings, AsyncMock(spec=AlpacaCryptoData), AsyncMock(spec=AlpacaTrading))
    bot.state.daily_order_date = date.today()
    bot.state.risk_stop_latched = True
    bot.state.halted_reason = "max daily loss exceeded"

    asyncio.run(bot.resume())
    assert bot.state.halted_reason == "max daily loss exceeded"
    assert bot.state.risk_stop_latched is True


def test_reset_risk_clears_daily_loss_stop():
    settings = AppSettings(broker_mode="paper", trading_enabled=True)
    trading = AsyncMock(spec=AlpacaTrading)
    trading.get_account.return_value = {"cash": "1000", "equity": "100000", "status": "ACTIVE"}
    bot = TradingBot(settings, AsyncMock(spec=AlpacaCryptoData), trading)
    bot.state.risk_stop_latched = True
    bot.state.halted_reason = "max daily loss exceeded"

    asyncio.run(bot.reset_risk())

    assert bot.state.risk_stop_latched is False
    assert bot.state.halted_reason is None
    assert bot.state.day_peak_equity == 100000.0
    assert bot.state.current_equity_drawdown_usd == 0.0


def test_fake_order_response_does_not_update_state():
    settings = AppSettings(broker_mode="paper", trading_enabled=True)
    bot = TradingBot(settings, AsyncMock(spec=AlpacaCryptoData), AsyncMock(spec=AlpacaTrading))
    bot.state.daily_order_date = date.today()

    bot.data_service.fetch_bars = AsyncMock(return_value=sample_bars_df())
    bot.trading_service.get_account.return_value = {"cash": "1000", "equity": "100000", "status": "ACTIVE"}
    bot.trading_service.list_positions.return_value = []
    bot.trading_service.submit_market_buy_notional = AsyncMock(return_value={"filled_avg_price": "100.0"})  # Fake response

    with patch("app.services.bot.evaluate_signal", return_value=SignalResult(signal="BUY", reason="crossed up")):
        result = asyncio.run(bot.run_once())

    assert result["results"][0]["reason"] == "broker order validation failed"
    assert result["results"][0]["submission_attempted"] is True
    assert result["results"][0]["broker_order_accepted"] is False
    assert bot.state.daily_order_count == 0  # Not incremented
    assert len(bot.state.cooldowns) == 0  # No cooldown applied


def test_valid_order_response_updates_state():
    settings = AppSettings(broker_mode="paper", trading_enabled=True, default_symbols=["BTC/USD"])
    bot = TradingBot(settings, AsyncMock(spec=AlpacaCryptoData), AsyncMock(spec=AlpacaTrading))
    bot.state.daily_order_date = date.today()

    valid_order = {
        "id": "12345",
        "symbol": "BTC/USD",
        "side": "buy",
        "status": "accepted",
        "submitted_at": "2023-01-01T00:00:00Z",
        "filled_avg_price": "100.0",
    }

    bot.data_service.fetch_bars = AsyncMock(return_value=sample_bars_df())
    bot.trading_service.get_account.return_value = {"cash": "1000", "equity": "100000", "status": "ACTIVE"}
    bot.trading_service.list_positions.return_value = []
    bot.trading_service.list_orders = AsyncMock(return_value=[])
    bot.trading_service.submit_market_buy_notional = AsyncMock(return_value=valid_order)
    bot._extract_filled_price = lambda order, fallback: 100.0

    with patch("app.services.bot.evaluate_signal", return_value=SignalResult(signal="BUY", reason="crossed up")):
        result = asyncio.run(bot.run_once())

    assert result["results"][0]["reason"] == "buy order accepted by broker"
    assert result["results"][0]["submission_attempted"] is True
    assert result["results"][0]["broker_order_accepted"] is True
    assert result["results"][0]["broker_order_id"] == "12345"
    assert result["results"][0]["cooldown_applied"] is True
    assert bot.state.daily_order_count == 1  # Incremented
    assert "BTC/USD" in bot.state.cooldowns  # Cooldown applied


def test_broker_reconciliation_clears_stale_state():
    settings = AppSettings(broker_mode="paper", trading_enabled=True)
    bot = TradingBot(settings, AsyncMock(spec=AlpacaCryptoData), AsyncMock(spec=AlpacaTrading))
    bot.state.daily_order_date = date.today()
    bot.state.open_orders = {"BTC/USD": {"id": "fake", "symbol": "BTC/USD"}}  # Stale state

    bot.trading_service.list_positions.return_value = []
    bot.trading_service.list_orders.return_value = []  # Broker has no orders
    bot.trading_service.get_account.return_value = {"cash": "1000", "equity": "100000", "status": "ACTIVE"}

    asyncio.run(bot._reconcile_broker_state())

    assert bot.state.open_orders == {}  # Cleared


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

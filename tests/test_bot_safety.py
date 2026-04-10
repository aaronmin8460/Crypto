from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pandas as pd
from fastapi.testclient import TestClient

from app.api.app import app
from app.config.settings import AppSettings
from app.services.alpaca_crypto_data import AlpacaCryptoData
from app.services.alpaca_trading import AlpacaTrading
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
    **overrides,
) -> dict[str, str]:
    order = {
        "id": str(uuid4()),
        "symbol": symbol,
        "side": side,
        "status": status,
        "submitted_at": submitted_at or broker_timestamp(),
        "filled_avg_price": "100.0",
        "filled_qty": "0.01",
        "notional": "100.0",
    }
    order.update(overrides)
    return order


def fake_stale_order(symbol: str = "BTC/USD") -> dict[str, str]:
    return {
        "id": "12345",
        "symbol": symbol,
        "side": "buy",
        "status": "accepted",
        "submitted_at": "2023-01-01T00:00:00Z",
        "filled_avg_price": "100.0",
    }


def make_bot(tmp_path=None, **settings_overrides) -> TradingBot:
    settings_kwargs = {"broker_mode": "paper", "trading_enabled": True}
    if tmp_path is not None:
        settings_kwargs["persistence_db_path"] = str(tmp_path / "bot_state.db")
    settings_kwargs.update(settings_overrides)
    settings = AppSettings(**settings_kwargs)
    return TradingBot(
        settings,
        AsyncMock(spec=AlpacaCryptoData),
        AsyncMock(spec=AlpacaTrading),
    )


def test_live_trading_safety_gate():
    settings = AppSettings(broker_mode="live", trading_enabled=True, allow_live_trading=False)

    assert settings.is_live_mode is True
    assert settings.trading_allowed is False
    assert settings.alpaca_base_url == "https://api.alpaca.markets"
    assert settings.paper_trading is False


def test_no_order_submitted_when_trading_disabled():
    bot = make_bot(trading_enabled=False)
    bot.state.daily_order_date = date.today()

    bot.data_service.fetch_bars = AsyncMock(return_value=sample_bars_df())
    bot.trading_service.get_account.return_value = {"cash": "1000", "equity": "1000", "status": "ACTIVE"}
    bot.trading_service.list_positions.return_value = []
    bot.trading_service.list_orders.return_value = []
    bot.trading_service.submit_market_buy_notional = AsyncMock()

    with patch("app.services.bot.evaluate_signal", return_value=SignalResult(signal="BUY", reason="crossed up")):
        result = asyncio.run(bot.run_once())

    assert result["results"][0]["reason"] == "trading disabled"
    bot.trading_service.submit_market_buy_notional.assert_not_awaited()


def test_daily_order_limit_reached():
    bot = make_bot(max_daily_orders=1)
    bot.state.daily_order_date = date.today()

    bot.data_service.fetch_bars = AsyncMock(return_value=sample_bars_df())
    bot.trading_service.get_account.return_value = {"cash": "1000", "equity": "1000", "status": "ACTIVE"}
    bot.trading_service.list_positions.return_value = []
    bot.trading_service.list_orders.return_value = [
        valid_order(
            symbol="BTC/USD",
            side="buy",
            status="filled",
            submitted_at=broker_timestamp_minutes_ago(60),
        )
    ]

    with patch("app.services.bot.evaluate_signal", return_value=SignalResult(signal="BUY", reason="crossed up")):
        result = asyncio.run(bot.run_once())

    assert result["results"][0]["reason"] == "daily order limit reached"


def test_record_equity_change_updates_peak_and_drawdown():
    state = make_bot().state
    state.record_equity_change(Decimal("100000"))
    state.record_equity_change(Decimal("101000"))
    state.record_equity_change(Decimal("100200"))

    assert state.day_peak_equity == 101000.0
    assert state.current_equity_drawdown_usd == 800.0
    assert state.max_intraday_drawdown_usd == 800.0


def test_fake_order_response_does_not_update_state():
    bot = make_bot(default_symbols=["BTC/USD"])
    bot.state.daily_order_date = date.today()

    bot.data_service.fetch_bars = AsyncMock(return_value=sample_bars_df())
    bot.trading_service.get_account.return_value = {
        "cash": "1000",
        "equity": "100000",
        "status": "ACTIVE",
    }
    bot.trading_service.list_positions.return_value = []
    bot.trading_service.list_orders.return_value = []
    bot.trading_service.submit_market_buy_notional = AsyncMock(return_value=fake_stale_order())

    with patch("app.services.bot.evaluate_signal", return_value=SignalResult(signal="BUY", reason="crossed up")):
        result = asyncio.run(bot.run_once())

    assert result["results"][0]["reason"] == "broker order validation failed"
    assert result["results"][0]["submission_attempted"] is True
    assert result["results"][0]["broker_order_accepted"] is False
    assert bot.state.daily_order_count == 0
    assert bot.state.cooldowns == {}
    assert bot.state.last_order_by_symbol == {}
    assert bot.state.local_order_attempts_by_symbol == {}


def test_stale_fake_accepted_order_is_cleared_when_broker_empty():
    bot = make_bot()
    bot.state.daily_order_date = date.today()
    bot.state.last_order_by_symbol["BTC/USD"] = fake_stale_order()
    bot.state.cooldowns["BTC/USD"] = datetime.now(timezone.utc) + timedelta(minutes=5)
    bot.state.daily_order_count = 1
    bot.state.daily_symbol_trade_count = {"BTC/USD": 1}
    bot.state.position_entry_price = {"BTC/USD": 100.0}

    bot.trading_service.get_account.return_value = {
        "cash": "10000",
        "equity": "10000",
        "status": "ACTIVE",
    }
    bot.trading_service.list_positions.return_value = []
    bot.trading_service.list_orders.return_value = []

    summary = asyncio.run(bot.reconcile_broker_state())

    assert bot.state.last_order_by_symbol == {}
    assert bot.state.cooldowns == {}
    assert bot.state.daily_order_count == 0
    assert bot.state.daily_symbol_trade_count == {}
    assert bot.state.position_entry_price == {}
    assert summary["stale_orders_cleared"] == 1
    assert summary["cooldowns_cleared"] == 1
    assert summary["entry_prices_cleared"] == 1
    assert summary["stale_state_detected"] is True


def test_stale_cooldown_without_broker_backing_is_cleared():
    bot = make_bot()
    bot.state.cooldowns["BTC/USD"] = datetime.now(timezone.utc) + timedelta(minutes=5)
    bot.state.daily_order_date = date.today()

    bot.trading_service.get_account.return_value = {"cash": "1000", "equity": "1000", "status": "ACTIVE"}
    bot.trading_service.list_positions.return_value = []
    bot.trading_service.list_orders.return_value = []

    summary = asyncio.run(bot.reconcile_broker_state())

    assert bot.state.cooldowns == {}
    assert summary["cooldowns_cleared"] == 1


def test_daily_trade_counts_are_recomputed_from_broker_truth():
    bot = make_bot()
    bot.state.daily_order_date = date.today()
    bot.state.daily_order_count = 5
    bot.state.daily_symbol_trade_count = {"BTC/USD": 5}

    broker_orders = [
        valid_order(symbol="BTC/USD", side="buy"),
        valid_order(symbol="ETH/USD", side="sell"),
    ]
    bot.trading_service.get_account.return_value = {"cash": "1000", "equity": "1000", "status": "ACTIVE"}
    bot.trading_service.list_positions.return_value = []
    bot.trading_service.list_orders.return_value = broker_orders

    summary = asyncio.run(bot.reconcile_broker_state())

    assert bot.state.daily_order_count == 2
    assert bot.state.daily_symbol_trade_count == {"BTC/USD": 1, "ETH/USD": 1}
    assert summary["trade_counts_recomputed"] is True
    assert summary["daily_order_count_recomputed"] == 2


def test_reconcile_endpoint_returns_useful_summary():
    client = TestClient(app)
    reconcile_summary = {
        "stale_orders_cleared": 1,
        "cooldowns_cleared": 1,
        "entry_prices_cleared": 1,
        "trade_counts_recomputed": True,
        "positions_synced": 0,
        "confirmed_positions": 0,
        "open_orders_synced": 0,
        "confirmed_open_orders": 0,
        "daily_order_count_recomputed": 0,
        "daily_symbol_trade_count_recomputed": {},
        "stale_state_detected": True,
        "stale_state_cleared_count": 4,
        "untrusted_local_orders_discarded": 1,
        "invalid_broker_orders_ignored": 0,
        "broker_state_consistent": True,
        "state_last_reconciled_at": broker_timestamp(),
        "account": {"cash": "1000"},
        "positions": [],
        "confirmed_orders": [],
    }

    with patch.object(app.state.bot, "reconcile_broker_state", AsyncMock(return_value=reconcile_summary)) as reconcile_mock:
        with patch.object(app.state.bot.persistence, "save_state") as save_state_mock:
            response = client.post("/bot/reconcile-state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "state reconciled"
    assert payload["stale_orders_cleared"] == 1
    assert payload["cooldowns_cleared"] == 1
    assert payload["stale_state_cleared_count"] == 4
    assert "account" not in payload
    assert "confirmed_orders" not in payload
    reconcile_mock.assert_awaited_once()
    save_state_mock.assert_called_once()


def test_startup_reconciliation_repairs_stale_local_state_automatically(tmp_path):
    settings = AppSettings(
        broker_mode="paper",
        trading_enabled=True,
        persistence_db_path=str(tmp_path / "bot_state.db"),
    )
    persistence = Persistence(settings)
    seeded_bot = TradingBot(
        settings,
        AsyncMock(spec=AlpacaCryptoData),
        AsyncMock(spec=AlpacaTrading),
        persistence=persistence,
    )
    seeded_bot.state.daily_order_date = date.today()
    seeded_bot.state.last_order_by_symbol["BTC/USD"] = fake_stale_order()
    seeded_bot.state.cooldowns["BTC/USD"] = datetime.now(timezone.utc) + timedelta(minutes=5)
    seeded_bot.state.daily_order_count = 1
    seeded_bot.state.daily_symbol_trade_count = {"BTC/USD": 1}
    persistence.save_state(seeded_bot.state)

    trading_service = AsyncMock(spec=AlpacaTrading)
    trading_service.get_account.return_value = {"cash": "1000", "equity": "1000", "status": "ACTIVE"}
    trading_service.list_positions.return_value = []
    trading_service.list_orders.return_value = []
    bot = TradingBot(
        settings,
        AsyncMock(spec=AlpacaCryptoData),
        trading_service,
        persistence=Persistence(settings),
    )

    asyncio.run(bot.initialize())

    assert bot.state.last_order_by_symbol == {}
    assert bot.state.cooldowns == {}
    assert bot.state.daily_order_count == 0
    assert bot.state.broker_state_consistent is True
    assert bot.state.last_reconciled_at is not None


def test_status_cannot_continue_showing_fake_order_metadata_after_reconcile():
    bot = make_bot()
    fake_order = fake_stale_order()
    bot.state.last_order_by_symbol["BTC/USD"] = fake_order
    bot.state.open_orders["BTC/USD"] = fake_order
    bot.state.daily_order_date = date.today()

    bot.trading_service.get_account.return_value = {"cash": "1000", "equity": "1000", "status": "ACTIVE"}
    bot.trading_service.list_positions.return_value = []
    bot.trading_service.list_orders.return_value = []

    asyncio.run(bot.reconcile_broker_state())
    status = bot.status()

    assert status["last_order_by_symbol"] == {}
    assert status["open_orders"] == {}
    assert status["broker_state_consistent"] is True
    assert status["stale_state_detected"] is True


def test_real_broker_confirmed_order_remains_after_reconciliation():
    bot = make_bot()
    broker_order = valid_order(symbol="BTC/USD", side="buy")

    bot.trading_service.get_account.return_value = {"cash": "1000", "equity": "1000", "status": "ACTIVE"}
    bot.trading_service.list_positions.return_value = []
    bot.trading_service.list_orders.return_value = [broker_order]

    summary = asyncio.run(bot.reconcile_broker_state())

    assert bot.state.last_order_by_symbol["BTC/USD"]["id"] == broker_order["id"]
    assert bot.state.last_order_by_symbol["BTC/USD"]["source"] == "broker"
    assert bot.state.last_order_by_symbol["BTC/USD"]["confirmation_path"] == "broker.list_orders"
    assert bot.state.confirmed_open_orders == 1
    assert summary["stale_orders_cleared"] == 0


def test_reset_risk_does_not_preserve_bogus_order_state():
    bot = make_bot()
    bot.state.daily_order_date = date.today()
    bot.state.risk_stop_latched = True
    bot.state.halted_reason = "max daily loss exceeded"
    bot.state.last_order_by_symbol["BTC/USD"] = fake_stale_order()
    bot.state.cooldowns["BTC/USD"] = datetime.now(timezone.utc) + timedelta(minutes=5)
    bot.state.daily_order_count = 3
    bot.state.daily_symbol_trade_count = {"BTC/USD": 3}

    bot.trading_service.get_account.return_value = {"cash": "1000", "equity": "100000", "status": "ACTIVE"}
    bot.trading_service.list_positions.return_value = []
    bot.trading_service.list_orders.return_value = []

    asyncio.run(bot.reset_risk())

    assert bot.state.risk_stop_latched is False
    assert bot.state.halted_reason is None
    assert bot.state.last_order_by_symbol == {}
    assert bot.state.cooldowns == {}
    assert bot.state.daily_order_count == 0


def test_broker_state_consistent_only_when_local_state_matches_broker_truth():
    bot = make_bot()
    fake_order = fake_stale_order()
    bot.state.last_order_by_symbol["BTC/USD"] = fake_order
    bot.state.open_orders["BTC/USD"] = fake_order

    status_before = bot.status()
    assert status_before["broker_state_consistent"] is False

    bot.trading_service.get_account.return_value = {"cash": "1000", "equity": "1000", "status": "ACTIVE"}
    bot.trading_service.list_positions.return_value = []
    bot.trading_service.list_orders.return_value = []

    asyncio.run(bot.reconcile_broker_state())
    status_after = bot.status()

    assert status_after["broker_state_consistent"] is True
    assert status_after["last_order_by_symbol"] == {}

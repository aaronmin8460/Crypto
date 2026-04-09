from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.api.app import app


def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_run_once_endpoint_returns_structure():
    client = TestClient(app)
    fake_response = {
        "run_time": datetime.now(timezone.utc).isoformat(),
        "results": [],
        "account": {},
        "positions": [],
    }

    with patch.object(app.state.bot, "run_once", AsyncMock(return_value=fake_response)):
        response = client.post("/run-once")

    assert response.status_code == 200
    assert response.json()["results"] == []


def test_bot_control_endpoints():
    client = TestClient(app)
    with patch.object(app.state.bot, "start", AsyncMock()) as start_mock:
        response = client.post("/bot/start")
        assert response.status_code == 200
        assert response.json() == {"status": "started"}
        start_mock.assert_awaited_once()

    with patch.object(app.state.bot, "stop", AsyncMock()) as stop_mock:
        response = client.post("/bot/stop")
        assert response.status_code == 200
        assert response.json() == {"status": "stopped"}
        stop_mock.assert_awaited_once()


def test_bot_halt_and_resume_endpoints():
    client = TestClient(app)
    with patch.object(app.state.bot, "halt", AsyncMock()) as halt_mock:
        response = client.post("/bot/halt")
        assert response.status_code == 200
        assert response.json()["status"] == "halted"
        halt_mock.assert_awaited_once()

    with patch.object(app.state.bot, "resume", AsyncMock()) as resume_mock:
        response = client.post("/bot/resume")
        assert response.status_code == 200
        assert response.json() == {"status": "resumed"}
        resume_mock.assert_awaited_once()


def test_bot_log_summary_endpoint():
    client = TestClient(app)
    with patch.object(app.state.bot, "status", return_value={
        "running": False,
        "mode": "paper",
        "trading_enabled": False,
        "halted_reason": None,
        "last_run_time": None,
        "last_error": None,
        "last_results": {},
        "cooldowns": {},
        "risk_profile": {},
        "daily_order_count": 0,
        "daily_equity_drawdown_usd": 0.0,
        "last_signal_by_symbol": {},
        "last_order_by_symbol": {},
    }):
        response = client.get("/bot/log-summary")

    assert response.status_code == 200
    assert response.json()["mode"] == "paper"


def test_metrics_endpoint_returns_summary():
    client = TestClient(app)
    with patch.object(app.state.bot.persistence, "get_metrics", return_value={
        "total_trades": 3,
        "win_rate": 66.67,
        "average_gain_loss": 12.5,
        "cumulative_realized_pnl": 37.5,
    }):
        response = client.get("/metrics")

    assert response.status_code == 200
    assert response.json()["total_trades"] == 3


def test_journal_endpoint_returns_entries():
    client = TestClient(app)
    entry = {
        "id": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": "BTC/USD",
        "action": "BUY",
        "reason": "test",
        "entry_price": 100.0,
        "exit_price": None,
        "quantity": 1.0,
        "notional": 100.0,
        "realized_pnl": None,
        "drawdown": 0.0,
        "raw": {},
    }
    with patch.object(app.state.bot.persistence, "get_journal", return_value=[entry]):
        response = client.get("/journal")

    assert response.status_code == 200
    assert response.json()["entries"][0]["symbol"] == "BTC/USD"

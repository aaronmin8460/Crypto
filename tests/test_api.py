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

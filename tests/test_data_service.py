import asyncio
from unittest.mock import AsyncMock, patch

import pandas as pd

from app.config.settings import AppSettings
from app.services.alpaca_crypto_data import AlpacaCryptoData


class DummyResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class DummyClient:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None):
        return DummyResponse(self._payload)


def test_fetch_bars_normalizes_data():
    settings = AppSettings(
        alpaca_api_key="test-key",
        alpaca_secret_key="test-secret",
    )
    service = AlpacaCryptoData(settings)
    payload = {
        "bars": [
            {"t": "2025-01-01T00:00:00Z", "o": 30000, "h": 31000, "l": 29000, "c": 30500, "v": 12},
            {"t": "2025-01-01T01:00:00Z", "o": 30500, "h": 31500, "l": 30000, "c": 31000, "v": 14},
        ]
    }

    with patch("app.services.alpaca_crypto_data.httpx.AsyncClient", return_value=DummyClient(payload)):
        df = asyncio.run(service.fetch_bars("BTC/USD", timeframe="1H", limit=2))

    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
    assert df.iloc[0]["Close"] == 30500
    assert df.iloc[1]["Close"] == 31000

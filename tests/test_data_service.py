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
    # New v1beta3 format: bars is a dict with symbol keys
    payload = {
        "bars": {
            "BTC/USD": [
                {"t": "2025-01-01T00:00:00Z", "o": 30000, "h": 31000, "l": 29000, "c": 30500, "v": 12},
                {"t": "2025-01-01T01:00:00Z", "o": 30500, "h": 31500, "l": 30000, "c": 31000, "v": 14},
            ]
        }
    }

    with patch("app.services.alpaca_crypto_data.httpx.AsyncClient", return_value=DummyClient(payload)):
        df = asyncio.run(service.fetch_bars("BTC/USD", timeframe="1H", limit=2))

    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
    assert df.iloc[0]["Close"] == 30500
    assert df.iloc[1]["Close"] == 31000


def test_fetch_bars_eth_usd():
    """Test that ETH/USD works with the new v1beta3 endpoint."""
    settings = AppSettings(
        alpaca_api_key="test-key",
        alpaca_secret_key="test-secret",
    )
    service = AlpacaCryptoData(settings)
    payload = {
        "bars": {
            "ETH/USD": [
                {"t": "2025-01-01T00:00:00Z", "o": 1800, "h": 1850, "l": 1750, "c": 1825, "v": 100},
            ]
        }
    }

    with patch("app.services.alpaca_crypto_data.httpx.AsyncClient", return_value=DummyClient(payload)):
        df = asyncio.run(service.fetch_bars("ETH/USD", timeframe="1H", limit=1))

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert df.iloc[0]["Close"] == 1825


def test_fetch_bars_v1beta3_endpoint():
    """Test that the correct v1beta3 endpoint format is used."""
    settings = AppSettings(
        alpaca_api_key="test-key",
        alpaca_secret_key="test-secret",
    )
    service = AlpacaCryptoData(settings)
    payload = {
        "bars": {
            "BTC/USD": [
                {"t": "2025-01-01T00:00:00Z", "o": 30000, "h": 31000, "l": 29000, "c": 30500, "v": 12},
            ]
        }
    }

    # Capture the actual request made
    call_log = []

    class TrackingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None):
            call_log.append({"url": url, "params": params})
            return DummyResponse(payload)

    with patch("app.services.alpaca_crypto_data.httpx.AsyncClient", return_value=TrackingClient()):
        df = asyncio.run(service.fetch_bars("BTC/USD", timeframe="1H", limit=120))

    assert len(call_log) == 1
    assert "/v1beta3/crypto/us/bars" in call_log[0]["url"]
    assert call_log[0]["params"]["symbols"] == "BTC/USD"
    assert call_log[0]["params"]["timeframe"] == "1H"
    assert call_log[0]["params"]["limit"] == 120

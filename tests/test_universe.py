import asyncio
from unittest.mock import AsyncMock

from app.config.settings import AppSettings
from app.services.crypto_universe import CryptoUniverseService
from app.services.persistence import Persistence


def test_crypto_universe_discovery_normalizes_and_filters_symbols(tmp_path):
    settings = AppSettings(
        enable_dynamic_universe=True,
        universe_excluded_symbols=["DOGE/USD"],
        persistence_db_path=str(tmp_path / "bot_state.db"),
    )
    trading_service = AsyncMock()
    trading_service.list_assets.return_value = [
        {"symbol": "BTCUSD", "status": "active", "tradable": True},
        {"symbol": "btc/usd", "status": "active", "tradable": True},
        {"symbol": "ETH/USD", "status": "active", "tradable": True},
        {"symbol": "SOLUSDT", "status": "active", "tradable": True},
        {"symbol": "DOGEUSD", "status": "active", "tradable": True},
        {"symbol": "XRP/USD", "status": "inactive", "tradable": True},
        {"symbol": "UNIUSD", "status": "active", "tradable": False},
        {"symbol": "BAD//USD", "status": "active", "tradable": True},
    ]

    service = CryptoUniverseService(settings, trading_service)
    snapshot = asyncio.run(service.refresh_universe())

    assert snapshot.symbols == ["BTC/USD", "ETH/USD"]
    assert snapshot.raw_asset_count == 8
    assert snapshot.skipped_asset_count == 6


def test_crypto_universe_uses_persisted_cache_when_available(tmp_path):
    settings = AppSettings(
        enable_dynamic_universe=True,
        persistence_db_path=str(tmp_path / "bot_state.db"),
        universe_refresh_seconds=3600,
    )
    persistence = Persistence(settings)
    seed_trading_service = AsyncMock()
    seed_trading_service.list_assets.return_value = [
        {"symbol": "BTCUSD", "status": "active", "tradable": True},
        {"symbol": "ETHUSD", "status": "active", "tradable": True},
    ]
    service = CryptoUniverseService(settings, seed_trading_service, persistence=persistence)

    seeded_snapshot = asyncio.run(service.refresh_universe())
    assert seeded_snapshot.symbols == ["BTC/USD", "ETH/USD"]

    cold_trading_service = AsyncMock()
    cold_trading_service.list_assets.side_effect = RuntimeError("network unavailable")
    restored_service = CryptoUniverseService(settings, cold_trading_service, persistence=persistence)

    restored_snapshot = asyncio.run(restored_service.get_universe())

    assert restored_snapshot.symbols == ["BTC/USD", "ETH/USD"]

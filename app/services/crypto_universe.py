from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.config.settings import AppSettings
from app.services.alpaca_trading import AlpacaTrading
from app.services.persistence import Persistence
from app.utils.symbols import normalize_symbol

logger = logging.getLogger(__name__)


class UniverseSnapshot(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    fetched_at: datetime
    raw_asset_count: int = 0
    skipped_asset_count: int = 0
    source: str = "alpaca"


class CryptoUniverseService:
    """Discover and cache the tradable Alpaca crypto universe."""

    CACHE_KEY = "crypto_universe"

    def __init__(
        self,
        settings: AppSettings,
        trading_service: AlpacaTrading,
        persistence: Persistence | None = None,
    ) -> None:
        self.settings = settings
        self.trading_service = trading_service
        self.persistence = persistence
        self._cache: UniverseSnapshot | None = None

    def _is_cache_fresh(self, snapshot: UniverseSnapshot | None) -> bool:
        if snapshot is None:
            return False
        age = datetime.now(timezone.utc) - snapshot.fetched_at.astimezone(timezone.utc)
        return age <= timedelta(seconds=self.settings.universe_refresh_seconds)

    def _restore_snapshot(self, payload: dict[str, Any]) -> UniverseSnapshot | None:
        if not payload:
            return None
        try:
            return UniverseSnapshot(**payload)
        except Exception as exc:
            logger.warning("failed to restore cached crypto universe: %s", exc)
            return None

    def _load_persisted_snapshot(self) -> UniverseSnapshot | None:
        if self.persistence is None or not self.settings.universe_persist_cache:
            return None
        return self._restore_snapshot(
            self.persistence.load_universe_snapshot(cache_key=self.CACHE_KEY)
        )

    def _save_snapshot(self, snapshot: UniverseSnapshot) -> None:
        if self.persistence is None or not self.settings.universe_persist_cache:
            return
        self.persistence.save_universe_snapshot(
            snapshot.model_dump(mode="json"),
            cache_key=self.CACHE_KEY,
        )

    def _build_snapshot(self, assets: list[dict[str, Any]]) -> UniverseSnapshot:
        normalized_symbols: list[str] = []
        seen: set[str] = set()
        skipped = 0
        excluded = set(self.settings.universe_excluded_symbols)
        required_quote = self.settings.universe_quote_currency

        for asset in assets:
            if not isinstance(asset, dict):
                skipped += 1
                continue

            status = str(asset.get("status", "")).lower()
            if status and status != "active":
                skipped += 1
                continue

            if self.settings.universe_require_tradable and asset.get("tradable") is not True:
                skipped += 1
                continue

            raw_symbol = asset.get("symbol") or asset.get("name")
            symbol = normalize_symbol(raw_symbol, quote_currency=required_quote)
            if symbol is None or symbol in excluded or symbol in seen:
                skipped += 1
                continue

            seen.add(symbol)
            normalized_symbols.append(symbol)

        normalized_symbols.sort()
        if self.settings.universe_max_symbols > 0:
            normalized_symbols = normalized_symbols[: self.settings.universe_max_symbols]

        return UniverseSnapshot(
            symbols=normalized_symbols,
            fetched_at=datetime.now(timezone.utc),
            raw_asset_count=len(assets),
            skipped_asset_count=skipped,
            source="alpaca",
        )

    async def refresh_universe(self) -> UniverseSnapshot:
        assets = await self.trading_service.list_assets(status="active", asset_class="crypto")
        if not isinstance(assets, list):
            raise RuntimeError("unexpected asset discovery response from Alpaca")

        snapshot = self._build_snapshot(assets)
        self._cache = snapshot
        self._save_snapshot(snapshot)
        logger.info(
            "refreshed crypto universe raw_assets=%d symbols=%d skipped=%d",
            snapshot.raw_asset_count,
            len(snapshot.symbols),
            snapshot.skipped_asset_count,
        )
        return snapshot

    async def get_universe(self, force_refresh: bool = False) -> UniverseSnapshot:
        if not force_refresh and self._is_cache_fresh(self._cache):
            return self._cache

        if not force_refresh and self._cache is None:
            restored = self._load_persisted_snapshot()
            if self._is_cache_fresh(restored):
                self._cache = restored
                return restored

        try:
            return await self.refresh_universe()
        except Exception as exc:
            logger.warning("crypto universe refresh failed: %s", exc)
            if self._cache is not None:
                return self._cache
            restored = self._load_persisted_snapshot()
            if restored is not None:
                self._cache = restored
                return restored
            raise

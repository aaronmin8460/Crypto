from __future__ import annotations

import asyncio
import logging
from httpx import AsyncClient, HTTPError

from app.config.settings import AppSettings

logger = logging.getLogger(__name__)


class AlpacaTrading:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.headers = {
            "APCA-API-KEY-ID": self.settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": self.settings.alpaca_secret_key,
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, params: dict | None = None, json: dict | None = None) -> dict | list:
        if (
            method.upper() not in {"GET", "HEAD", "OPTIONS"}
            and self.settings.is_live_mode
            and not self.settings.allow_live_trading
        ):
            raise RuntimeError("live trading is disabled by configuration")

        url = f"{self.settings.alpaca_base_url}{path}"
        for attempt in range(2):
            try:
                async with AsyncClient(headers=self.headers, timeout=20.0) as client:
                    response = await client.request(method, url, params=params, json=json)
                if response.status_code >= 400:
                    raise RuntimeError(
                        f"Alpaca trading request failed: {response.status_code} {response.text}"
                    )
                return response.json()
            except HTTPError as exc:
                logger.warning("Alpaca request failed on attempt %d: %s", attempt + 1, exc)
                if attempt == 1:
                    raise RuntimeError(f"Alpaca trading request failed: {exc}") from exc
                await asyncio.sleep(1)

    async def get_account(self) -> dict:
        return await self._request("GET", "/v2/account")

    async def list_positions(self) -> list[dict]:
        return await self._request("GET", "/v2/positions")

    async def list_orders(self, status: str = "all", limit: int = 50) -> list[dict]:
        params = {"status": status, "limit": limit}
        return await self._request("GET", "/v2/orders", params=params)

    async def list_assets(self, status: str = "active", asset_class: str = "crypto") -> list[dict]:
        params = {"status": status, "asset_class": asset_class}
        return await self._request("GET", "/v2/assets", params=params)

    async def submit_market_buy_notional(self, symbol: str, notional: float) -> dict:
        payload = {
            "symbol": symbol,
            "side": "buy",
            "type": "market",
            "time_in_force": self.settings.trade_time_in_force,
            "notional": str(notional),
        }
        return await self._request("POST", "/v2/orders", json=payload)

    async def submit_market_sell_qty(self, symbol: str, qty: float) -> dict:
        payload = {
            "symbol": symbol,
            "side": "sell",
            "type": "market",
            "time_in_force": self.settings.trade_time_in_force,
            "qty": str(qty),
        }
        return await self._request("POST", "/v2/orders", json=payload)

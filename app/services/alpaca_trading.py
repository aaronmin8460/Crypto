from __future__ import annotations

import httpx

from app.config.settings import AppSettings


class AlpacaTrading:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.headers = {
            "APCA-API-KEY-ID": self.settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": self.settings.alpaca_secret_key,
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, params: dict | None = None, json: dict | None = None) -> dict | list:
        url = f"{self.settings.alpaca_base_url}{path}"
        async with httpx.AsyncClient(headers=self.headers, timeout=20.0) as client:
            response = await client.request(method, url, params=params, json=json)

        if response.status_code >= 400:
            raise RuntimeError(
                f"Alpaca trading request failed: {response.status_code} {response.text}"
            )
        return response.json()

    async def get_account(self) -> dict:
        return await self._request("GET", "/v2/account")

    async def list_positions(self) -> list[dict]:
        return await self._request("GET", "/v2/positions")

    async def list_orders(self, status: str = "all") -> list[dict]:
        params = {"status": status, "limit": 50}
        return await self._request("GET", "/v2/orders", params=params)

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

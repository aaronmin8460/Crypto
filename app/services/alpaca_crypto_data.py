from __future__ import annotations

from urllib.parse import quote

import httpx
import pandas as pd

from app.config.settings import AppSettings


class AlpacaCryptoData:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.headers = {
            "APCA-API-KEY-ID": self.settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": self.settings.alpaca_secret_key,
        }

    async def fetch_bars(self, symbol: str, timeframe: str | None = None, limit: int | None = None) -> pd.DataFrame:
        timeframe = timeframe or self.settings.default_timeframe
        limit = limit or self.settings.bar_limit
        symbol_token = quote(symbol, safe="")
        url = f"{self.settings.alpaca_data_base_url}/v2/crypto/{symbol_token}/bars"
        params = {"timeframe": timeframe, "limit": limit}

        async with httpx.AsyncClient(headers=self.headers, timeout=20.0) as client:
            response = await client.get(url, params=params)

        if response.status_code != 200:
            raise RuntimeError(
                f"crypto bars request failed: {response.status_code} {response.text}"
            )

        payload = response.json()
        bars = payload.get("bars")
        if not isinstance(bars, list) or not bars:
            raise ValueError("crypto bars response missing bars")

        df = pd.DataFrame(bars)
        if df.empty:
            raise ValueError("crypto bars response returned empty bars")

        df = df.rename(
            columns={
                "t": "Date",
                "o": "Open",
                "h": "High",
                "l": "Low",
                "c": "Close",
                "v": "Volume",
            }
        )
        df["Date"] = pd.to_datetime(df["Date"], utc=True)
        df = df[["Date", "Open", "High", "Low", "Close", "Volume"]].sort_values(
            "Date", ignore_index=True
        )
        return df

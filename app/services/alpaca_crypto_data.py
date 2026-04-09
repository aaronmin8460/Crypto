from __future__ import annotations

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
        url = f"{self.settings.alpaca_data_base_url}/v1beta3/crypto/us/bars"
        params = {"symbols": symbol, "timeframe": timeframe, "limit": limit}

        async with httpx.AsyncClient(headers=self.headers, timeout=20.0) as client:
            response = await client.get(url, params=params)

        if response.status_code != 200:
            raise RuntimeError(
                f"crypto bars request failed: {response.status_code} {response.text}"
            )

        payload = response.json()
        bars_data = payload.get("bars", {})
        if not isinstance(bars_data, dict) or symbol not in bars_data:
            raise ValueError(f"crypto bars response missing bars for symbol {symbol}")
        
        bars = bars_data[symbol]
        if not isinstance(bars, list) or not bars:
            raise ValueError(f"crypto bars response returned empty bars for {symbol}")

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

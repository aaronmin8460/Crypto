from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd

from app.config.settings import AppSettings

logger = logging.getLogger(__name__)


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
        
        # Calculate start and end times to ensure sufficient historical data
        # For 1H bars with limit=120, request 200 hours back to account for market gaps
        now = datetime.now(timezone.utc)
        if timeframe == "1H":
            # Request 200 hours back for 1H timeframe (extra buffer for market hours)
            lookback_hours = 200
        elif timeframe == "1D":
            # Request 250 days back for daily timeframe
            lookback_hours = 6000
        else:
            # Default: request time back based on limit (assume 1.5x multiplier for market gaps)
            lookback_hours = int(limit * 1.5)
        
        start_time = now - timedelta(hours=lookback_hours)
        end_time = now
        
        url = f"{self.settings.alpaca_data_base_url}/v1beta3/crypto/us/bars"
        params = {
            "symbols": symbol,
            "timeframe": timeframe,
            "limit": limit,
            "start": start_time.isoformat(),
            "end": end_time.isoformat(),
        }

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
        
        # Log if bars returned is low
        if len(df) < 51:
            logger.warning(
                f"insufficient bars for {symbol}: received {len(df)} bars "
                f"(need >=51 for SMA50). timeframe={timeframe}, limit={limit}, "
                f"start={start_time.isoformat()}, end={end_time.isoformat()}"
            )

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

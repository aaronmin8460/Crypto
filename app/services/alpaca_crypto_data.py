from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd

from app.config.settings import AppSettings
from app.utils.symbols import normalize_symbol, unique_symbols

logger = logging.getLogger(__name__)


class AlpacaCryptoData:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.headers = {
            "APCA-API-KEY-ID": self.settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": self.settings.alpaca_secret_key,
        }

    def _build_request_window(self, timeframe: str, limit: int) -> tuple[datetime, datetime]:
        now = datetime.now(timezone.utc)
        if timeframe == "1H":
            lookback_hours = max(200, limit * 2)
        elif timeframe == "1D":
            lookback_hours = max(6000, limit * 36)
        else:
            lookback_hours = max(int(limit * 1.5), limit + 24)
        return now - timedelta(hours=lookback_hours), now

    def _normalize_bars(self, bars: list[dict]) -> pd.DataFrame:
        df = pd.DataFrame(bars)
        if df.empty:
            return df

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
        return df[["Date", "Open", "High", "Low", "Close", "Volume"]].sort_values(
            "Date",
            ignore_index=True,
        )

    async def _request_bars_batch(
        self,
        symbols: Sequence[str],
        timeframe: str,
        limit: int,
    ) -> dict[str, pd.DataFrame]:
        normalized_symbols = unique_symbols(symbols, quote_currency=self.settings.universe_quote_currency)
        if not normalized_symbols:
            return {}

        start_time, end_time = self._build_request_window(timeframe, limit)
        url = f"{self.settings.alpaca_data_base_url}/v1beta3/crypto/us/bars"
        params = {
            "symbols": ",".join(normalized_symbols),
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
        if not isinstance(bars_data, dict):
            raise ValueError("crypto bars response missing bars payload")

        normalized_frames: dict[str, pd.DataFrame] = {}
        for raw_symbol, raw_bars in bars_data.items():
            normalized_symbol = normalize_symbol(
                raw_symbol,
                quote_currency=self.settings.universe_quote_currency,
            )
            if normalized_symbol is None or not isinstance(raw_bars, list) or not raw_bars:
                continue
            frame = self._normalize_bars(raw_bars)
            if frame.empty:
                continue
            normalized_frames[normalized_symbol] = frame

        requested_set = set(normalized_symbols)
        returned_set = set(normalized_frames)
        missing_symbols = sorted(requested_set - returned_set)
        logger.info(
            "crypto bar batch completed timeframe=%s limit=%d batch_size=%d requested_symbols=%d returned_symbols=%d missing_symbols=%d",
            timeframe,
            limit,
            len(normalized_symbols),
            len(normalized_symbols),
            len(normalized_frames),
            len(missing_symbols),
        )
        if missing_symbols:
            logger.warning(
                "crypto bar batch returned partial data timeframe=%s missing=%s",
                timeframe,
                missing_symbols[:10],
            )

        return normalized_frames

    async def fetch_bars_batch(
        self,
        symbols: Sequence[str],
        timeframe: str | None = None,
        limit: int | None = None,
    ) -> dict[str, pd.DataFrame]:
        timeframe = timeframe or self.settings.default_timeframe
        limit = limit or self.settings.bar_limit
        return await self._request_bars_batch(symbols, timeframe, limit)

    async def fetch_bars_for_symbols(
        self,
        symbols: Sequence[str],
        timeframe: str | None = None,
        limit: int | None = None,
    ) -> dict[str, pd.DataFrame]:
        timeframe = timeframe or self.settings.default_timeframe
        limit = limit or self.settings.bar_limit
        normalized_symbols = unique_symbols(symbols, quote_currency=self.settings.universe_quote_currency)
        if not normalized_symbols:
            return {}

        frames: dict[str, pd.DataFrame] = {}
        chunk_size = max(1, self.settings.bar_batch_size)
        chunks = [
            normalized_symbols[index : index + chunk_size]
            for index in range(0, len(normalized_symbols), chunk_size)
        ]
        for chunk_index, chunk in enumerate(chunks, start=1):
            for attempt in range(self.settings.bar_batch_max_retries + 1):
                try:
                    logger.info(
                        "requesting crypto bar chunk chunk=%d/%d batch_size=%d requested_symbols=%d attempt=%d timeframe=%s limit=%d",
                        chunk_index,
                        len(chunks),
                        len(chunk),
                        len(normalized_symbols),
                        attempt + 1,
                        timeframe,
                        limit,
                    )
                    chunk_frames = await self._request_bars_batch(chunk, timeframe, limit)
                    frames.update(chunk_frames)
                    break
                except Exception as exc:
                    logger.warning(
                        "crypto bar chunk failed chunk=%d/%d batch_size=%d attempt=%d error=%s",
                        chunk_index,
                        len(chunks),
                        len(chunk),
                        attempt + 1,
                        exc,
                    )
                    if attempt >= self.settings.bar_batch_max_retries:
                        logger.error(
                            "crypto bar chunk exhausted retries chunk=%d/%d symbols=%s",
                            chunk_index,
                            len(chunks),
                            chunk,
                        )
                        break
                    await asyncio.sleep(0.5 * (attempt + 1))

        return frames

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        normalized_symbol = normalize_symbol(
            symbol,
            quote_currency=self.settings.universe_quote_currency,
        )
        if normalized_symbol is None:
            raise ValueError(f"invalid symbol: {symbol}")

        frames = await self.fetch_bars_batch([normalized_symbol], timeframe=timeframe, limit=limit)
        bars = frames.get(normalized_symbol)
        if bars is None or bars.empty:
            raise ValueError(f"crypto bars response missing bars for symbol {normalized_symbol}")
        if len(bars) < 51:
            logger.warning(
                "insufficient bars for %s: received %d bars (need >=51 for SMA50)",
                normalized_symbol,
                len(bars),
            )
        return bars

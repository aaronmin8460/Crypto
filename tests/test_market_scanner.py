import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pandas as pd

from app.config.settings import AppSettings
from app.services.crypto_universe import UniverseSnapshot
from app.services.market_scanner import MarketScanner


def make_bars(
    *,
    start_price: float,
    price_step: float,
    volume: float,
    volatility_pct: float,
    periods: int = 80,
) -> pd.DataFrame:
    rows = []
    for index in range(periods):
        close = start_price + price_step * index
        high = close * (1 + volatility_pct)
        low = close * (1 - volatility_pct)
        rows.append(
            {
                "Date": datetime(2025, 1, 1, tzinfo=timezone.utc) + pd.Timedelta(hours=index),
                "Open": close - price_step / 2,
                "High": high,
                "Low": low,
                "Close": close,
                "Volume": volume,
            }
        )
    return pd.DataFrame(rows)


def make_snapshot(symbols: list[str]) -> UniverseSnapshot:
    return UniverseSnapshot(
        symbols=symbols,
        fetched_at=datetime.now(timezone.utc),
        raw_asset_count=len(symbols),
        skipped_asset_count=0,
    )


def test_market_scanner_prefilter_ranks_and_selects_top_candidates():
    settings = AppSettings(
        enable_dynamic_universe=True,
        top_candidates_per_scan=2,
        min_average_volume=1000,
        min_volatility_pct=0.01,
        min_price=10,
    )
    data_service = AsyncMock()
    data_service.fetch_bars_for_symbols.return_value = {
        "BTC/USD": make_bars(start_price=100, price_step=0.8, volume=4000, volatility_pct=0.02),
        "ETH/USD": make_bars(start_price=50, price_step=1.2, volume=5000, volatility_pct=0.03),
        "SOL/USD": make_bars(start_price=25, price_step=2.0, volume=8000, volatility_pct=0.04),
        "XRP/USD": make_bars(start_price=1, price_step=0.01, volume=50, volatility_pct=0.002),
    }
    universe_service = AsyncMock()
    universe_service.get_universe.return_value = make_snapshot(["BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD"])
    scanner = MarketScanner(settings, data_service, universe_service)

    plan = asyncio.run(
        scanner.build_scan_plan(
            position_symbols=set(),
            open_order_symbols=set(),
            cooldown_symbols=set(),
        )
    )

    assert plan.summary["universe_symbol_count"] == 4
    assert plan.prefilter_results["XRP/USD"].passed is False
    assert "minimum_average_volume" in plan.prefilter_results["XRP/USD"].failed_filters
    assert [candidate.symbol for candidate in plan.top_candidates] == ["SOL/USD", "ETH/USD"]
    assert plan.evaluation_symbols == ["SOL/USD", "ETH/USD"]


def test_market_scanner_static_mode_uses_default_symbols():
    settings = AppSettings(
        enable_dynamic_universe=False,
        default_symbols=["BTC/USD", "ETHUSD", "BTCUSD"],
    )
    scanner = MarketScanner(settings, AsyncMock(), AsyncMock())

    plan = asyncio.run(
        scanner.build_scan_plan(
            position_symbols=set(),
            open_order_symbols=set(),
            cooldown_symbols=set(),
        )
    )

    assert plan.mode == "static"
    assert plan.evaluation_symbols == ["BTC/USD", "ETH/USD"]
    assert [candidate.symbol for candidate in plan.top_candidates] == ["BTC/USD", "ETH/USD"]


def test_market_scanner_keeps_management_symbols_without_duplicates():
    settings = AppSettings(
        enable_dynamic_universe=True,
        top_candidates_per_scan=1,
        exclude_existing_positions_from_prefilter=True,
    )
    data_service = AsyncMock()
    data_service.fetch_bars_for_symbols.return_value = {
        "BTC/USD": make_bars(start_price=100, price_step=1.5, volume=6000, volatility_pct=0.03),
        "ETH/USD": make_bars(start_price=50, price_step=1.0, volume=7000, volatility_pct=0.03),
    }
    universe_service = AsyncMock()
    universe_service.get_universe.return_value = make_snapshot(["BTC/USD", "ETH/USD"])
    scanner = MarketScanner(settings, data_service, universe_service)

    plan = asyncio.run(
        scanner.build_scan_plan(
            position_symbols={"BTC/USD"},
            open_order_symbols=set(),
            cooldown_symbols=set(),
        )
    )

    assert plan.prefilter_results["BTC/USD"].passed is False
    assert "not_existing_position" in plan.prefilter_results["BTC/USD"].failed_filters
    assert plan.evaluation_symbols.count("BTC/USD") == 1
    assert set(plan.evaluation_symbols) == {"BTC/USD", "ETH/USD"}

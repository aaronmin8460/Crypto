import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pandas as pd

from app.config.settings import AppSettings
from app.services.bot import TradingBot
from app.services.market_scanner import PrefilterResult, RankedCandidate, ScanPlan
from app.services.strategy import SignalResult


def make_bars(price: float = 100.0) -> pd.DataFrame:
    rows = []
    for index in range(80):
        close = price + index
        rows.append(
            {
                "Date": datetime(2025, 1, 1, tzinfo=timezone.utc) + pd.Timedelta(hours=index),
                "Open": close - 0.5,
                "High": close + 1.0,
                "Low": close - 1.0,
                "Close": close,
                "Volume": 5000.0,
            }
        )
    return pd.DataFrame(rows)


def make_order(symbol: str) -> dict[str, str]:
    return {
        "id": str(uuid4()),
        "symbol": symbol,
        "side": "buy",
        "status": "accepted",
        "submitted_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "filled_avg_price": "100.0",
        "filled_qty": "1.0",
        "notional": "100.0",
    }


def make_scan_plan(symbols: list[str]) -> ScanPlan:
    prefilter_results = {
        symbol: PrefilterResult(symbol=symbol, passed=True, metrics={}, filters={}, failed_filters=[])
        for symbol in symbols
    }
    ranked_candidates = [
        RankedCandidate(
            symbol=symbol,
            score=float(len(symbols) - index),
            ranking_reasons=[f"rank {index + 1}"],
            metrics={},
            prefilter=prefilter_results[symbol],
        )
        for index, symbol in enumerate(symbols)
    ]
    return ScanPlan(
        mode="dynamic",
        universe_symbols=symbols,
        market_data_symbols=symbols,
        prefilter_results=prefilter_results,
        ranked_candidates=ranked_candidates,
        top_candidates=ranked_candidates,
        evaluation_symbols=symbols,
        bars_by_symbol={symbol: make_bars(100 + index * 10) for index, symbol in enumerate(symbols)},
        scan_duration_ms=12,
        summary={
            "mode": "dynamic",
            "universe_symbol_count": len(symbols),
            "eligible_symbol_count": len(symbols),
            "filtered_symbol_count": len(symbols),
            "symbols_skipped_by_prefilter": 0,
            "top_candidates": [candidate.to_summary() for candidate in ranked_candidates],
            "prefilter_results": {symbol: prefilter.to_dict() for symbol, prefilter in prefilter_results.items()},
            "scan_duration_ms": 12,
        },
    )


def test_dynamic_run_respects_rank_order_and_max_open_positions(tmp_path):
    settings = AppSettings(
        enable_dynamic_universe=True,
        trading_enabled=True,
        max_open_positions=1,
        persistence_db_path=str(tmp_path / "bot_state.db"),
    )
    bot = TradingBot(settings, AsyncMock(), AsyncMock())
    bot.market_scanner.build_scan_plan = AsyncMock(return_value=make_scan_plan(["SOL/USD", "ETH/USD"]))
    bot.data_service.fetch_bars_for_symbols = AsyncMock(return_value={})

    confirmed_order = make_order("SOL/USD")
    bot.trading_service.get_account.return_value = {"cash": "1000", "equity": "1000", "status": "ACTIVE"}
    bot.trading_service.list_positions.return_value = []
    bot.trading_service.list_orders.return_value = []
    bot.trading_service.submit_market_buy_notional.return_value = confirmed_order
    bot._confirm_submitted_order = AsyncMock(
        return_value=(
            confirmed_order,
            {
                "positions": [
                    {
                        "symbol": "SOL/USD",
                        "market_value": "100",
                        "current_price": "100",
                        "avg_entry_price": "100",
                        "qty": "1",
                    }
                ]
            },
        )
    )

    with patch(
        "app.services.bot.evaluate_signal",
        side_effect=[
            SignalResult(signal="BUY", reason="best ranked"),
            SignalResult(signal="BUY", reason="second ranked"),
        ],
    ):
        result = asyncio.run(bot.run_once())

    assert result["results"][0]["symbol"] == "SOL/USD"
    assert result["results"][0]["broker_order_accepted"] is True
    assert result["results"][1]["symbol"] == "ETH/USD"
    assert result["results"][1]["reason"] == "max open positions reached"
    bot.trading_service.submit_market_buy_notional.assert_awaited_once()
    status = bot.status()
    assert status["dynamic_universe_enabled"] is True
    assert status["top_candidates"][0]["symbol"] == "SOL/USD"


def test_dynamic_mode_reconciliation_keeps_broker_truth_after_buy(tmp_path):
    settings = AppSettings(
        enable_dynamic_universe=True,
        trading_enabled=True,
        persistence_db_path=str(tmp_path / "bot_state.db"),
    )
    bot = TradingBot(settings, AsyncMock(), AsyncMock())
    bot.market_scanner.build_scan_plan = AsyncMock(return_value=make_scan_plan(["BTC/USD"]))
    bot.data_service.fetch_bars_for_symbols = AsyncMock(return_value={})

    confirmed_order = make_order("BTC/USD")
    bot.trading_service.get_account.return_value = {"cash": "1000", "equity": "1000", "status": "ACTIVE"}
    bot.trading_service.list_positions.side_effect = [[], []]
    bot.trading_service.list_orders.side_effect = [[], [confirmed_order]]
    bot.trading_service.submit_market_buy_notional.return_value = confirmed_order

    with patch("app.services.bot.evaluate_signal", return_value=SignalResult(signal="BUY", reason="scanner buy")):
        result = asyncio.run(bot.run_once())

    assert result["results"][0]["broker_order_accepted"] is True
    assert bot.state.last_order_by_symbol["BTC/USD"]["source"] == "broker"
    assert bot.state.local_order_attempts_by_symbol == {}
    assert bot.state.last_results["scan_summary"]["mode"] == "dynamic"

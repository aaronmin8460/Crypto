from __future__ import annotations

import logging
from dataclasses import dataclass, field
from time import perf_counter

import pandas as pd

from app.config.settings import AppSettings
from app.services.alpaca_crypto_data import AlpacaCryptoData
from app.services.crypto_universe import CryptoUniverseService
from app.services.strategy import IndicatorSnapshot, build_indicator_snapshot
from app.utils.symbols import unique_symbols

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PrefilterResult:
    symbol: str
    passed: bool
    metrics: dict[str, float] = field(default_factory=dict)
    filters: dict[str, bool] = field(default_factory=dict)
    failed_filters: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "metrics": self.metrics,
            "filters": self.filters,
            "failed_filters": self.failed_filters,
        }


@dataclass(slots=True)
class RankedCandidate:
    symbol: str
    score: float
    ranking_reasons: list[str]
    metrics: dict[str, float]
    prefilter: PrefilterResult

    def to_summary(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "rank_score": round(self.score, 6),
            "ranking_reasons": self.ranking_reasons,
        }


@dataclass(slots=True)
class ScanPlan:
    mode: str
    universe_symbols: list[str]
    market_data_symbols: list[str]
    prefilter_results: dict[str, PrefilterResult]
    ranked_candidates: list[RankedCandidate]
    top_candidates: list[RankedCandidate]
    evaluation_symbols: list[str]
    bars_by_symbol: dict[str, pd.DataFrame]
    scan_duration_ms: int
    summary: dict[str, object]
    used_default_fallback: bool = False


class MarketScanner:
    def __init__(
        self,
        settings: AppSettings,
        data_service: AlpacaCryptoData,
        universe_service: CryptoUniverseService,
    ) -> None:
        self.settings = settings
        self.data_service = data_service
        self.universe_service = universe_service

    async def build_scan_plan(
        self,
        *,
        position_symbols: set[str],
        open_order_symbols: set[str],
        cooldown_symbols: set[str],
    ) -> ScanPlan:
        started_at = perf_counter()
        if not self.settings.enable_dynamic_universe:
            static_symbols = unique_symbols(
                self.settings.default_symbols,
                quote_currency=self.settings.universe_quote_currency,
            )
            scan_duration_ms = int((perf_counter() - started_at) * 1000)
            top_candidates = [
                RankedCandidate(
                    symbol=symbol,
                    score=0.0,
                    ranking_reasons=["static symbol mode"],
                    metrics={},
                    prefilter=PrefilterResult(symbol=symbol, passed=True),
                )
                for symbol in static_symbols
            ]
            return ScanPlan(
                mode="static",
                universe_symbols=static_symbols,
                market_data_symbols=static_symbols,
                prefilter_results={symbol: PrefilterResult(symbol=symbol, passed=True) for symbol in static_symbols},
                ranked_candidates=top_candidates,
                top_candidates=top_candidates,
                evaluation_symbols=static_symbols,
                bars_by_symbol={},
                scan_duration_ms=scan_duration_ms,
                summary={
                    "mode": "static",
                    "universe_symbol_count": len(static_symbols),
                    "eligible_symbol_count": len(static_symbols),
                    "filtered_symbol_count": len(static_symbols),
                    "symbols_skipped_by_prefilter": 0,
                    "top_candidates": [candidate.to_summary() for candidate in top_candidates],
                    "prefilter_results": {
                        symbol: {"passed": True, "filters": {}, "failed_filters": [], "metrics": {}}
                        for symbol in static_symbols
                    },
                    "scan_duration_ms": scan_duration_ms,
                },
            )

        universe_snapshot = await self.universe_service.get_universe()
        universe_symbols = list(universe_snapshot.symbols)
        used_default_fallback = False
        if not universe_symbols:
            universe_symbols = unique_symbols(
                self.settings.default_symbols,
                quote_currency=self.settings.universe_quote_currency,
            )
            used_default_fallback = True

        if self.settings.max_symbols_per_scan > 0:
            universe_symbols = universe_symbols[: self.settings.max_symbols_per_scan]

        bars_by_symbol = await self.data_service.fetch_bars_for_symbols(
            universe_symbols,
            timeframe=self.settings.default_timeframe,
            limit=self.settings.bar_limit,
        )

        prefilter_results: dict[str, PrefilterResult] = {}
        indicator_snapshots: dict[str, IndicatorSnapshot] = {}
        passed_symbols: list[str] = []
        for symbol in universe_symbols:
            bars = bars_by_symbol.get(symbol)
            prefilter = self._prefilter_symbol(
                symbol=symbol,
                bars=bars,
                position_symbols=position_symbols,
                open_order_symbols=open_order_symbols,
                cooldown_symbols=cooldown_symbols,
            )
            prefilter_results[symbol] = prefilter
            if prefilter.passed:
                passed_symbols.append(symbol)
                indicator_snapshots[symbol] = build_indicator_snapshot(bars, self.settings)

        ranked_candidates = self._rank_candidates(passed_symbols, indicator_snapshots, prefilter_results)
        if self.settings.top_candidates_per_scan > 0:
            top_candidates = ranked_candidates[: self.settings.top_candidates_per_scan]
        else:
            top_candidates = ranked_candidates

        management_symbols = unique_symbols(
            sorted(position_symbols) + sorted(open_order_symbols),
            quote_currency=self.settings.universe_quote_currency,
        )
        evaluation_symbols = unique_symbols(
            management_symbols + [candidate.symbol for candidate in top_candidates],
            quote_currency=self.settings.universe_quote_currency,
        )

        missing_symbols = [symbol for symbol in evaluation_symbols if symbol not in bars_by_symbol]
        if missing_symbols:
            bars_by_symbol.update(
                await self.data_service.fetch_bars_for_symbols(
                    missing_symbols,
                    timeframe=self.settings.default_timeframe,
                    limit=self.settings.bar_limit,
                )
            )

        scan_duration_ms = int((perf_counter() - started_at) * 1000)
        summary = {
            "mode": "dynamic",
            "used_default_fallback": used_default_fallback,
            "universe_symbol_count": len(universe_symbols),
            "eligible_symbol_count": len(prefilter_results),
            "filtered_symbol_count": len(passed_symbols),
            "symbols_skipped_by_prefilter": len(prefilter_results) - len(passed_symbols),
            "top_candidates": [candidate.to_summary() for candidate in top_candidates],
            "ranked_symbols": [candidate.to_summary() for candidate in ranked_candidates],
            "prefilter_results": {
                symbol: result.to_dict() for symbol, result in prefilter_results.items()
            },
            "evaluation_symbols": evaluation_symbols,
            "scan_duration_ms": scan_duration_ms,
        }

        logger.info(
            "scanner completed universe=%d eligible=%d passed=%d shortlisted=%d evaluation_symbols=%d duration_ms=%d",
            len(universe_symbols),
            len(prefilter_results),
            len(passed_symbols),
            len(top_candidates),
            len(evaluation_symbols),
            scan_duration_ms,
        )
        return ScanPlan(
            mode="dynamic",
            universe_symbols=universe_symbols,
            market_data_symbols=list(prefilter_results),
            prefilter_results=prefilter_results,
            ranked_candidates=ranked_candidates,
            top_candidates=top_candidates,
            evaluation_symbols=evaluation_symbols,
            bars_by_symbol=bars_by_symbol,
            scan_duration_ms=scan_duration_ms,
            summary=summary,
            used_default_fallback=used_default_fallback,
        )

    def _prefilter_symbol(
        self,
        *,
        symbol: str,
        bars: pd.DataFrame | None,
        position_symbols: set[str],
        open_order_symbols: set[str],
        cooldown_symbols: set[str],
    ) -> PrefilterResult:
        metrics: dict[str, float] = {}
        filters: dict[str, bool] = {}
        if bars is None or bars.empty:
            filters["bars_available"] = False
            return PrefilterResult(
                symbol=symbol,
                passed=False,
                metrics=metrics,
                filters=filters,
                failed_filters=["bars_available"],
            )

        try:
            snapshot = build_indicator_snapshot(bars, self.settings)
        except Exception:
            filters["sufficient_history"] = False
            return PrefilterResult(
                symbol=symbol,
                passed=False,
                metrics=metrics,
                filters=filters,
                failed_filters=["sufficient_history"],
            )

        metrics = {
            "price": snapshot.last_close,
            "average_volume": snapshot.average_volume,
            "volume": snapshot.last_volume,
            "volatility_pct": snapshot.volatility_pct,
            "rsi": snapshot.rsi,
            "momentum_pct": snapshot.momentum_pct,
            "trend_strength_pct": snapshot.trend_strength_pct,
            "distance_from_fast_sma_pct": snapshot.distance_from_fast_sma_pct,
            "distance_from_slow_sma_pct": snapshot.distance_from_slow_sma_pct,
        }
        filters = {
            "minimum_price": snapshot.last_close >= self.settings.min_price,
            "minimum_average_volume": snapshot.average_volume >= self.settings.min_average_volume,
            "minimum_volatility": snapshot.volatility_pct >= self.settings.min_volatility_pct,
            "not_excluded_symbol": symbol not in set(self.settings.universe_excluded_symbols),
            "not_in_cooldown": (
                symbol not in cooldown_symbols
                if self.settings.exclude_cooldown_symbols_from_prefilter
                else True
            ),
            "not_existing_position": (
                symbol not in position_symbols
                if self.settings.exclude_existing_positions_from_prefilter
                else True
            ),
            "not_open_order": (
                symbol not in open_order_symbols
                if self.settings.exclude_open_order_symbols_from_prefilter
                else True
            ),
        }
        failed_filters = [name for name, passed in filters.items() if not passed]
        return PrefilterResult(
            symbol=symbol,
            passed=not failed_filters,
            metrics=metrics,
            filters=filters,
            failed_filters=failed_filters,
        )

    def _rank_candidates(
        self,
        symbols: list[str],
        snapshots: dict[str, IndicatorSnapshot],
        prefilter_results: dict[str, PrefilterResult],
    ) -> list[RankedCandidate]:
        if not symbols:
            return []

        raw_scores = {
            "trend": {
                symbol: max(0.0, snapshots[symbol].trend_strength_pct)
                + max(0.0, snapshots[symbol].distance_from_slow_sma_pct)
                for symbol in symbols
            },
            "volume": {
                symbol: max(0.0, snapshots[symbol].average_volume)
                for symbol in symbols
            },
            "volatility": {
                symbol: max(0.0, snapshots[symbol].volatility_pct)
                for symbol in symbols
            },
            "momentum": {
                symbol: max(0.0, snapshots[symbol].momentum_pct)
                + self._rsi_quality(snapshots[symbol].rsi)
                + max(0.0, snapshots[symbol].distance_from_fast_sma_pct)
                for symbol in symbols
            },
        }

        normalized_components = {
            component: self._normalize_component_scores(component_scores)
            for component, component_scores in raw_scores.items()
        }
        weighted_total = {
            symbol: (
                normalized_components["trend"][symbol] * self.settings.rank_by_trend_weight
                + normalized_components["volume"][symbol] * self.settings.rank_by_volume_weight
                + normalized_components["volatility"][symbol] * self.settings.rank_by_volatility_weight
                + normalized_components["momentum"][symbol] * self.settings.rank_by_momentum_weight
            )
            for symbol in symbols
        }

        ranked: list[RankedCandidate] = []
        for symbol in symbols:
            snapshot = snapshots[symbol]
            contributions = {
                "trend": normalized_components["trend"][symbol] * self.settings.rank_by_trend_weight,
                "volume": normalized_components["volume"][symbol] * self.settings.rank_by_volume_weight,
                "volatility": normalized_components["volatility"][symbol] * self.settings.rank_by_volatility_weight,
                "momentum": normalized_components["momentum"][symbol] * self.settings.rank_by_momentum_weight,
            }
            ranking_reasons = self._build_ranking_reasons(snapshot, contributions)
            metrics = {
                "price": snapshot.last_close,
                "average_volume": snapshot.average_volume,
                "volatility_pct": snapshot.volatility_pct,
                "rsi": snapshot.rsi,
                "momentum_pct": snapshot.momentum_pct,
                "trend_strength_pct": snapshot.trend_strength_pct,
                "distance_from_fast_sma_pct": snapshot.distance_from_fast_sma_pct,
                "distance_from_slow_sma_pct": snapshot.distance_from_slow_sma_pct,
            }
            ranked.append(
                RankedCandidate(
                    symbol=symbol,
                    score=weighted_total[symbol],
                    ranking_reasons=ranking_reasons,
                    metrics=metrics,
                    prefilter=prefilter_results[symbol],
                )
            )

        ranked.sort(key=lambda candidate: (candidate.score, candidate.symbol), reverse=True)
        return ranked

    def _build_ranking_reasons(
        self,
        snapshot: IndicatorSnapshot,
        contributions: dict[str, float],
    ) -> list[str]:
        ordered_components = sorted(contributions.items(), key=lambda item: item[1], reverse=True)
        reasons: list[str] = []
        for component, _ in ordered_components[:3]:
            if component == "trend":
                reasons.append(f"trend strength {snapshot.trend_strength_pct:.2%}")
            elif component == "volume":
                reasons.append(f"average volume {snapshot.average_volume:.2f}")
            elif component == "volatility":
                reasons.append(f"volatility {snapshot.volatility_pct:.2%}")
            elif component == "momentum":
                reasons.append(f"momentum {snapshot.momentum_pct:.2%} with RSI {snapshot.rsi:.1f}")
        return reasons

    def _normalize_component_scores(self, raw_scores: dict[str, float]) -> dict[str, float]:
        minimum = min(raw_scores.values())
        maximum = max(raw_scores.values())
        if maximum == minimum:
            return {symbol: 1.0 for symbol in raw_scores}
        return {
            symbol: (score - minimum) / (maximum - minimum)
            for symbol, score in raw_scores.items()
        }

    def _rsi_quality(self, rsi: float) -> float:
        return max(0.0, 1.0 - abs(rsi - 55.0) / 55.0)

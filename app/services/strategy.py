from __future__ import annotations

from typing import Any

import pandas as pd
from pydantic import BaseModel, Field


class StrategyResult(BaseModel):
    signal: str
    reason: str
    filters: dict[str, bool] = Field(default_factory=dict)
    indicators: dict[str, float] = Field(default_factory=dict)
    blocked_by: list[str] = Field(default_factory=list)


class IndicatorSnapshot(BaseModel):
    last_close: float
    last_high: float
    last_low: float
    last_volume: float
    average_volume: float
    volatility_pct: float
    rsi: float
    atr: float
    fast_sma: float
    slow_sma: float
    momentum_pct: float
    trend_strength_pct: float
    distance_from_fast_sma_pct: float
    distance_from_slow_sma_pct: float


def _compute_rsi(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(length).mean()
    avg_loss = loss.rolling(length).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _compute_atr(df: pd.DataFrame, length: int) -> pd.Series:
    close = df["Close"].astype(float)
    high = df.get("High", close).astype(float)
    low = df.get("Low", close).astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(length).mean()


def _signal_from_crossovers(df: pd.DataFrame, fast_len: int, slow_len: int) -> tuple[bool, bool]:
    close = df["Close"].astype(float)
    fast = close.rolling(fast_len).mean()
    slow = close.rolling(slow_len).mean()
    prev_fast = fast.shift(1)
    prev_slow = slow.shift(1)
    last_fast = fast.iloc[-1]
    last_slow = slow.iloc[-1]
    prior_fast = prev_fast.iloc[-1]
    prior_slow = prev_slow.iloc[-1]
    if pd.isna(prior_fast) or pd.isna(prior_slow) or pd.isna(last_fast) or pd.isna(last_slow):
        raise ValueError("insufficient SMA data for strategy")
    crossed_up = prior_fast <= prior_slow and last_fast > last_slow
    crossed_down = prior_fast >= prior_slow and last_fast < last_slow
    return crossed_up, crossed_down


def _higher_timeframe_is_ok(higher_bars: pd.DataFrame, fast_len: int, slow_len: int) -> bool:
    if higher_bars is None or len(higher_bars) < slow_len + 1:
        return False
    higher_close = higher_bars["Close"].astype(float)
    fast = higher_close.rolling(fast_len).mean().iloc[-1]
    slow = higher_close.rolling(slow_len).mean().iloc[-1]
    return not pd.isna(fast) and not pd.isna(slow) and fast > slow


def _default_settings() -> Any:
    class DefaultSettings:
        strategy_fast_sma = 20
        strategy_slow_sma = 50
        rsi_length = 14
        atr_length = 14
        min_volume = 0.0
        min_volatility_pct = 0.0
        rsi_overbought = 70.0
        higher_timeframe_confirmation = False

    return DefaultSettings()


def minimum_bars_required(settings: Any | None = None) -> int:
    if settings is None:
        settings = _default_settings()

    return max(settings.strategy_slow_sma, settings.rsi_length, settings.atr_length, 20) + 2


def build_indicator_snapshot(df: pd.DataFrame, settings: Any | None = None) -> IndicatorSnapshot:
    if settings is None:
        settings = _default_settings()

    df = df.sort_values("Date", ignore_index=True)
    minimum_bars = minimum_bars_required(settings)
    if len(df) < minimum_bars:
        raise ValueError(f"not enough bars to compute strategy indicators (need {minimum_bars})")

    close = df["Close"].astype(float)
    high = df.get("High", close).astype(float)
    low = df.get("Low", close).astype(float)
    volume = df.get("Volume", pd.Series([1.0] * len(df))).astype(float)

    crossed_up, crossed_down = _signal_from_crossovers(df, settings.strategy_fast_sma, settings.strategy_slow_sma)
    last_close = float(close.iloc[-1])
    last_high = float(high.iloc[-1])
    last_low = float(low.iloc[-1])
    average_volume = float(volume.rolling(20).mean().iloc[-1])
    last_volume = float(volume.iloc[-1])
    volatility_pct = float((last_high - last_low) / last_close if last_close else 0.0)
    rsi = float(_compute_rsi(close, settings.rsi_length).iloc[-1])
    atr = float(_compute_atr(df, settings.atr_length).iloc[-1])
    fast_sma = float(close.rolling(settings.strategy_fast_sma).mean().iloc[-1])
    slow_sma = float(close.rolling(settings.strategy_slow_sma).mean().iloc[-1])
    trend_strength_pct = float((fast_sma - slow_sma) / slow_sma if slow_sma else 0.0)
    momentum_period = min(10, max(2, len(close) - 1))
    reference_close = float(close.iloc[-momentum_period])
    momentum_pct = float((last_close - reference_close) / reference_close if reference_close else 0.0)
    distance_from_fast_sma_pct = float((last_close - fast_sma) / fast_sma if fast_sma else 0.0)
    distance_from_slow_sma_pct = float((last_close - slow_sma) / slow_sma if slow_sma else 0.0)

    return IndicatorSnapshot(
        last_close=last_close,
        last_high=last_high,
        last_low=last_low,
        last_volume=last_volume,
        average_volume=average_volume,
        volatility_pct=volatility_pct,
        rsi=rsi,
        atr=atr,
        fast_sma=fast_sma,
        slow_sma=slow_sma,
        momentum_pct=momentum_pct,
        trend_strength_pct=trend_strength_pct,
        distance_from_fast_sma_pct=distance_from_fast_sma_pct,
        distance_from_slow_sma_pct=distance_from_slow_sma_pct,
    )


def evaluate_signal(df: pd.DataFrame, settings: Any | None = None, higher_bars: pd.DataFrame | None = None) -> StrategyResult:
    if settings is None:
        settings = _default_settings()

    df = df.sort_values("Date", ignore_index=True)
    minimum_bars = minimum_bars_required(settings)
    if len(df) < minimum_bars:
        raise ValueError(f"not enough bars to compute strategy indicators (need {minimum_bars})")

    close = df["Close"].astype(float)
    snapshot = build_indicator_snapshot(df, settings)
    crossed_up, crossed_down = _signal_from_crossovers(df, settings.strategy_fast_sma, settings.strategy_slow_sma)
    trend_ok = snapshot.fast_sma > snapshot.slow_sma
    higher_trend_ok = True
    if settings.higher_timeframe_confirmation:
        higher_trend_ok = _higher_timeframe_is_ok(higher_bars, settings.strategy_fast_sma, settings.strategy_slow_sma)

    extended_up = False
    if len(close) >= 4:
        extended_up = close.iloc[-4:-1].diff().dropna().gt(0).all()
    overbought = snapshot.rsi > settings.rsi_overbought and extended_up

    filters = {
        "trend": trend_ok,
        "volume": snapshot.last_volume >= max(settings.min_volume, snapshot.average_volume * 0.5),
        "volatility": snapshot.volatility_pct >= settings.min_volatility_pct,
        "rsi_not_overbought": not overbought,
        "higher_timeframe": higher_trend_ok,
    }
    indicators = {
        "fast_sma": snapshot.fast_sma,
        "slow_sma": snapshot.slow_sma,
        "rsi": snapshot.rsi,
        "atr": snapshot.atr,
        "volatility_pct": snapshot.volatility_pct,
        "volume": snapshot.last_volume,
        "average_volume": snapshot.average_volume,
        "momentum_pct": snapshot.momentum_pct,
        "trend_strength_pct": snapshot.trend_strength_pct,
        "distance_from_fast_sma_pct": snapshot.distance_from_fast_sma_pct,
        "distance_from_slow_sma_pct": snapshot.distance_from_slow_sma_pct,
        "last_close": snapshot.last_close,
    }

    blocked_by: list[str] = []
    if crossed_up:
        if not trend_ok:
            blocked_by.append("trend")
        if not filters["volume"]:
            blocked_by.append("volume")
        if not filters["volatility"]:
            blocked_by.append("volatility")
        if not filters["rsi_not_overbought"]:
            blocked_by.append("overbought")
        if settings.higher_timeframe_confirmation and not higher_trend_ok:
            blocked_by.append("higher_timeframe")

        if blocked_by:
            reason_details = ", ".join(blocked_by)
            return StrategyResult(
                signal="HOLD",
                reason=f"buy signal blocked by: {reason_details}",
                filters=filters,
                indicators=indicators,
                blocked_by=blocked_by,
            )

        return StrategyResult(
            signal="BUY",
            reason="sma crossover confirmed with trend, volume, and volatility filters",
            filters=filters,
            indicators=indicators,
            blocked_by=[],
        )

    if crossed_down or snapshot.last_close < snapshot.slow_sma:
        return StrategyResult(
            signal="SELL",
            reason="price weakness detected by SMA crossover or close below slow SMA",
            filters=filters,
            indicators=indicators,
            blocked_by=[],
        )

    return StrategyResult(
        signal="HOLD",
        reason="no actionable signal",
        filters=filters,
        indicators=indicators,
        blocked_by=[],
    )


SignalResult = StrategyResult

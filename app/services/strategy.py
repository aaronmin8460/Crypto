from __future__ import annotations

import pandas as pd
from pydantic import BaseModel


class SignalResult(BaseModel):
    signal: str
    reason: str


def evaluate_signal(df: pd.DataFrame) -> SignalResult:
    df = df.sort_values("Date", ignore_index=True)
    if len(df) < 51:
        raise ValueError("not enough bars to compute SMA50")

    close = df["Close"].astype(float)
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()

    prev_index = len(df) - 2
    last_index = len(df) - 1

    prev_fast = sma20.iloc[prev_index]
    prev_slow = sma50.iloc[prev_index]
    last_fast = sma20.iloc[last_index]
    last_slow = sma50.iloc[last_index]
    last_close = close.iloc[last_index]

    if pd.isna(prev_fast) or pd.isna(prev_slow) or pd.isna(last_fast) or pd.isna(last_slow):
        raise ValueError("insufficient valid SMA values")

    crossed_up = prev_fast <= prev_slow and last_fast > last_slow
    crossed_down = prev_fast >= prev_slow and last_fast < last_slow
    close_below_slow = last_close < last_slow

    if crossed_up and last_close > last_slow:
        return SignalResult(
            signal="BUY",
            reason="fast SMA crossed above slow SMA and latest close is above slow SMA",
        )

    if crossed_down or close_below_slow:
        return SignalResult(
            signal="SELL",
            reason="fast SMA crossed below slow SMA or latest close dropped below slow SMA",
        )

    return SignalResult(signal="HOLD", reason="no valid crossover signal")

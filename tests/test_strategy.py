from datetime import datetime, timedelta

import pandas as pd

from app.services.strategy import evaluate_signal


def make_bars(values):
    start = datetime(2025, 1, 1)
    data = [
        {"Date": start + timedelta(hours=i), "Close": value}
        for i, value in enumerate(values)
    ]
    return pd.DataFrame(data)


def test_buy_signal():
    values = [100.0] * 59 + [200.0]
    df = make_bars(values)
    result = evaluate_signal(df)
    assert result.signal == "BUY"


def test_sell_signal():
    values = [200.0] * 59 + [100.0]
    df = make_bars(values)
    result = evaluate_signal(df)
    assert result.signal == "SELL"


def test_hold_signal():
    values = [100.0] * 60
    df = make_bars(values)
    result = evaluate_signal(df)
    assert result.signal == "HOLD"


def test_overbought_extends_hold():
    values = [100.0] * 40 + [110.0, 115.0, 120.0, 125.0, 130.0, 135.0, 140.0, 145.0, 150.0, 155.0, 160.0, 165.0, 170.0, 175.0, 180.0, 185.0, 190.0, 195.0, 200.0, 205.0]
    df = make_bars(values)
    result = evaluate_signal(df)
    assert result.signal == "HOLD"
    assert result.filters["rsi_not_overbought"] is False
    assert result.indicators["rsi"] > 70

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

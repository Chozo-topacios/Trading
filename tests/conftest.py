import numpy as np
import pandas as pd
import pytest


def make_ohlc(closes, opens=None):
    closes = np.asarray(closes, float)
    opens = np.asarray(opens, float) if opens is not None else np.r_[closes[0], closes[:-1]]
    idx = pd.bdate_range("2020-01-01", periods=len(closes))
    return pd.DataFrame({
        "Open": opens,
        "High": np.maximum(opens, closes) * 1.005,
        "Low": np.minimum(opens, closes) * 0.995,
        "Close": closes,
    }, index=idx)


@pytest.fixture
def random_walk():
    rng = np.random.default_rng(42)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, 1500)))
    opens = closes * (1 + rng.normal(0, 0.003, len(closes)))
    df = make_ohlc(closes, opens)
    body_hi = np.maximum(df["Open"], df["Close"])
    body_lo = np.minimum(df["Open"], df["Close"])
    df["High"] = body_hi * (1 + np.abs(rng.normal(0, 0.006, len(df))))
    df["Low"] = body_lo * (1 - np.abs(rng.normal(0, 0.006, len(df))))
    return df

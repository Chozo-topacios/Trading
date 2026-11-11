"""Indicadores técnicos (implementados con pandas, sin dependencias externas)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 2) -> pd.Series:
    """RSI de Wilder (el mismo que usan TradingView / MetaTrader)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    out = 100.0 - 100.0 / (1.0 + rs)
    # Sin pérdidas en la ventana => RSI 100; sin ganancias => 0
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    out[avg_gain.isna()] = np.nan
    return out


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range de Wilder."""
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def ibs(df: pd.DataFrame) -> pd.Series:
    """Internal Bar Strength: (Close - Low) / (High - Low)."""
    rng = (df["High"] - df["Low"]).replace(0, np.nan)
    return ((df["Close"] - df["Low"]) / rng).fillna(0.5)

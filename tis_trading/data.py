"""Carga de datos diarios OHLC (CSV, yfinance opcional o API de Schwab)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

REQUIRED = ["Open", "High", "Low", "Close"]


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    cols = {c: c.strip().capitalize() for c in df.columns}
    df = df.rename(columns=cols)
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
        df = df.set_index("Date")
    df.index = pd.to_datetime(df.index)
    df.index.name = "Date"
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas {missing}; se esperan Date, Open, High, Low, Close")
    keep = REQUIRED + (["Volume"] if "Volume" in df.columns else [])
    df = df[keep].astype(float).dropna(subset=REQUIRED).sort_index()
    return df[~df.index.duplicated(keep="last")]


def load_csv(path: str | Path) -> pd.DataFrame:
    return normalize(pd.read_csv(path))


def load_yfinance(symbol: str, start: str = "2000-01-01") -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("Instala yfinance: pip install yfinance") from e
    df = yf.download(symbol, start=start, auto_adjust=True, progress=False, multi_level_index=False)
    return normalize(df.reset_index())


def load(symbol: str, source: str = "csv", data_dir: str | Path = "data",
         client=None) -> pd.DataFrame:
    """Velas diarias de ``symbol`` según ``source`` (csv | yfinance | schwab | ibkr | mt5).

    Para schwab / ibkr / mt5, ``client`` es el objeto con ``price_history_daily``.
    """
    if source == "csv":
        path = Path(data_dir) / f"{symbol}_D1.csv"
        if not path.exists():
            raise FileNotFoundError(
                f"No existe {path}. Descarga datos con: python -m tis_trading download {symbol}"
            )
        return load_csv(path)
    if source == "yfinance":
        return load_yfinance(symbol)
    if source in ("schwab", "ibkr", "mt5", "broker"):
        if client is None:
            raise ValueError(f"source={source} requiere una conexión activa con el bróker")
        return normalize(client.price_history_daily(symbol).reset_index())
    raise ValueError(f"Fuente de datos desconocida: {source}")

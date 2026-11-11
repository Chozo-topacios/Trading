"""Estrategias de trading diarias, solo largos.

Convención común a todas las estrategias:
  * Las señales se evalúan AL CIERRE de la vela ``i``.
  * La orden se ejecuta en la APERTURA de la vela ``i + 1`` (orden a mercado).

Cada estrategia implementa:
  * ``prepare(df)``      -> añade columnas de indicadores.
  * ``entry(df, i)``     -> True si hay señal de compra al cierre de ``i``.
  * ``exit(df, i, entry_i)`` -> True si hay señal de venta al cierre de ``i``
    estando en posición desde la vela ``entry_i`` (vela de la apertura de entrada).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from tis_trading import indicators as ind


@dataclass
class Strategy:
    name: str = "base"
    description: str = ""
    params: dict = field(default_factory=dict)
    #: Número mínimo de velas necesarias antes de poder emitir señales.
    warmup: int = 200

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:  # pragma: no cover - interfaz
        raise NotImplementedError

    def entry(self, df: pd.DataFrame, i: int) -> bool:  # pragma: no cover - interfaz
        raise NotImplementedError

    def exit(self, df: pd.DataFrame, i: int, entry_i: int) -> bool:  # pragma: no cover
        raise NotImplementedError

    def time_stop(self, i: int, entry_i: int) -> bool:
        max_hold = self.params.get("max_hold_days")
        return bool(max_hold) and (i - entry_i + 1) >= int(max_hold)


# ---------------------------------------------------------------------------
# 1) TIS · RSI2 Mean Reversion (Mariel Lang · Trade It Simple, estrategia #1)
# ---------------------------------------------------------------------------
@dataclass
class TisRsi2(Strategy):
    """Reglas publicadas en el research reproducible de Trade It Simple.

    * Filtro de tendencia: Close > SMA(200).
    * Entrada: RSI(2) < 15 al cierre -> compra en la apertura siguiente.
    * Salida: dos velas verdes consecutivas (Close > Open) -> venta en la
      apertura siguiente. Sin stop de precio en las reglas originales.
    * Solo largos, temporalidad diaria. Activo de referencia: QQQ.
    """

    name: str = "tis_rsi2"
    description: str = "TIS RSI(2) <15 sobre SMA200, salida tras 2 velas verdes"

    def __post_init__(self):
        defaults = {"rsi_period": 2, "rsi_entry": 15.0, "sma_period": 200, "green_bars": 2}
        self.params = {**defaults, **self.params}
        self.warmup = int(self.params["sma_period"])

    def prepare(self, df):
        df = df.copy()
        df["sma_trend"] = ind.sma(df["Close"], int(self.params["sma_period"]))
        df["rsi"] = ind.rsi(df["Close"], int(self.params["rsi_period"]))
        df["green"] = df["Close"] > df["Open"]
        return df

    def entry(self, df, i):
        row = df.iloc[i]
        if pd.isna(row["sma_trend"]) or pd.isna(row["rsi"]):
            return False
        return bool(row["Close"] > row["sma_trend"] and row["rsi"] < self.params["rsi_entry"])

    def exit(self, df, i, entry_i):
        n = int(self.params["green_bars"])
        # Las velas verdes deben pertenecer al periodo en posición
        # (la vela de entrada cuenta: se abrió la posición en su apertura).
        if i - entry_i + 1 < n:
            return False
        return bool(df["green"].iloc[i - n + 1 : i + 1].all())


# ---------------------------------------------------------------------------
# Estrategias complementarias (clásicas de reversión a la media, L. Connors)
# ---------------------------------------------------------------------------
@dataclass
class ConnorsRsi2(Strategy):
    """RSI(2) original de Larry Connors: RSI2 < 5 sobre SMA200, salida Close > SMA(5)."""

    name: str = "connors_rsi2"
    description: str = "RSI(2) <5 sobre SMA200, salida al cerrar sobre SMA5"

    def __post_init__(self):
        defaults = {"rsi_period": 2, "rsi_entry": 5.0, "sma_period": 200, "exit_sma": 5}
        self.params = {**defaults, **self.params}
        self.warmup = int(self.params["sma_period"])

    def prepare(self, df):
        df = df.copy()
        df["sma_trend"] = ind.sma(df["Close"], int(self.params["sma_period"]))
        df["sma_exit"] = ind.sma(df["Close"], int(self.params["exit_sma"]))
        df["rsi"] = ind.rsi(df["Close"], int(self.params["rsi_period"]))
        return df

    def entry(self, df, i):
        row = df.iloc[i]
        if pd.isna(row["sma_trend"]) or pd.isna(row["rsi"]):
            return False
        return bool(row["Close"] > row["sma_trend"] and row["rsi"] < self.params["rsi_entry"])

    def exit(self, df, i, entry_i):
        row = df.iloc[i]
        return bool(row["Close"] > row["sma_exit"])


@dataclass
class Double7(Strategy):
    """Double 7's (Connors): cierre en mínimo de 7 días sobre SMA200; sale en máximo de 7 días."""

    name: str = "double7"
    description: str = "Cierre en mínimo de 7 días sobre SMA200, salida en máximo de 7 días"

    def __post_init__(self):
        defaults = {"lookback": 7, "sma_period": 200}
        self.params = {**defaults, **self.params}
        self.warmup = int(self.params["sma_period"])

    def prepare(self, df):
        df = df.copy()
        n = int(self.params["lookback"])
        df["sma_trend"] = ind.sma(df["Close"], int(self.params["sma_period"]))
        df["low_n"] = df["Close"].rolling(n, min_periods=n).min()
        df["high_n"] = df["Close"].rolling(n, min_periods=n).max()
        return df

    def entry(self, df, i):
        row = df.iloc[i]
        if pd.isna(row["sma_trend"]) or pd.isna(row["low_n"]):
            return False
        return bool(row["Close"] > row["sma_trend"] and row["Close"] <= row["low_n"])

    def exit(self, df, i, entry_i):
        row = df.iloc[i]
        return bool(row["Close"] >= row["high_n"])


@dataclass
class IbsReversion(Strategy):
    """Internal Bar Strength: compra IBS < 0.2 sobre SMA200, vende IBS > 0.8 o Close > máximo previo."""

    name: str = "ibs"
    description: str = "IBS <0.2 sobre SMA200, salida IBS >0.8 o cierre sobre máximo anterior"

    def __post_init__(self):
        defaults = {"ibs_entry": 0.2, "ibs_exit": 0.8, "sma_period": 200}
        self.params = {**defaults, **self.params}
        self.warmup = int(self.params["sma_period"])

    def prepare(self, df):
        df = df.copy()
        df["sma_trend"] = ind.sma(df["Close"], int(self.params["sma_period"]))
        df["ibs"] = ind.ibs(df)
        df["prev_high"] = df["High"].shift(1)
        return df

    def entry(self, df, i):
        row = df.iloc[i]
        if pd.isna(row["sma_trend"]):
            return False
        return bool(row["Close"] > row["sma_trend"] and row["ibs"] < self.params["ibs_entry"])

    def exit(self, df, i, entry_i):
        row = df.iloc[i]
        return bool(row["ibs"] > self.params["ibs_exit"] or row["Close"] > row["prev_high"])


STRATEGIES: dict[str, type[Strategy]] = {
    "tis_rsi2": TisRsi2,
    "connors_rsi2": ConnorsRsi2,
    "double7": Double7,
    "ibs": IbsReversion,
}


def get_strategy(name: str, params: dict | None = None) -> Strategy:
    try:
        cls = STRATEGIES[name]
    except KeyError:
        raise ValueError(f"Estrategia desconocida '{name}'. Disponibles: {', '.join(STRATEGIES)}")
    return cls(params=dict(params or {}))


def should_exit(strategy: Strategy, df: pd.DataFrame, i: int, entry_i: int) -> tuple[bool, str]:
    """Combina la salida de la estrategia con el stop temporal opcional."""
    if strategy.exit(df, i, entry_i):
        return True, "signal"
    if strategy.time_stop(i, entry_i):
        return True, "time_stop"
    return False, ""

"""Dimensionamiento de posición.

Modo por defecto ("risk"), igual que el robot TIS: arriesgar ``risk_pct`` del
capital contra un stop *virtual* a ``atr_mult`` x ATR(14) del precio de entrada.
El stop es solo para calcular el tamaño: las reglas de la estrategia no usan
stop de precio salvo que se active ``use_stop``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class RiskConfig:
    mode: str = "risk"            # "risk" | "fraction"
    risk_pct: float = 0.01        # 1 % del capital por operación
    atr_period: int = 14
    atr_mult: float = 2.0         # stop virtual = 2 x ATR(14)
    fraction: float = 1.0         # modo "fraction": % del capital invertido
    max_position_pct: float = 1.0 # tope de exposición por posición (1.0 = 100 %)
    use_stop: bool = False        # True => stop real a atr_mult x ATR

    @classmethod
    def from_dict(cls, d: dict | None) -> "RiskConfig":
        d = d or {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def position_size(equity: float, cash: float, price: float, atr_value: float | None,
                  cfg: RiskConfig) -> int:
    """Número entero de acciones a comprar."""
    if price <= 0 or equity <= 0:
        return 0
    if cfg.mode == "risk":
        if atr_value is None or not math.isfinite(atr_value) or atr_value <= 0:
            return 0
        risk_per_share = cfg.atr_mult * atr_value
        shares = (equity * cfg.risk_pct) / risk_per_share
    elif cfg.mode == "fraction":
        shares = (equity * cfg.fraction) / price
    else:
        raise ValueError(f"Modo de riesgo desconocido: {cfg.mode}")
    max_by_exposure = (equity * cfg.max_position_pct) / price
    max_by_cash = cash / price
    return max(0, int(math.floor(min(shares, max_by_exposure, max_by_cash))))

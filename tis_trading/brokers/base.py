"""Interfaz común de bróker."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd

from tis_trading.risk import RiskConfig, position_size


@dataclass
class Order:
    symbol: str
    side: str          # "BUY" | "SELL"
    quantity: float    # acciones (Schwab/IBKR/paper) o lotes (MT5)
    signal_date: str   # fecha (YYYY-MM-DD) de la vela que generó la señal
    reason: str = ""
    order_id: str | None = None
    status: str = "PENDING"


class Broker(ABC):
    name = "base"
    #: True si ``price_history_daily`` solo devuelve velas ya cerradas.
    bars_complete = False

    @abstractmethod
    def get_cash(self) -> float: ...

    @abstractmethod
    def get_equity(self, prices: dict[str, float]) -> float: ...

    @abstractmethod
    def get_positions(self) -> dict[str, float]:
        """Cantidad por símbolo (solo posiciones largas)."""

    @abstractmethod
    def place_market_order(self, order: Order) -> Order: ...

    def has_pending(self, symbol: str) -> bool:
        return False

    def price_history_daily(self, symbol: str) -> pd.DataFrame:
        raise NotImplementedError(f"El bróker {self.name} no ofrece datos históricos")

    def size_position(self, symbol: str, equity: float, cash: float, price: float,
                      atr_value: float, risk: RiskConfig) -> float:
        """Tamaño de la orden. Por defecto: acciones enteras."""
        return position_size(equity, cash, price, atr_value, risk)

    def order_cost(self, symbol: str, quantity: float, price: float) -> float:
        """Efectivo (o margen) que consume una compra; se usa para no sobreasignar."""
        return quantity * price

    def close(self):
        """Libera conexiones (IBKR / MT5)."""

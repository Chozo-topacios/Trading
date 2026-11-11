"""Interfaz común de bróker."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Order:
    symbol: str
    side: str          # "BUY" | "SELL"
    quantity: int
    signal_date: str   # fecha (YYYY-MM-DD) de la vela que generó la señal
    reason: str = ""
    order_id: str | None = None
    status: str = "PENDING"


class Broker(ABC):
    name = "base"

    @abstractmethod
    def get_cash(self) -> float: ...

    @abstractmethod
    def get_equity(self, prices: dict[str, float]) -> float: ...

    @abstractmethod
    def get_positions(self) -> dict[str, int]:
        """Cantidad de acciones por símbolo (solo posiciones largas)."""

    @abstractmethod
    def place_market_order(self, order: Order) -> Order: ...

    def has_pending(self, symbol: str) -> bool:
        return False

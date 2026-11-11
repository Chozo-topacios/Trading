"""Adaptador de MetaTrader 5 (Exness u otro bróker MT5) usando el paquete oficial ``MetaTrader5``.

Requisitos:
  * Windows (o Wine) con el terminal MT5 instalado y abierto.
  * ``pip install MetaTrader5``
  * Credenciales en variables de entorno (opcional si el terminal ya tiene sesión):
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER (p. ej. "Exness-MT5Trial").
  * En MT5: Herramientas -> Opciones -> Asesores Expertos -> "Permitir trading algorítmico".

Diferencias con acciones:
  * Se operan CFDs (p. ej. ``USTEC`` = Nasdaq 100 en Exness; en cuentas Standard
    puede llevar sufijo, p. ej. ``USTECm``). Los símbolos distinguen mayúsculas.
  * El tamaño va en LOTES y se calcula con el valor del tick del símbolo.
  * Las velas diarias se cierran según la hora del servidor del bróker, por eso
    las señales no coinciden exactamente con las del ETF QQQ.
  * Hay swap (financiación nocturna) por cada noche con la posición abierta.

Seguridad: en modo ``mt5_demo`` el adaptador se niega a operar si la cuenta
conectada no es DEMO (``account_info().trade_mode``).
"""
from __future__ import annotations

import logging
import math
import os

import pandas as pd

from tis_trading.brokers.base import Broker, Order
from tis_trading.risk import RiskConfig

log = logging.getLogger(__name__)


class Mt5Broker(Broker):
    name = "mt5"
    bars_complete = True  # se piden velas desde la posición 1 (la 0 es la vela en curso)

    def __init__(self, demo: bool = True, magic: int = 20250202, deviation: int = 20,
                 terminal_path: str | None = None, login: int | None = None,
                 password: str | None = None, server: str | None = None, mt5=None):
        if mt5 is None:
            try:
                import MetaTrader5 as mt5
            except ImportError as e:  # pragma: no cover - depende del entorno
                raise RuntimeError("Instala la librería (solo Windows): pip install MetaTrader5") from e
        self.mt5 = mt5
        self.magic, self.deviation = int(magic), int(deviation)
        login = login or (int(os.environ["MT5_LOGIN"]) if os.environ.get("MT5_LOGIN") else None)
        kwargs = {k: v for k, v in {
            "path": terminal_path, "login": login,
            "password": password or os.environ.get("MT5_PASSWORD"),
            "server": server or os.environ.get("MT5_SERVER"),
        }.items() if v}
        if not mt5.initialize(**kwargs):
            raise RuntimeError(f"No se pudo conectar con MT5: {mt5.last_error()}")
        info = mt5.account_info()
        if info is None:
            raise RuntimeError(f"MT5 sin cuenta activa: {mt5.last_error()}")
        self.is_demo = info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
        if demo and not self.is_demo:
            mt5.shutdown()
            raise PermissionError(
                f"Modo mt5_demo pero la cuenta {info.login} ({info.server}) NO es demo. "
                "Inicia sesión en MT5 con tu cuenta demo."
            )
        log.info("MT5 conectado: cuenta %s en %s (%s)", info.login, info.server,
                 "DEMO" if self.is_demo else "REAL")

    # -- helpers ------------------------------------------------------------
    def _symbol(self, symbol: str):
        if not self.mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Símbolo '{symbol}' no disponible en este bróker (¿sufijo? p. ej. USTECm)")
        return self.mt5.symbol_info(symbol)

    def _value_per_point(self, info) -> float:
        """Valor en divisa de la cuenta de un movimiento de 1.0 en el precio, por lote."""
        if info.trade_tick_size > 0 and info.trade_tick_value > 0:
            return info.trade_tick_value / info.trade_tick_size
        return float(info.trade_contract_size)

    def _filling(self, info) -> int:
        m = self.mt5
        if info.filling_mode & 1:
            return m.ORDER_FILLING_FOK
        if info.filling_mode & 2:
            return m.ORDER_FILLING_IOC
        return m.ORDER_FILLING_RETURN

    def _our_positions(self, symbol: str | None = None):
        m = self.mt5
        pos = m.positions_get(symbol=symbol) if symbol else m.positions_get()
        return [p for p in (pos or []) if p.type == m.POSITION_TYPE_BUY and p.magic == self.magic]

    # -- datos ------------------------------------------------------------------
    def price_history_daily(self, symbol: str, count: int = 600) -> pd.DataFrame:
        self._symbol(symbol)
        rates = self.mt5.copy_rates_from_pos(symbol, self.mt5.TIMEFRAME_D1, 1, count)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"MT5 no devolvió velas para {symbol}: {self.mt5.last_error()}")
        df = pd.DataFrame(rates)
        out = pd.DataFrame({"Open": df["open"], "High": df["high"], "Low": df["low"],
                            "Close": df["close"], "Volume": df["tick_volume"]}).astype(float)
        out.index = pd.DatetimeIndex(pd.to_datetime(df["time"], unit="s").dt.normalize(), name="Date")
        return out

    # -- cuenta ---------------------------------------------------------------
    def get_cash(self) -> float:
        return float(self.mt5.account_info().margin_free)

    def get_equity(self, prices: dict[str, float]) -> float:
        return float(self.mt5.account_info().equity)

    def get_positions(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for p in self._our_positions():
            out[p.symbol] = round(out.get(p.symbol, 0.0) + float(p.volume), 8)
        return out

    def has_pending(self, symbol: str) -> bool:
        orders = self.mt5.orders_get(symbol=symbol) or []
        return any(o.magic == self.magic for o in orders)

    # -- tamaño ---------------------------------------------------------------
    def _round_volume(self, lots: float, info) -> float:
        step = info.volume_step or 0.01
        lots = math.floor(lots / step + 1e-9) * step
        lots = min(lots, info.volume_max)
        return round(lots, 8) if lots >= info.volume_min else 0.0

    def size_position(self, symbol, equity, cash, price, atr_value, risk: RiskConfig) -> float:
        info = self._symbol(symbol)
        vpp = self._value_per_point(info)
        if risk.mode == "risk":
            if not atr_value or not math.isfinite(atr_value) or atr_value <= 0:
                return 0.0
            lots = equity * risk.risk_pct / (risk.atr_mult * atr_value * vpp)
        else:
            lots = equity * risk.fraction / (price * vpp)
        lots = min(lots, equity * risk.max_position_pct / (price * vpp))  # tope de exposición nocional
        lots = self._round_volume(lots, info)
        # Comprobación de margen: reducir hasta que quepa en el margen libre
        while lots > 0:
            margin = self.mt5.order_calc_margin(self.mt5.ORDER_TYPE_BUY, symbol, lots, price)
            if margin is not None and margin <= cash:
                break
            lots = self._round_volume(lots - info.volume_step, info)
        return lots

    def order_cost(self, symbol: str, quantity: float, price: float) -> float:
        m = self.mt5.order_calc_margin(self.mt5.ORDER_TYPE_BUY, symbol, quantity, price)
        return float(m or 0.0)

    # -- órdenes --------------------------------------------------------------
    def _send(self, request: dict):
        res = self.mt5.order_send(request)
        if res is None or res.retcode != self.mt5.TRADE_RETCODE_DONE:
            code = getattr(res, "retcode", None)
            raise RuntimeError(f"MT5 rechazó la orden (retcode={code}): "
                               f"{getattr(res, 'comment', self.mt5.last_error())}")
        return res

    def place_market_order(self, order: Order) -> Order:
        m = self.mt5
        info = self._symbol(order.symbol)
        tick = m.symbol_info_tick(order.symbol)
        base = {"action": m.TRADE_ACTION_DEAL, "symbol": order.symbol, "deviation": self.deviation,
                "magic": self.magic, "comment": f"tis {order.reason}"[:31],
                "type_time": m.ORDER_TIME_GTC, "type_filling": self._filling(info)}
        tickets = []
        if order.side == "BUY":
            res = self._send({**base, "volume": float(order.quantity),
                              "type": m.ORDER_TYPE_BUY, "price": tick.ask})
            tickets.append(res.order)
        else:  # cerrar nuestras posiciones (cuentas hedging o netting)
            for p in self._our_positions(order.symbol):
                res = self._send({**base, "volume": float(p.volume), "type": m.ORDER_TYPE_SELL,
                                  "price": tick.bid, "position": p.ticket})
                tickets.append(res.order)
        order.order_id = ",".join(map(str, tickets))
        order.status = "FILLED" if tickets else "NO_POSITION"
        log.warning("MT5 %s %s %s lotes -> tickets %s", order.side, order.symbol,
                    order.quantity, order.order_id)
        return order

    def close(self):
        self.mt5.shutdown()

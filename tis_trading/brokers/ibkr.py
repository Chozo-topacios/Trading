"""Adaptador de Interactive Brokers (TWS / IB Gateway) usando ``ib_async``.

Requisitos:
  * ``pip install ib_async``
  * TWS o IB Gateway abierto y con la API activada
    (Configure -> API -> Settings -> "Enable ActiveX and Socket Clients").
  * Puertos por defecto: TWS paper 7497 / real 7496 ; Gateway paper 4002 / real 4001.

Seguridad: en modo ``ibkr_paper`` el adaptador se niega a operar si la cuenta
conectada no es de paper trading (las cuentas paper empiezan por "DU").

Órdenes: por defecto Market-on-Open (``MKT`` con ``tif=OPG``), que ejecuta en la
subasta de apertura, exactamente como piden las reglas. Hay que enviarlas antes
de las ~9:28 ET; ejecutar el bot tras el cierre (16:15 ET) cumple de sobra.
"""
from __future__ import annotations

import logging

import pandas as pd

from tis_trading.brokers.base import Broker, Order

log = logging.getLogger(__name__)

PAPER_PORTS = (7497, 4002)


def is_paper_account(account_id: str) -> bool:
    return account_id.upper().startswith("DU")


class IbkrBroker(Broker):
    name = "ibkr"

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 17,
                 account: str | None = None, paper: bool = True, order_type: str = "MOO",
                 exchange: str = "SMART", currency: str = "USD", ib=None):
        try:
            import ib_async as iba
        except ImportError as e:  # pragma: no cover - depende del entorno
            raise RuntimeError("Instala la librería: pip install ib_async") from e
        self.iba = iba
        self.ib = ib or iba.IB()
        self.exchange, self.currency = exchange, currency
        self.order_type = order_type.upper()
        if self.order_type not in ("MOO", "MKT"):
            raise ValueError("order_type debe ser MOO o MKT")
        if not self.ib.isConnected():
            self.ib.connect(host, int(port), clientId=int(client_id), timeout=20)
        accounts = list(self.ib.managedAccounts())
        if not accounts:
            raise RuntimeError("IBKR no devolvió cuentas")
        self.account = account or accounts[0]
        if self.account not in accounts:
            raise RuntimeError(f"La cuenta {self.account} no está en esta sesión de TWS/Gateway")
        if paper and not is_paper_account(self.account):
            raise PermissionError(
                f"Modo ibkr_paper pero la cuenta {self.account} NO es paper (deben empezar por DU). "
                "Inicia TWS/Gateway con tu usuario de paper trading."
            )
        if paper and int(port) not in PAPER_PORTS:
            log.warning("Puerto %s no es el habitual de paper (%s)", port, PAPER_PORTS)
        self._contracts: dict = {}

    # -- helpers ------------------------------------------------------------
    def contract(self, symbol: str):
        if symbol not in self._contracts:
            c = self.iba.Stock(symbol, self.exchange, self.currency)
            self.ib.qualifyContracts(c)
            self._contracts[symbol] = c
        return self._contracts[symbol]

    def _summary(self, tag: str) -> float:
        for v in self.ib.accountSummary(self.account):
            if v.tag == tag and v.currency in (self.currency, "BASE", ""):
                return float(v.value)
        return 0.0

    # -- datos ------------------------------------------------------------------
    def price_history_daily(self, symbol: str, duration: str = "2 Y") -> pd.DataFrame:
        bars = self.ib.reqHistoricalData(
            self.contract(symbol), endDateTime="", durationStr=duration,
            barSizeSetting="1 day", whatToShow="TRADES", useRTH=True, formatDate=1,
        )
        if not bars:
            raise RuntimeError(f"IBKR no devolvió velas para {symbol} (¿permisos de datos?)")
        df = pd.DataFrame({
            "Open": [b.open for b in bars], "High": [b.high for b in bars],
            "Low": [b.low for b in bars], "Close": [b.close for b in bars],
            "Volume": [b.volume for b in bars],
        }, index=pd.DatetimeIndex(pd.to_datetime([str(b.date) for b in bars]), name="Date"))
        return df.astype(float)

    # -- cuenta ---------------------------------------------------------------
    def get_cash(self) -> float:
        return self._summary("AvailableFunds") or self._summary("TotalCashValue")

    def get_equity(self, prices: dict[str, float]) -> float:
        return self._summary("NetLiquidation")

    def get_positions(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for p in self.ib.positions(self.account):
            if p.contract.secType == "STK" and p.position > 0:
                out[p.contract.symbol] = out.get(p.contract.symbol, 0) + float(p.position)
        return out

    def has_pending(self, symbol: str) -> bool:
        return any(t.contract.symbol == symbol and not t.isDone() for t in self.ib.openTrades())

    # -- órdenes --------------------------------------------------------------
    def build_order(self, side: str, quantity: float):
        if self.order_type == "MOO":
            o = self.iba.Order(action=side, totalQuantity=quantity, orderType="MKT", tif="OPG")
        else:
            o = self.iba.MarketOrder(side, quantity, tif="DAY")
        o.account = self.account
        return o

    def place_market_order(self, order: Order) -> Order:
        trade = self.ib.placeOrder(self.contract(order.symbol),
                                   self.build_order(order.side, int(order.quantity)))
        order.order_id = str(trade.order.orderId)
        order.status = f"SUBMITTED_{self.order_type}"
        log.warning("Orden enviada a IBKR (%s) id=%s: %s %s %s", self.account,
                    order.order_id, order.side, order.quantity, order.symbol)
        return order

    def close(self):
        if self.ib.isConnected():
            self.ib.disconnect()

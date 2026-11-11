"""Tests del adaptador IBKR con un módulo ib_async simulado."""
import sys
from types import ModuleType, SimpleNamespace as NS

import pytest


class FakeOrder(NS):
    pass


def install_fake_ib_async(monkeypatch):
    mod = ModuleType("ib_async")
    mod.Stock = lambda sym, exch, cur: NS(symbol=sym, exchange=exch, currency=cur, secType="STK")
    mod.Order = lambda **kw: FakeOrder(**kw)
    mod.MarketOrder = lambda action, qty, **kw: FakeOrder(action=action, totalQuantity=qty,
                                                         orderType="MKT", **kw)
    mod.IB = object
    monkeypatch.setitem(sys.modules, "ib_async", mod)


class FakeIB:
    def __init__(self, account="DU1234567"):
        self.acct = account
        self.placed = []

    def isConnected(self): return True
    def managedAccounts(self): return [self.acct]
    def qualifyContracts(self, c): return [c]
    def accountSummary(self, a):
        return [NS(tag="NetLiquidation", value="1000000", currency="USD"),
                NS(tag="AvailableFunds", value="250000", currency="USD")]
    def positions(self, a):
        return [NS(contract=NS(symbol="QQQ", secType="STK"), position=10.0)]
    def openTrades(self): return []
    def placeOrder(self, c, o):
        self.placed.append((c, o))
        return NS(order=NS(orderId=42))
    def disconnect(self): pass


def test_refuses_live_account_in_paper_mode(monkeypatch):
    install_fake_ib_async(monkeypatch)
    from tis_trading.brokers.ibkr import IbkrBroker
    with pytest.raises(PermissionError):
        IbkrBroker(ib=FakeIB(account="U7654321"), paper=True)


def test_paper_account_and_moo_order(monkeypatch):
    install_fake_ib_async(monkeypatch)
    from tis_trading.brokers.base import Order
    from tis_trading.brokers.ibkr import IbkrBroker
    ib = FakeIB()
    b = IbkrBroker(ib=ib, paper=True)
    assert b.get_equity({}) == 1_000_000 and b.get_cash() == 250_000
    assert b.get_positions() == {"QQQ": 10.0}
    o = b.place_market_order(Order("QQQ", "BUY", 5, "2026-11-10"))
    contract, order = ib.placed[0]
    assert order.tif == "OPG" and order.orderType == "MKT" and order.totalQuantity == 5
    assert order.account == "DU1234567" and contract.symbol == "QQQ"
    assert o.order_id == "42"


def test_live_modes_locked():
    from tis_trading.engine import Config, build_broker
    for mode, section in (("ibkr_live", "ibkr"), ("mt5_live", "mt5")):
        cfg = Config(mode=mode, **{section: {"live_trading_enabled": False}})
        with pytest.raises(PermissionError):
            build_broker(cfg, confirm_live=True)
        cfg = Config(mode=mode, **{section: {"live_trading_enabled": True}})
        with pytest.raises(PermissionError):
            build_broker(cfg, confirm_live=False)

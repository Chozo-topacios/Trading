"""Tests del adaptador MT5 con un módulo MetaTrader5 simulado."""
from types import SimpleNamespace as NS

import numpy as np
import pandas as pd
import pytest

from tests.test_strategies import uptrend_with_dip
from tis_trading.brokers.base import Order
from tis_trading.brokers.mt5 import Mt5Broker
from tis_trading.engine import Config, Engine
from tis_trading.risk import RiskConfig


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_REAL = 0, 2
    TIMEFRAME_D1 = 16408
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    POSITION_TYPE_BUY = 0
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self, trade_mode=0, df=None):
        self.trade_mode = trade_mode
        self.df = df
        self.sent = []
        self.positions = []
        self.info = NS(trade_contract_size=1.0, trade_tick_size=0.01, trade_tick_value=0.01,
                       volume_min=0.1, volume_max=100.0, volume_step=0.1, filling_mode=2)

    def initialize(self, **kw): return True
    def shutdown(self): pass
    def last_error(self): return (0, "ok")
    def account_info(self):
        return NS(trade_mode=self.trade_mode, login=123, server="Exness-MT5Trial",
                  equity=10_000.0, margin_free=10_000.0)
    def symbol_select(self, s, on): return s == "USTEC"
    def symbol_info(self, s): return self.info
    def symbol_info_tick(self, s): return NS(ask=101.0, bid=100.0)
    def order_calc_margin(self, t, s, lots, price): return lots * price / 20  # apalancamiento 1:20
    def orders_get(self, symbol=None): return []
    def positions_get(self, symbol=None):
        return [p for p in self.positions if symbol in (None, p.symbol)]
    def copy_rates_from_pos(self, s, tf, start, count):
        d = self.df.iloc[: len(self.df) - start] if start else self.df
        return np.array([(int(t.timestamp()), r.Open, r.High, r.Low, r.Close, 100)
                         for t, r in d.tail(count).iterrows()],
                        dtype=[("time", "i8"), ("open", "f8"), ("high", "f8"),
                               ("low", "f8"), ("close", "f8"), ("tick_volume", "i8")])
    def order_send(self, req):
        self.sent.append(req)
        if req["type"] == self.ORDER_TYPE_BUY:
            self.positions.append(NS(symbol=req["symbol"], type=0, magic=req["magic"],
                                     volume=req["volume"], ticket=len(self.sent)))
        else:
            self.positions = [p for p in self.positions if p.ticket != req["position"]]
        return NS(retcode=self.TRADE_RETCODE_DONE, order=len(self.sent), comment="done")


def test_refuses_real_account_in_demo_mode():
    with pytest.raises(PermissionError):
        Mt5Broker(demo=True, mt5=FakeMT5(trade_mode=2))


def test_lot_sizing_risk_and_margin():
    b = Mt5Broker(mt5=FakeMT5())
    # vpp = 0.01/0.01 = 1 ; riesgo 1 % de 10k = 100 ; 2 x ATR(5) = 10 -> 10 lotes
    r = RiskConfig(mode="risk", risk_pct=0.01, atr_mult=2, max_position_pct=10)
    assert b.size_position("USTEC", 10_000, 10_000, 100, 5.0, r) == pytest.approx(10.0)
    # tope de exposición nocional 50 % -> 5000/100 = 50 lotes (no limita) ; con 5 % -> 5 lotes
    r.max_position_pct = 0.05
    assert b.size_position("USTEC", 10_000, 10_000, 100, 5.0, r) == pytest.approx(5.0)
    # margen libre insuficiente: 10 lotes*100/20 = 50 > 30 -> reduce a 6 lotes
    r.max_position_pct = 10
    assert b.size_position("USTEC", 10_000, 30, 100, 5.0, r) == pytest.approx(6.0)


def test_unknown_symbol_message():
    b = Mt5Broker(mt5=FakeMT5())
    with pytest.raises(RuntimeError, match="sufijo"):
        b.size_position("QQQ", 10_000, 10_000, 100, 5.0, RiskConfig())


def test_engine_cycle_with_mt5(tmp_path):
    df = uptrend_with_dip()
    fake = FakeMT5(df=df.iloc[:253])      # posición 0 = vela en curso (252) -> última cerrada 251
    b = Mt5Broker(mt5=fake)
    bars = b.price_history_daily("USTEC")
    assert bars.index[-1] == df.index[251]

    cfg = Config(mode="mt5_demo", data_source="mt5", state_dir=str(tmp_path),
                 portfolio=[{"symbol": "USTEC", "strategy": "tis_rsi2"}],
                 risk=RiskConfig(mode="fraction", fraction=0.5))
    eng = Engine(cfg, b)
    rep = eng.run_once()
    assert rep[0]["action"].startswith("BUY") and b.get_positions()["USTEC"] > 0

    # dos velas verdes después -> cierra la posición por ticket
    fake.df = df.iloc[:255]
    rep = Engine(cfg, b).run_once()
    assert rep[0]["action"].startswith("SELL")
    assert b.get_positions() == {}
    assert fake.sent[-1]["position"] == 1 and fake.sent[-1]["type"] == FakeMT5.ORDER_TYPE_SELL

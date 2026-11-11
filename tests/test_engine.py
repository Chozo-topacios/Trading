from datetime import datetime

import pandas as pd
import pytest

from tests.test_strategies import uptrend_with_dip
from tis_trading.brokers.paper import PaperBroker
from tis_trading.brokers.schwab import market_order_payload
from tis_trading.engine import NY, Config, Engine, build_broker, last_complete_bar
from tis_trading.risk import RiskConfig


def make_engine(tmp_path, upto):
    cfg = Config(state_dir=str(tmp_path), portfolio=[{"symbol": "QQQ", "strategy": "tis_rsi2"}],
                 risk=RiskConfig(mode="fraction", fraction=0.5))
    broker = PaperBroker(tmp_path / "paper.json", 100_000, 5)
    return Engine(cfg, broker), broker


def test_paper_full_cycle(tmp_path):
    df = uptrend_with_dip()
    eng, broker = make_engine(tmp_path, 251)

    rep = eng.run_once({"QQQ": df.iloc[:252]})          # señal al cierre de la vela 251
    assert rep[0]["action"].startswith("BUY")
    assert broker.state["pending"]

    rep = eng.run_once({"QQQ": df.iloc[:252]})          # re-ejecución: no duplica
    assert "YA ENVIADA" in rep[0]["action"]

    rep = eng.run_once({"QQQ": df.iloc[:253]})          # llena en apertura de 252
    assert rep[0]["action"].startswith("FILLED BUY")
    assert rep[0]["price"] == pytest.approx(df["Open"].iloc[252])
    assert rep[1]["action"] == "MANTENER"

    rep = eng.run_once({"QQQ": df.iloc[:254]})          # 2ª vela verde -> vender
    assert rep[0]["action"].startswith("SELL")

    rep = eng.run_once({"QQQ": df.iloc[:255]})
    assert rep[0]["action"].startswith("FILLED SELL")
    assert broker.get_positions() == {}
    assert broker.get_cash() > 100_000                  # operación ganadora


def test_drop_incomplete_today_bar():
    idx = pd.bdate_range("2026-11-09", "2026-11-11")
    df = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0}, index=idx)
    assert len(last_complete_bar(df, datetime(2026, 11, 11, 11, 0, tzinfo=NY))) == 2
    assert len(last_complete_bar(df, datetime(2026, 11, 11, 16, 30, tzinfo=NY))) == 3


def test_live_mode_is_locked():
    cfg = Config(mode="live", schwab={"live_trading_enabled": False})
    with pytest.raises(PermissionError):
        build_broker(cfg, confirm_live=True)


def test_schwab_order_payload():
    p = market_order_payload("QQQ", "BUY", 7)
    leg = p["orderLegCollection"][0]
    assert p["orderType"] == "MARKET" and leg["quantity"] == 7
    assert leg["instrument"] == {"symbol": "QQQ", "assetType": "EQUITY"}


def test_example_config_loads():
    cfg = Config.load("config.example.toml")
    assert cfg.mode == "paper" and cfg.portfolio[0]["strategy"] == "tis_rsi2"

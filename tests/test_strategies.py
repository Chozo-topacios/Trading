import numpy as np

from tests.conftest import make_ohlc
from tis_trading.strategies import STRATEGIES, TisRsi2, get_strategy


def uptrend_with_dip():
    closes = list(np.linspace(100, 200, 250))
    closes += [195, 189]                  # caída fuerte -> RSI(2) muy bajo
    closes += [190, 192, 194, 196]        # rebote
    opens = [closes[0]] + closes[:-1]
    return make_ohlc(closes, opens)


def test_tis_rsi2_entry_on_dip():
    df = uptrend_with_dip()
    s = TisRsi2()
    p = s.prepare(df)
    i = 251  # vela del cierre en 189
    assert p["rsi"].iloc[i] < 15 and p["Close"].iloc[i] > p["sma_trend"].iloc[i]
    assert s.entry(p, i)
    assert not s.entry(p, 240)   # en plena subida el RSI2 está alto


def test_tis_rsi2_exit_two_green_candles_since_entry():
    df = uptrend_with_dip()
    s = TisRsi2()
    p = s.prepare(df)
    entry_i = 252          # compra en la apertura de la vela siguiente a la señal
    assert p["green"].iloc[252] and p["green"].iloc[253]
    assert not s.exit(p, 252, entry_i)   # solo 1 vela verde en posición
    assert s.exit(p, 253, entry_i)       # 2 velas verdes consecutivas


def test_no_entry_below_sma200():
    closes = list(np.linspace(200, 100, 260))
    df = make_ohlc(closes)
    s = TisRsi2()
    p = s.prepare(df)
    assert not any(s.entry(p, i) for i in range(200, len(p)))


def test_registry_and_params():
    assert set(STRATEGIES) == {"tis_rsi2", "connors_rsi2", "double7", "ibs"}
    s = get_strategy("tis_rsi2", {"rsi_entry": 10})
    assert s.params["rsi_entry"] == 10 and s.params["sma_period"] == 200

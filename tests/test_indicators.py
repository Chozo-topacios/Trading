import numpy as np
import pandas as pd

from tis_trading import indicators as ind


def test_rsi_bounds_and_extremes():
    up = pd.Series(np.arange(1, 30, dtype=float))
    assert ind.rsi(up, 2).dropna().eq(100).all()
    down = pd.Series(np.arange(30, 1, -1, dtype=float))
    assert ind.rsi(down, 2).dropna().eq(0).all()


def test_rsi_wilder_known_value():
    # Cambios: +1, -2  -> avg_gain=0.5, avg_loss=1 (semilla ewm) ; siguiente cambio +1
    s = pd.Series([10.0, 11.0, 9.0, 10.0])
    r = ind.rsi(s, 2)
    # ewm alpha=0.5: gain=[nan,1,0,1] -> 1, 0.5, 0.75 ; loss=[nan,0,2,0] -> 0,1,0.5
    assert abs(r.iloc[-1] - (100 - 100 / (1 + 0.75 / 0.5))) < 1e-9


def test_sma_and_atr_shapes(random_walk):
    assert ind.sma(random_walk["Close"], 200).isna().sum() == 199
    a = ind.atr(random_walk, 14)
    assert (a.dropna() > 0).all()

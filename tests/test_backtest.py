import pytest

from tis_trading.backtest import run_backtest
from tis_trading.risk import RiskConfig, position_size
from tis_trading.strategies import STRATEGIES, get_strategy


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_backtest_runs_and_is_consistent(random_walk, name):
    res = run_backtest(random_walk, get_strategy(name), initial_capital=100_000,
                       risk=RiskConfig(mode="fraction", fraction=1.0))
    m = res.metrics
    assert m["trades"] > 0
    assert len(res.equity) == len(random_walk)
    # La equity final debe cuadrar con el P&L de las operaciones menos costes
    pnl = sum(t.pnl for t in res.trades)
    costs = sum((t.entry_price + t.exit_price) * t.shares * 5 / 10_000 for t in res.trades)
    assert m["final_equity"] - 100_000 == pytest.approx(pnl - costs, abs=0.05)
    for t in res.trades:
        assert t.exit_date >= t.entry_date
        # entradas siempre en una apertura
        assert t.entry_price == random_walk.loc[t.entry_date, "Open"]


def test_signal_executes_next_open(random_walk):
    s = get_strategy("tis_rsi2")
    res = run_backtest(random_walk, s, risk=RiskConfig(mode="fraction"))
    p = s.prepare(random_walk)
    t = res.trades[0]
    i = random_walk.index.get_loc(t.entry_date)
    assert s.entry(p, i - 1)


def test_position_size_risk_mode():
    cfg = RiskConfig(mode="risk", risk_pct=0.01, atr_mult=2.0, max_position_pct=1.0)
    # 1 % de 100k = 1000 ; riesgo/acción = 2*5 = 10 -> 100 acciones
    assert position_size(100_000, 100_000, 100.0, 5.0, cfg) == 100
    # limitado por efectivo
    assert position_size(100_000, 5_000, 100.0, 5.0, cfg) == 50
    assert position_size(100_000, 100_000, 100.0, float("nan"), cfg) == 0

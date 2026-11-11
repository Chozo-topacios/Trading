"""Backtester barra a barra (datos diarios).

Señal al cierre de la vela i -> ejecución en la apertura de la vela i+1.
Costes en puntos básicos por lado (el research TIS usa 5 bps).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from tis_trading import indicators as ind
from tis_trading.risk import RiskConfig, position_size
from tis_trading.strategies import Strategy, should_exit


@dataclass
class Trade:
    entry_date: pd.Timestamp
    entry_price: float
    shares: int
    exit_date: pd.Timestamp | None = None
    exit_price: float | None = None
    reason: str = ""

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.shares if self.exit_price else 0.0

    @property
    def ret(self) -> float:
        return self.exit_price / self.entry_price - 1 if self.exit_price else 0.0


@dataclass
class BacktestResult:
    strategy: str
    symbol: str
    equity: pd.Series
    trades: list[Trade] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame([{**t.__dict__, "pnl": t.pnl, "ret": t.ret} for t in self.trades])


def run_backtest(df: pd.DataFrame, strategy: Strategy, *, symbol: str = "",
                 initial_capital: float = 100_000.0, cost_bps: float = 5.0,
                 risk: RiskConfig | None = None) -> BacktestResult:
    risk = risk or RiskConfig()
    data = strategy.prepare(df)
    data["atr"] = ind.atr(data, risk.atr_period)
    cost = cost_bps / 10_000.0

    cash = initial_capital
    shares = 0
    entry_i = -1
    stop_price = None
    pending: str | None = None
    pending_reason = ""
    trades: list[Trade] = []
    equity = np.empty(len(data))
    o, h, l, c = (data[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    atr_arr = data["atr"].to_numpy(float)
    dates = data.index

    def close_trade(i: int, price: float, reason: str):
        nonlocal cash, shares, entry_i, stop_price
        cash += shares * price * (1 - cost)
        trades[-1].exit_date, trades[-1].exit_price, trades[-1].reason = dates[i], price, reason
        shares, entry_i, stop_price = 0, -1, None

    for i in range(len(data)):
        # 1) Ejecutar órdenes pendientes en la apertura
        if pending == "buy" and shares == 0:
            eq_prev = cash
            qty = position_size(eq_prev, cash / (1 + cost), o[i], atr_arr[i - 1] if i else None, risk)
            if qty > 0:
                cash -= qty * o[i] * (1 + cost)
                shares, entry_i = qty, i
                trades.append(Trade(dates[i], o[i], qty))
                if risk.use_stop and np.isfinite(atr_arr[i - 1]):
                    stop_price = o[i] - risk.atr_mult * atr_arr[i - 1]
        elif pending == "sell" and shares > 0:
            close_trade(i, o[i], pending_reason)
        pending = None

        # 2) Stop de precio opcional (intradía)
        if shares > 0 and stop_price is not None and l[i] <= stop_price:
            close_trade(i, min(o[i], stop_price), "stop")

        # 3) Señales al cierre
        if i >= strategy.warmup:
            if shares > 0:
                ex, why = should_exit(strategy, data, i, entry_i)
                if ex:
                    pending, pending_reason = "sell", why
            elif strategy.entry(data, i):
                pending = "buy"

        equity[i] = cash + shares * c[i]

    # Cerrar posición abierta al final (marcada a mercado)
    if shares > 0:
        close_trade(len(data) - 1, c[-1], "end_of_data")
        equity[-1] = cash

    eq = pd.Series(equity, index=dates, name="equity")
    res = BacktestResult(strategy.name, symbol, eq, trades)
    res.metrics = compute_metrics(eq, trades, data)
    return res


def compute_metrics(equity: pd.Series, trades: list[Trade], data: pd.DataFrame) -> dict:
    rets = equity.pct_change().dropna()
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    total = equity.iloc[-1] / equity.iloc[0] - 1
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if equity.iloc[-1] > 0 else -1.0
    dd = equity / equity.cummax() - 1
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    in_market = sum(
        int(((data.index >= t.entry_date) & (data.index <= t.exit_date)).sum()) for t in trades
    )
    out = {
        "total_return_pct": round(100 * total, 2),
        "cagr_pct": round(100 * cagr, 2),
        "max_drawdown_pct": round(100 * dd.min(), 2),
        "sharpe": round(float(np.sqrt(252) * rets.mean() / rets.std()), 2) if rets.std() > 0 else 0.0,
        "trades": len(trades),
        "win_rate_pct": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf") if gross_win else 0.0,
        "avg_trade_pct": round(100 * float(np.mean([t.ret for t in trades])), 3) if trades else 0.0,
        "exposure_pct": round(100 * in_market / len(data), 1),
        "final_equity": round(float(equity.iloc[-1]), 2),
    }
    return {k: float(v) if isinstance(v, (float, np.floating)) else v for k, v in out.items()}

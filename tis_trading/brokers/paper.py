"""Bróker simulado (modo DEMO).

La API pública de Charles Schwab NO ofrece una cuenta paper/sandbox para enviar
órdenes (paperMoney de thinkorswim no es accesible vía API). Por eso el modo
demo usa este bróker local: las órdenes se guardan como pendientes y se llenan
al precio de APERTURA de la siguiente vela diaria, con costes en bps, igual que
el backtest. Los datos de mercado sí pueden venir de Schwab.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from tis_trading.brokers.base import Broker, Order


class PaperBroker(Broker):
    name = "paper"

    def __init__(self, state_path: str | Path = "state/paper_account.json",
                 initial_cash: float = 100_000.0, cost_bps: float = 5.0):
        self.path = Path(state_path)
        self.cost = cost_bps / 10_000.0
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
        else:
            self.state = {"cash": initial_cash, "initial_cash": initial_cash,
                          "positions": {}, "pending": [], "fills": []}
            self.save()

    # -- persistencia -------------------------------------------------------
    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, indent=2, default=str))

    # -- Broker -------------------------------------------------------------
    def get_cash(self) -> float:
        return float(self.state["cash"])

    def get_positions(self) -> dict[str, int]:
        return {s: int(p["qty"]) for s, p in self.state["positions"].items() if p["qty"] > 0}

    def get_equity(self, prices: dict[str, float]) -> float:
        value = sum(q * prices.get(s, self.state["positions"][s]["entry_price"])
                    for s, q in self.get_positions().items())
        return self.get_cash() + value

    def has_pending(self, symbol: str) -> bool:
        return any(o["symbol"] == symbol for o in self.state["pending"])

    def place_market_order(self, order: Order) -> Order:
        order.order_id = f"PAPER-{len(self.state['fills']) + len(self.state['pending']) + 1}"
        order.status = "QUEUED"
        self.state["pending"].append(asdict(order))
        self.save()
        return order

    def process_pending(self, bars: dict[str, pd.DataFrame]) -> list[dict]:
        """Llena las órdenes pendientes en la apertura de la vela posterior a la señal."""
        filled, still = [], []
        for o in self.state["pending"]:
            df = bars.get(o["symbol"])
            nxt = None if df is None else df[df.index > pd.Timestamp(o["signal_date"])]
            if nxt is None or nxt.empty:
                still.append(o)
                continue
            fill_date, price = nxt.index[0], float(nxt["Open"].iloc[0])
            sym, qty = o["symbol"], int(o["quantity"])
            pos = self.state["positions"].get(sym, {"qty": 0, "entry_price": 0.0, "entry_date": None})
            if o["side"] == "BUY":
                qty = min(qty, int(self.state["cash"] // (price * (1 + self.cost))))
                if qty <= 0:
                    o["status"] = "REJECTED_NO_CASH"
                    self.state["fills"].append(o)
                    continue
                self.state["cash"] -= qty * price * (1 + self.cost)
                total = pos["qty"] + qty
                pos["entry_price"] = (pos["entry_price"] * pos["qty"] + price * qty) / total
                pos["qty"] = total
                pos["entry_date"] = pos["entry_date"] or str(fill_date.date())
            else:
                qty = min(qty, pos["qty"])
                self.state["cash"] += qty * price * (1 - self.cost)
                o["pnl"] = round((price - pos["entry_price"]) * qty, 2)
                pos["qty"] -= qty
                if pos["qty"] == 0:
                    pos = None
            if pos:
                self.state["positions"][sym] = pos
            else:
                self.state["positions"].pop(sym, None)
            o.update(status="FILLED", quantity=qty, fill_price=round(price, 4),
                     fill_date=str(fill_date.date()),
                     processed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            self.state["fills"].append(o)
            filled.append(o)
        self.state["pending"] = still
        self.save()
        return filled

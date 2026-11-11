"""Motor de ejecución diaria.

Pensado para ejecutarse una vez al día DESPUÉS del cierre (p. ej. 16:15 ET)
o antes de la apertura: evalúa las señales sobre la última vela diaria
COMPLETA y envía órdenes a mercado que se ejecutan en la apertura siguiente.
"""
from __future__ import annotations

import json
import logging
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from tis_trading import data as data_mod
from tis_trading import indicators as ind
from tis_trading.brokers.base import Broker, Order
from tis_trading.brokers.paper import PaperBroker
from tis_trading.risk import RiskConfig
from tis_trading.strategies import get_strategy, should_exit

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
MODES = ("paper", "schwab_dry_run", "schwab_live", "ibkr_paper", "ibkr_live", "mt5_demo", "mt5_live")
MODE_ALIASES = {"live": "schwab_live"}
#: Fuentes de datos que se leen del propio bróker conectado
BROKER_SOURCES = ("ibkr", "mt5", "broker")


@dataclass
class Config:
    mode: str = "paper"
    data_source: str = "csv"
    data_dir: str = "data"
    state_dir: str = "state"
    initial_capital: float = 100_000.0
    cost_bps: float = 5.0
    risk: RiskConfig = field(default_factory=RiskConfig)
    portfolio: list[dict] = field(default_factory=lambda: [{"symbol": "QQQ", "strategy": "tis_rsi2"}])
    schwab: dict = field(default_factory=dict)
    ibkr: dict = field(default_factory=dict)
    mt5: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None) -> "Config":
        if not path or not Path(path).exists():
            return cls()
        raw = tomllib.loads(Path(path).read_text())
        g = raw.get("general", {})
        cfg = cls(
            mode=g.get("mode", "paper"),
            data_source=g.get("data_source", "csv"),
            data_dir=g.get("data_dir", "data"),
            state_dir=g.get("state_dir", "state"),
            initial_capital=float(g.get("initial_capital", 100_000)),
            cost_bps=float(g.get("cost_bps", 5.0)),
            risk=RiskConfig.from_dict(raw.get("risk")),
            portfolio=raw.get("portfolio", cls().portfolio),
            schwab=raw.get("schwab", {}),
            ibkr=raw.get("ibkr", {}),
            mt5=raw.get("mt5", {}),
        )
        cfg.validate()
        return cfg

    def validate(self):
        self.mode = MODE_ALIASES.get(self.mode, self.mode)
        if self.mode not in MODES:
            raise ValueError(f"mode debe ser uno de {MODES}")
        for p in self.portfolio:
            if "symbol" not in p or "strategy" not in p:
                raise ValueError(f"Cada [[portfolio]] necesita 'symbol' y 'strategy': {p}")
        syms = [p["symbol"].strip() for p in self.portfolio]
        if len(syms) != len(set(syms)):
            raise ValueError("Cada símbolo solo puede tener una estrategia en el portafolio")
        if self.data_source in BROKER_SOURCES and self.mode == "paper":
            raise ValueError(f"data_source={self.data_source} requiere un modo ibkr_* o mt5_*")


def last_complete_bar(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """Descarta la vela de hoy si el mercado aún no ha cerrado (16:00 ET)."""
    now = now or datetime.now(NY)
    today = pd.Timestamp(now.date())
    if len(df) and df.index[-1] >= today and (now.hour, now.minute) < (16, 0):
        return df[df.index < today]
    return df


class Engine:
    def __init__(self, cfg: Config, broker: Broker, data_client=None, now: datetime | None = None):
        self.cfg = cfg
        self.broker = broker
        # Proveedor de velas: el cliente de Schwab o el propio bróker (IBKR / MT5)
        self.client = broker if cfg.data_source in BROKER_SOURCES else data_client
        self.now = now
        self.state_path = Path(cfg.state_dir) / f"engine_{cfg.mode}.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    def _save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2))

    def load_bars(self) -> dict[str, pd.DataFrame]:
        bars = {}
        for item in self.cfg.portfolio:
            sym = item["symbol"].strip()
            df = data_mod.load(sym, self.cfg.data_source, self.cfg.data_dir, self.client)
            complete = getattr(self.client, "bars_complete", False)
            bars[sym] = df if complete else last_complete_bar(df, self.now)
        return bars

    def run_once(self, bars: dict[str, pd.DataFrame] | None = None) -> list[dict]:
        bars = bars or self.load_bars()
        report: list[dict] = []

        if isinstance(self.broker, PaperBroker):
            for f in self.broker.process_pending(bars):
                log.info("Paper fill: %s", f)
                report.append({"symbol": f["symbol"], "action": f"FILLED {f['side']}",
                               "qty": f["quantity"], "price": f["fill_price"], "date": f["fill_date"]})

        positions = self.broker.get_positions()
        prices = {s: float(df["Close"].iloc[-1]) for s, df in bars.items() if len(df)}
        equity = self.broker.get_equity(prices)
        cash = self.broker.get_cash()

        for item in self.cfg.portfolio:
            sym = item["symbol"].strip()
            strat = get_strategy(item["strategy"], item.get("params"))
            df = strat.prepare(bars[sym])
            df["atr"] = ind.atr(df, self.cfg.risk.atr_period)
            i = len(df) - 1
            bar_date = str(df.index[i].date())
            st = self.state.setdefault(sym, {})
            qty = positions.get(sym, 0)
            row = {"symbol": sym, "strategy": strat.name, "bar": bar_date,
                   "close": round(float(df["Close"].iloc[i]), 2), "position": qty}
            for col in ("rsi", "sma_trend", "ibs"):
                if col in df:
                    row[col] = round(float(df[col].iloc[i]), 2)

            if i < strat.warmup:
                report.append({**row, "action": "SIN DATOS SUFICIENTES"})
                continue
            if self.broker.has_pending(sym) or st.get("last_order_bar") == bar_date:
                report.append({**row, "action": "ORDEN YA ENVIADA PARA ESTA VELA"})
                continue

            if qty > 0:
                entry_i = self._entry_index(df, st)
                ex, why = should_exit(strat, df, i, entry_i)
                if ex:
                    o = self.broker.place_market_order(Order(sym, "SELL", qty, bar_date, why))
                    st.update(last_order_bar=bar_date, entry_signal_date=None)
                    report.append({**row, "action": f"SELL {qty} ({why}) -> {o.status}"})
                else:
                    report.append({**row, "action": "MANTENER"})
            else:
                st.pop("entry_signal_date", None)
                if strat.entry(df, i):
                    price = float(df["Close"].iloc[i])
                    n = self.broker.size_position(sym, equity, cash, price,
                                                  float(df["atr"].iloc[i]), self.cfg.risk)
                    if n > 0:
                        o = self.broker.place_market_order(Order(sym, "BUY", n, bar_date, "entry"))
                        cash -= self.broker.order_cost(sym, n, price)
                        st.update(last_order_bar=bar_date, entry_signal_date=bar_date)
                        report.append({**row, "action": f"BUY {n} -> {o.status}"})
                    else:
                        report.append({**row, "action": "SEÑAL DE COMPRA, TAMAÑO 0"})
                else:
                    report.append({**row, "action": "SIN SEÑAL"})
        self._save()
        return report

    @staticmethod
    def _entry_index(df: pd.DataFrame, st: dict) -> int:
        """Índice de la vela de entrada (la primera posterior a la señal de compra)."""
        sig = st.get("entry_signal_date")
        if not sig:
            log.warning("Posición sin fecha de entrada registrada; se permite la salida inmediata")
            return 0
        after = (df.index > pd.Timestamp(sig)).nonzero()[0]
        return int(after[0]) if len(after) else len(df) - 1


def _require_live(section: dict, name: str, confirm_live: bool):
    if not (section.get("live_trading_enabled") and confirm_live):
        raise PermissionError(
            f"Modo real bloqueado: requiere [{name}] live_trading_enabled = true en la "
            "configuración Y el flag --confirm-live en la línea de comandos."
        )


def build_broker(cfg: Config, *, confirm_live: bool = False):
    """Crea el bróker según el modo. Devuelve (broker, cliente_de_datos | None)."""
    cfg.validate()
    client = None
    if cfg.mode.startswith("schwab") or cfg.data_source == "schwab":
        from tis_trading.brokers.schwab import SchwabClient
        client = SchwabClient(token_path=cfg.schwab.get("token_path", "tokens/schwab_token.json"))

    if cfg.mode == "paper":
        return PaperBroker(Path(cfg.state_dir) / "paper_account.json",
                           cfg.initial_capital, cfg.cost_bps), client

    if cfg.mode.startswith("schwab"):
        from tis_trading.brokers.schwab import SchwabBroker
        live = cfg.mode == "schwab_live"
        if live:
            _require_live(cfg.schwab, "schwab", confirm_live)
        return SchwabBroker(client, cfg.schwab.get("account_number") or None, dry_run=not live,
                            journal_path=Path(cfg.state_dir) / "schwab_orders.jsonl"), client

    if cfg.mode.startswith("ibkr"):
        from tis_trading.brokers.ibkr import IbkrBroker
        live = cfg.mode == "ibkr_live"
        if live:
            _require_live(cfg.ibkr, "ibkr", confirm_live)
        c = cfg.ibkr
        return IbkrBroker(host=c.get("host", "127.0.0.1"), port=int(c.get("port", 7496 if live else 7497)),
                          client_id=int(c.get("client_id", 17)), account=c.get("account") or None,
                          paper=not live, order_type=c.get("order_type", "MOO")), client

    from tis_trading.brokers.mt5 import Mt5Broker
    live = cfg.mode == "mt5_live"
    if live:
        _require_live(cfg.mt5, "mt5", confirm_live)
    c = cfg.mt5
    return Mt5Broker(demo=not live, magic=int(c.get("magic", 20250202)),
                     deviation=int(c.get("deviation", 20)),
                     terminal_path=c.get("terminal_path") or None,
                     server=c.get("server") or None), client

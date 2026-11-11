"""Línea de comandos: python -m tis_trading <comando>."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from tis_trading import data as data_mod
from tis_trading.backtest import run_backtest
from tis_trading.engine import Config, Engine, build_broker
from tis_trading.strategies import STRATEGIES, get_strategy


def _print_table(rows: list[dict]):
    if rows:
        print(pd.DataFrame(rows).to_string(index=False))


def cmd_backtest(args, cfg: Config):
    df = data_mod.load(args.symbol, args.source or "csv", cfg.data_dir)
    if args.start:
        df = df[df.index >= pd.Timestamp(args.start) - pd.Timedelta(days=400)]
    names = list(STRATEGIES) if args.strategy == "all" else [args.strategy]
    rows = []
    for name in names:
        params = json.loads(args.params) if args.params else None
        res = run_backtest(df, get_strategy(name, params), symbol=args.symbol,
                           initial_capital=cfg.initial_capital, cost_bps=cfg.cost_bps, risk=cfg.risk)
        rows.append({"strategy": name, **res.metrics})
        if args.out:
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            res.trades_frame().to_csv(out / f"{args.symbol}_{name}_trades.csv", index=False)
            res.equity.to_csv(out / f"{args.symbol}_{name}_equity.csv")
    print(f"\nBacktest {args.symbol}  {df.index[0].date()} -> {df.index[-1].date()}  "
          f"(costes {cfg.cost_bps} bps/lado, riesgo modo={cfg.risk.mode})\n")
    _print_table(rows)


def cmd_download(args, cfg: Config):
    if args.source == "schwab":
        from tis_trading.brokers.schwab import SchwabClient
        df = SchwabClient().price_history_daily(args.symbol, years=args.years)
    else:
        df = data_mod.load_yfinance(args.symbol)
    path = Path(cfg.data_dir) / f"{args.symbol}_D1.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    print(f"Guardadas {len(df)} velas en {path}")


def cmd_auth(args, cfg: Config):
    from tis_trading.brokers.schwab import SchwabClient
    c = SchwabClient(token_path=cfg.schwab.get("token_path", "tokens/schwab_token.json"))
    print("1) Abre esta URL, inicia sesión en Schwab y autoriza tus cuentas:\n")
    print("   " + c.authorize_url() + "\n")
    print("2) Serás redirigido a tu Callback URL (la página puede dar error, es normal).")
    url = input("3) Pega aquí la URL completa a la que fuiste redirigido: ").strip()
    c.exchange_code(url)
    print(f"Token guardado en {c.token_path}. Cuentas vinculadas:")
    for a in c.account_numbers():
        print("  ****" + a["accountNumber"][-4:])


def cmd_run(args, cfg: Config):
    if args.mode:
        cfg.mode = args.mode
        cfg.validate()
    broker, client = build_broker(cfg, confirm_live=args.confirm_live)
    print(f"Modo: {cfg.mode.upper()} | datos: {cfg.data_source} | bróker: {broker.name}")
    report = Engine(cfg, broker, client).run_once()
    _print_table(report)


def cmd_status(args, cfg: Config):
    from tis_trading.brokers.paper import PaperBroker
    p = Path(cfg.state_dir) / "paper_account.json"
    if not p.exists():
        print("Aún no hay cuenta demo. Ejecuta: python -m tis_trading run")
        return
    st = PaperBroker(p).state
    print(f"Efectivo: {st['cash']:.2f}  (inicial {st['initial_cash']:.2f})")
    print("Posiciones:", json.dumps(st["positions"], indent=2))
    print("Órdenes pendientes:", json.dumps(st["pending"], indent=2))
    closed = [f for f in st["fills"] if f.get("side") == "SELL" and "pnl" in f]
    if closed:
        print(f"Operaciones cerradas: {len(closed)}  P&L total: {sum(f['pnl'] for f in closed):.2f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tis_trading", description="Estrategias TIS + Charles Schwab")
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backtest", help="Backtest de una estrategia (o 'all') sobre CSV")
    b.add_argument("symbol")
    b.add_argument("--strategy", default="tis_rsi2", choices=[*STRATEGIES, "all"])
    b.add_argument("--params", help='JSON con parámetros, p. ej. \'{"rsi_entry": 10}\'')
    b.add_argument("--source", choices=["csv", "yfinance"])
    b.add_argument("--start", help="Fecha inicial YYYY-MM-DD")
    b.add_argument("--out", help="Carpeta para guardar trades y equity")

    d = sub.add_parser("download", help="Descarga velas diarias a data/<SYMBOL>_D1.csv")
    d.add_argument("symbol")
    d.add_argument("--source", default="yfinance", choices=["yfinance", "schwab"])
    d.add_argument("--years", type=int, default=20)

    sub.add_parser("auth", help="Autoriza la app de Schwab (OAuth) y guarda el token")

    r = sub.add_parser("run", help="Ejecuta el ciclo diario (señales + órdenes)")
    r.add_argument("--mode", choices=["paper", "schwab_dry_run", "live"])
    r.add_argument("--confirm-live", action="store_true", help="Necesario para enviar órdenes reales")

    sub.add_parser("status", help="Estado de la cuenta demo")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.load(args.config)
    {"backtest": cmd_backtest, "download": cmd_download, "auth": cmd_auth,
     "run": cmd_run, "status": cmd_status}[args.cmd](args, cfg)
    return 0

"""Cliente mínimo de la API de Charles Schwab (Trader API - Individual).

Requisitos:
  1. Crear una app en https://developer.schwab.com con los productos
     "Accounts and Trading Production" y "Market Data Production".
  2. Configurar la Callback URL (p. ej. https://127.0.0.1:8182).
  3. Exportar SCHWAB_APP_KEY, SCHWAB_APP_SECRET y SCHWAB_CALLBACK_URL.
  4. Ejecutar ``python -m tis_trading auth`` una vez (el refresh token dura 7 días).

Notas de seguridad:
  * Los tokens se guardan en ``tokens/schwab_token.json`` (ignorado por git).
  * ``SchwabBroker`` por defecto está en modo ``dry_run``: construye la orden y
    la registra, pero NO la envía. Enviar órdenes reales requiere
    ``dry_run=False`` (ver ``live_trading_enabled`` en la configuración).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import pandas as pd
import requests

from tis_trading.brokers.base import Broker, Order

log = logging.getLogger(__name__)

API = "https://api.schwabapi.com"
AUTH_URL = f"{API}/v1/oauth/authorize"
TOKEN_URL = f"{API}/v1/oauth/token"
REFRESH_TOKEN_LIFETIME = 7 * 24 * 3600


class SchwabAuthError(RuntimeError):
    pass


class SchwabClient:
    def __init__(self, app_key: str | None = None, app_secret: str | None = None,
                 callback_url: str | None = None,
                 token_path: str | Path = "tokens/schwab_token.json",
                 session: requests.Session | None = None):
        self.app_key = app_key or os.environ.get("SCHWAB_APP_KEY", "")
        self.app_secret = app_secret or os.environ.get("SCHWAB_APP_SECRET", "")
        self.callback_url = callback_url or os.environ.get("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182")
        self.token_path = Path(token_path)
        self.http = session or requests.Session()
        self._token: dict | None = None

    # ------------------------------------------------------------------ OAuth
    def authorize_url(self) -> str:
        return f"{AUTH_URL}?{urlencode({'client_id': self.app_key, 'redirect_uri': self.callback_url})}"

    def _basic_auth(self) -> str:
        if not self.app_key or not self.app_secret:
            raise SchwabAuthError("Faltan SCHWAB_APP_KEY / SCHWAB_APP_SECRET")
        return base64.b64encode(f"{self.app_key}:{self.app_secret}".encode()).decode()

    def _token_request(self, data: dict) -> dict:
        r = self.http.post(TOKEN_URL, data=data, timeout=30, headers={
            "Authorization": f"Basic {self._basic_auth()}",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        if r.status_code != 200:
            raise SchwabAuthError(f"Error obteniendo token ({r.status_code}): {r.text[:300]}")
        return r.json()

    def exchange_code(self, redirected_url: str) -> dict:
        """Intercambia el ``code`` de la URL de redirección por tokens."""
        qs = parse_qs(urlparse(redirected_url).query)
        if "code" not in qs:
            raise SchwabAuthError("La URL pegada no contiene el parámetro 'code'")
        tok = self._token_request({"grant_type": "authorization_code", "code": qs["code"][0],
                                   "redirect_uri": self.callback_url})
        now = time.time()
        tok["expires_at"] = now + int(tok.get("expires_in", 1800)) - 60
        tok["refresh_expires_at"] = now + REFRESH_TOKEN_LIFETIME - 3600
        self._save_token(tok)
        return tok

    def _save_token(self, tok: dict):
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(tok, indent=2))
        try:
            os.chmod(self.token_path, 0o600)
        except OSError:
            pass
        self._token = tok

    def _load_token(self) -> dict:
        if self._token is None:
            if not self.token_path.exists():
                raise SchwabAuthError("No hay token. Ejecuta: python -m tis_trading auth")
            self._token = json.loads(self.token_path.read_text())
        return self._token

    def access_token(self) -> str:
        tok = self._load_token()
        if time.time() < tok.get("expires_at", 0):
            return tok["access_token"]
        if time.time() > tok.get("refresh_expires_at", 0):
            raise SchwabAuthError("El refresh token caducó (7 días). Ejecuta de nuevo: python -m tis_trading auth")
        new = self._token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        tok.update(new)
        tok["expires_at"] = time.time() + int(new.get("expires_in", 1800)) - 60
        self._save_token(tok)
        return tok["access_token"]

    # ------------------------------------------------------------------- HTTP
    def request(self, method: str, path: str, **kw) -> requests.Response:
        headers = kw.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.access_token()}"
        r = self.http.request(method, f"{API}{path}", headers=headers, timeout=30, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"Schwab {method} {path} -> {r.status_code}: {r.text[:500]}")
        return r

    # ------------------------------------------------------------ Market data
    def price_history_daily(self, symbol: str, years: int = 2) -> pd.DataFrame:
        r = self.request("GET", "/marketdata/v1/pricehistory", params={
            "symbol": symbol, "periodType": "year", "period": years,
            "frequencyType": "daily", "frequency": 1,
        })
        candles = r.json().get("candles", [])
        if not candles:
            raise RuntimeError(f"Schwab no devolvió velas para {symbol}")
        df = pd.DataFrame(candles)
        idx = (pd.to_datetime(df["datetime"], unit="ms", utc=True)
               .dt.tz_convert("America/New_York").dt.normalize().dt.tz_localize(None))
        out = pd.DataFrame({"Open": df["open"], "High": df["high"], "Low": df["low"],
                            "Close": df["close"], "Volume": df["volume"]}).astype(float)
        out.index = pd.DatetimeIndex(idx, name="Date")
        return out[~out.index.duplicated(keep="last")].sort_index()

    def quotes(self, symbols: list[str]) -> dict:
        return self.request("GET", "/marketdata/v1/quotes",
                            params={"symbols": ",".join(symbols)}).json()

    # --------------------------------------------------------------- Accounts
    def account_numbers(self) -> list[dict]:
        return self.request("GET", "/trader/v1/accounts/accountNumbers").json()

    def account(self, account_hash: str) -> dict:
        return self.request("GET", f"/trader/v1/accounts/{account_hash}",
                            params={"fields": "positions"}).json()

    def place_order(self, account_hash: str, payload: dict) -> str | None:
        r = self.request("POST", f"/trader/v1/accounts/{account_hash}/orders", json=payload)
        loc = r.headers.get("Location", "")
        return loc.rstrip("/").split("/")[-1] if loc else None


def market_order_payload(symbol: str, side: str, quantity: int) -> dict:
    """Orden a mercado DAY. Enviada fuera de horario queda en cola para la apertura."""
    return {
        "orderType": "MARKET",
        "session": "NORMAL",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [{
            "instruction": side,
            "quantity": int(quantity),
            "instrument": {"symbol": symbol, "assetType": "EQUITY"},
        }],
    }


class SchwabBroker(Broker):
    name = "schwab"

    def __init__(self, client: SchwabClient, account_number: str | None = None,
                 dry_run: bool = True, journal_path: str | Path = "state/schwab_orders.jsonl"):
        self.client = client
        self.dry_run = dry_run
        self.journal = Path(journal_path)
        accounts = client.account_numbers()
        if not accounts:
            raise RuntimeError("La API no devolvió cuentas")
        if account_number:
            match = [a for a in accounts if a["accountNumber"] == str(account_number)]
            if not match:
                raise RuntimeError("La cuenta indicada no está vinculada a esta app")
            self.account_hash = match[0]["hashValue"]
        else:
            self.account_hash = accounts[0]["hashValue"]
        self._acct: dict | None = None

    def _account(self) -> dict:
        if self._acct is None:
            self._acct = self.client.account(self.account_hash)["securitiesAccount"]
        return self._acct

    def price_history_daily(self, symbol: str) -> pd.DataFrame:
        return self.client.price_history_daily(symbol)

    def get_cash(self) -> float:
        bal = self._account().get("currentBalances", {})
        return float(bal.get("cashAvailableForTrading", bal.get("cashBalance", 0.0)))

    def get_equity(self, prices: dict[str, float]) -> float:
        return float(self._account().get("currentBalances", {}).get("liquidationValue", 0.0))

    def get_positions(self) -> dict[str, int]:
        out = {}
        for p in self._account().get("positions", []):
            sym = p.get("instrument", {}).get("symbol")
            qty = int(float(p.get("longQuantity", 0)))
            if sym and qty > 0:
                out[sym] = qty
        return out

    def place_market_order(self, order: Order) -> Order:
        payload = market_order_payload(order.symbol, order.side, order.quantity)
        if self.dry_run:
            order.status = "DRY_RUN"
            log.warning("[DRY-RUN] Orden NO enviada a Schwab: %s", json.dumps(payload))
        else:
            order.order_id = self.client.place_order(self.account_hash, payload)
            order.status = "SUBMITTED"
            log.warning("Orden ENVIADA a Schwab id=%s: %s", order.order_id, json.dumps(payload))
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        with self.journal.open("a") as f:
            f.write(json.dumps({**asdict(order), "payload": payload, "ts": time.time()}) + "\n")
        self._acct = None
        return order

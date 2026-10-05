"""tastytrade Open API client (retail default: ES/MES futures options under
SPAN for the index leg, OCC equity options for the singles).

Auth: OAuth2 refresh-token grant -> 15-minute access tokens.
Env:  TT_CLIENT_SECRET, TT_REFRESH_TOKEN, TT_ACCOUNT, TT_ENV=sandbox|prod
Sandbox (api.cert.tastyworks.com) serves no market data; the usual pattern is
quotes from production credentials and orders to the sandbox (two clients).

Order safety (per tastytrade docs): dry-run first, unique external-identifier
on every submit, look the identifier up before re-submitting after a timeout.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import time
from urllib.parse import quote

import requests

from .broker import Broker, Position
from .orders import Quote, Ticket
from .symbols import occ, parse_occ

log = logging.getLogger(__name__)

BASE = {"sandbox": "https://api.cert.tastyworks.com", "prod": "https://api.tastyworks.com"}
UA = "dispersion-for-retail/0.1"


class TastyClient:
    def __init__(self, env: str | None = None, client_secret: str | None = None, refresh_token: str | None = None,
                 session: requests.Session | None = None):
        self.env = env or os.environ.get("TT_ENV", "sandbox")
        self.base = BASE[self.env]
        self.client_secret = client_secret or os.environ.get("TT_CLIENT_SECRET")
        self.refresh_token = refresh_token or os.environ.get("TT_REFRESH_TOKEN")
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"})
        self._token, self._token_exp = None, 0.0

    # ----------------------------------------------------------------- auth
    def token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        r = self.s.post(f"{self.base}/oauth/token", json={
            "grant_type": "refresh_token", "refresh_token": self.refresh_token, "client_secret": self.client_secret},
            timeout=30)
        r.raise_for_status()
        j = r.json()
        self._token = j["access_token"]
        self._token_exp = time.time() + float(j.get("expires_in", 900))
        return self._token

    def _req(self, method: str, path: str, retries=3, **kw):
        for attempt in range(retries):
            r = self.s.request(method, f"{self.base}{path}", headers={"Authorization": f"Bearer {self.token()}"},
                               timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 401 and attempt == 0:
                self._token = None
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
            return r.json() if r.text else {}
        raise RuntimeError(f"{method} {path}: rate limited")

    def get(self, path, **params):
        return self._req("GET", path, params=params or None)

    def post(self, path, body):
        return self._req("POST", path, json=body)

    def put(self, path, body):
        return self._req("PUT", path, json=body)

    def delete(self, path):
        return self._req("DELETE", path)

    # ----------------------------------------------------------- discovery
    def accounts(self) -> list[str]:
        j = self.get("/customers/me/accounts")
        return [a["account"]["account-number"] for a in j["data"]["items"]]


class TastyBroker(Broker):
    name = "tastytrade"

    def __init__(self, orders: TastyClient, account: str | None = None, quotes: TastyClient | None = None):
        self.c = orders
        self.q = quotes or orders        # sandbox has no market data: pass a prod client for quotes
        self.account = account or os.environ.get("TT_ACCOUNT") or self.c.accounts()[0]
        self._chain_cache: dict = {}

    # ---------------------------------------------------------- balances
    def equity(self) -> float:
        b = self.c.get(f"/accounts/{self.account}/balances")["data"]
        return float(b["net-liquidating-value"])

    def margin_requirement(self) -> dict:
        b = self.c.get(f"/accounts/{self.account}/balances")["data"]
        return {"maintenance": float(b.get("maintenance-requirement", 0)),
                "buying_power": float(b.get("derivative-buying-power", b.get("equity-buying-power", 0))),
                "futures_margin": float(b.get("futures-margin-requirement", 0))}

    def positions(self) -> list[Position]:
        out = []
        for p in self.c.get(f"/accounts/{self.account}/positions")["data"]["items"]:
            qty = int(float(p["quantity"])) * (-1 if p["quantity-direction"] == "Short" else 1)
            pos = Position(p["symbol"], p["instrument-type"], qty, float(p.get("multiplier", 1)),
                           p.get("underlying-symbol", p["symbol"]))
            if p["instrument-type"] == "Equity Option":
                o = parse_occ(p["symbol"])
                pos.strike, pos.expiry, pos.is_call = o["strike"], o["expiry"], o["call"]
            elif p["instrument-type"] == "Future Option":
                tail = p["symbol"].split()[-1]
                pos.expiry = dt.datetime.strptime(tail[:6], "%y%m%d").date().isoformat()
                pos.is_call = tail[6] == "C"
                pos.strike = float(tail[7:])
            pos.entry_price = float(p.get("average-open-price", 0))
            out.append(pos)
        return out

    # ------------------------------------------------------------- quotes
    def quote(self, symbol: str, instrument_type: str) -> Quote:
        key = {"Equity": "equity", "Equity Option": "equity-option", "Future": "future",
               "Future Option": "future-option", "Index": "index"}[instrument_type]
        j = self.q.get("/market-data/by-type", **{key: symbol})
        it = j["data"]["items"][0]
        return Quote(float(it["bid"]), float(it["ask"]))

    def quotes(self, symbols: list[str], instrument_type: str) -> dict[str, Quote]:
        key = {"Equity": "equity", "Equity Option": "equity-option", "Future": "future",
               "Future Option": "future-option"}[instrument_type]
        out = {}
        for i in range(0, len(symbols), 100):
            j = self.q.get("/market-data/by-type", **{key: ",".join(symbols[i:i + 100])})
            for it in j["data"]["items"]:
                out[it["symbol"]] = Quote(float(it["bid"]), float(it["ask"]))
        return out

    # ------------------------------------------------------------- chains
    def expirations(self, underlying: str) -> list[dict]:
        j = self.c.get(f"/option-chains/{quote(underlying, safe='')}/nested")
        exps = []
        for item in j["data"]["items"]:
            for e in item["expirations"]:
                exps.append({"expiry": e["expiration-date"], "dte": int(e["days-to-expiration"]),
                             "type": e.get("expiration-type"),
                             "strikes": [float(s["strike-price"]) for s in e["strikes"]]})
        return exps

    def monthly_expiry(self, underlying: str, target_dte=30) -> dict:
        """Regular monthly expiry nearest ~30 days out (same expiry for every leg)."""
        exps = self.expirations(underlying)
        monthly = [e for e in exps if (e["type"] or "Regular") == "Regular"] or exps
        return min(monthly, key=lambda e: abs(e["dte"] - target_dte))

    def option_chain(self, underlying: str, expiry: str) -> list[float]:
        for e in self.expirations(underlying):
            if e["expiry"] == expiry:
                return e["strikes"]
        return []

    def option_symbol(self, underlying, expiry, strike, call=True):
        return occ(underlying, expiry, strike, call)

    def futures_option_chain(self, product: str = "ES") -> list[dict]:
        """Nested futures option chain: one entry per expiration with its
        strikes and the symbols needed to build './ESZ5 EW3Z5 251219C6000'."""
        j = self.c.get(f"/futures-option-chains/{product}/nested")
        out = []
        data = j["data"]
        chains = data.get("option-chains", []) or data.get("items", [])
        for ch in chains:
            for e in ch.get("expirations", []):
                out.append({"expiry": e["expiration-date"], "dte": int(e["days-to-expiration"]),
                            "future": e.get("underlying-symbol"), "root": e.get("option-root-symbol"),
                            "contract": e.get("option-contract-symbol"), "type": e.get("expiration-type"),
                            "strikes": [float(s["strike-price"]) for s in e["strikes"]],
                            "calls": {float(s["strike-price"]): s["call"] for s in e["strikes"]}})
        return out

    # ------------------------------------------------------------- orders
    @staticmethod
    def _body(ticket: Ticket, price: float, tif="Day") -> dict:
        return {"order-type": "Limit", "time-in-force": tif, "price": f"{abs(price):.2f}",
                "price-effect": ticket.price_effect, "external-identifier": ticket.external_id,
                "source": "dispersion-for-retail",
                "legs": [{"instrument-type": l.instrument_type, "symbol": l.symbol, "action": l.action,
                          "quantity": l.quantity} for l in ticket.legs]}

    def dry_run(self, ticket: Ticket, price: float) -> dict:
        j = self.c.post(f"/accounts/{self.account}/orders/dry-run", self._body(ticket, price))["data"]
        bp = j.get("buying-power-effect", {})
        warn = [w.get("message", str(w)) for w in j.get("warnings", [])]
        return {"ok": not warn, "warnings": warn,
                "bp_change": float(bp.get("change-in-buying-power", 0)) * (-1 if bp.get("change-in-buying-power-effect") == "Debit" else 1),
                "margin_change": float(bp.get("change-in-margin-requirement", 0)),
                "fees": float(j.get("fee-calculation", {}).get("total-fees", 0))}

    def margin_dry_run(self, tickets: list[Ticket], prices: list[float]) -> dict:
        """POST /margin/accounts/{acct}/dry-run with every planned order: the
        projected post-roll margin used for the 60% cap."""
        body = {"orders": [self._body(t, p) for t, p in zip(tickets, prices)]}
        j = self.c.post(f"/margin/accounts/{self.account}/dry-run", body)["data"]
        return {"margin_after": float(j.get("new-margin-requirement", j.get("margin-requirement", 0))),
                "margin_change": float(j.get("change-in-margin-requirement", 0)),
                "bp_after": float(j.get("new-buying-power", 0)), "raw": j}

    def submit(self, ticket: Ticket, price: float) -> str:
        try:
            j = self.c.post(f"/accounts/{self.account}/orders", self._body(ticket, price))
        except (requests.Timeout, requests.ConnectionError):
            # uncertain outcome: look for our external id before resubmitting
            for o in self.c.get(f"/accounts/{self.account}/orders/live")["data"]["items"]:
                if o.get("external-identifier") == ticket.external_id:
                    return str(o["id"])
            raise
        return str(j["data"]["order"]["id"])

    def status(self, order_id: str) -> str:
        return self.c.get(f"/accounts/{self.account}/orders/{order_id}")["data"]["status"]

    def replace(self, order_id: str, price: float) -> str:
        o = self.c.get(f"/accounts/{self.account}/orders/{order_id}")["data"]
        body = {"order-type": "Limit", "time-in-force": o["time-in-force"], "price": f"{abs(price):.2f}",
                "price-effect": o["price-effect"]}
        j = self.c.put(f"/accounts/{self.account}/orders/{order_id}", body)
        return str(j["data"].get("id", order_id))

    def cancel(self, order_id: str) -> None:
        self.c.delete(f"/accounts/{self.account}/orders/{order_id}")

    def trading_status(self) -> dict:
        return self.c.get(f"/accounts/{self.account}/trading-status")["data"]

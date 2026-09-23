"""Broker execution client (Alpaca).

Uses the plain REST API via `requests` (no extra SDK needed). Paper trading
is the default; live trading only activates when explicitly configured AND
guardrails acknowledge it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import requests


@dataclass
class OrderResult:
    symbol: str
    side: str
    qty: float
    order_id: str | None
    status: str
    raw: dict[str, Any] = field(default_factory=dict)


class BrokerClient:
    """Thin Alpaca REST client. `paper=True` routes to the paper endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        paper: bool = True,
    ):
        self.api_key = api_key or os.getenv("ALPACA_API_KEY", "")
        self.api_secret = api_secret or os.getenv("ALPACA_SECRET_KEY", "")
        env_base = os.getenv("ALPACA_BASE_URL", "").strip()
        if env_base:
            base = env_base
        else:
            base = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        self.paper = paper
        self.base_url = base.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.api_secret,
                "Content-Type": "application/json",
            }
        )

    # ---------- account ----------
    def get_account(self) -> dict[str, Any]:
        return self._get("/v2/account")

    def get_positions(self) -> list[dict[str, Any]]:
        return self._get("/v2/positions")

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        resp = self.session.get(f"{self.base_url}/v2/positions/{symbol}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    # ---------- orders ----------
    def submit_market_order(self, symbol: str, qty: float, side: str) -> OrderResult:
        """side: 'buy' or 'sell'. Fractional qty allowed."""
        payload = {"symbol": symbol, "qty": str(qty), "side": side, "type": "market", "time_in_force": "day"}
        data = self._post("/v2/orders", payload)
        return OrderResult(
            symbol=symbol,
            side=side,
            qty=qty,
            order_id=data.get("id"),
            status=data.get("status", "unknown"),
            raw=data,
        )

    def close_position(self, symbol: str) -> OrderResult:
        data = self._post(f"/v2/positions/{symbol}/close", None)
        return OrderResult(
            symbol=symbol,
            side="sell",
            qty=float(data.get("qty", 0) or 0),
            order_id=data.get("id"),
            status=data.get("status", "submitted"),
            raw=data,
        )

    # ---------- internals ----------
    def _get(self, path: str):
        resp = self.session.get(f"{self.base_url}{path}")
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload):
        resp = self.session.post(f"{self.base_url}{path}", json=payload)
        resp.raise_for_status()
        return resp.json()

"""Nexar (Octopart) GraphQL API client — best for multi-vendor normalized data.

Credentials via env vars:
  NEXAR_CLIENT_ID
  NEXAR_CLIENT_SECRET

Register at: https://nexar.com/api
GraphQL explorer: https://api.nexar.com/graphql
"""

import os
import time
from .base import PartResult, RateLimiter, http_post, http_post_form, to_inr

_GQL   = "https://api.nexar.com/graphql"
_TOKEN = "https://identity.nexar.com/connect/token"

_SEARCH_QUERY = """
query PartSearch($query: String!, $limit: Int!) {
  supSearch(q: $query, limit: $limit, country: "US", currency: "USD") {
    hits
    results {
      part {
        mpn
        manufacturer { name }
        shortDescription
        category { name }
        lifecycleStatus
        bestDatasheet { url }
        sellers(includeBrokers: false) {
          company { name }
          offers {
            sku
            inventoryLevel
            prices { quantity price currency }
          }
        }
      }
    }
  }
}
"""


class OctopartClient:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        self._id     = client_id     or os.environ.get("NEXAR_CLIENT_ID", "")
        self._secret = client_secret or os.environ.get("NEXAR_CLIENT_SECRET", "")
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._rl = RateLimiter(calls_per_second=2)

    @property
    def available(self) -> bool:
        return bool(self._id and self._secret)

    def _auth(self) -> str:
        if self._token and time.monotonic() < self._token_expiry - 30:
            return self._token
        resp = http_post_form(_TOKEN, {
            "grant_type":    "client_credentials",
            "client_id":     self._id,
            "client_secret": self._secret,
        })
        self._token = resp["access_token"]
        self._token_expiry = time.monotonic() + resp.get("expires_in", 1800)
        return self._token

    def search(self, keyword: str, limit: int = 10) -> list[PartResult]:
        if not self.available:
            return []
        self._rl.wait()
        resp = http_post(
            _GQL,
            body={"query": _SEARCH_QUERY, "variables": {"query": keyword, "limit": limit}},
            headers={"Authorization": f"Bearer {self._auth()}"},
        )
        results = (resp.get("data") or {}).get("supSearch", {}).get("results") or []
        return [_parse_octo(r["part"]) for r in results if "part" in r]


def _parse_octo(p: dict) -> PartResult:
    stock = 0
    price = None
    vendor_sku = ""
    vendor_name = "octopart"
    price_breaks = []

    for seller in p.get("sellers") or []:
        for offer in seller.get("offers") or []:
            stock += offer.get("inventoryLevel") or 0
            for tier in offer.get("prices") or []:
                if tier.get("currency") != "USD":
                    continue
                try:
                    raw_price = float(tier["price"])
                    price_breaks.append({"quantity": int(tier.get("quantity", 1)),
                                         "unit_price": raw_price, "currency": "USD"})
                    if price is None: price = to_inr(raw_price, "USD")
                except (TypeError, ValueError):
                    pass
            if not vendor_sku:
                vendor_sku = offer.get("sku", "")
                vendor_name = (seller.get("company") or {}).get("name", "octopart")

    ds = (p.get("bestDatasheet") or {}).get("url")

    return PartResult(
        mpn=p.get("mpn", ""),
        manufacturer=(p.get("manufacturer") or {}).get("name", ""),
        description=p.get("shortDescription", ""),
        vendor=vendor_name,
        vendor_sku=vendor_sku,
        datasheet_url=ds,
        stock=stock,
        price_inr=price,
        category=(p.get("category") or {}).get("name"),
        lifecycle=p.get("lifecycleStatus"),
        price_breaks=price_breaks,
        source_url=_GQL,
    )

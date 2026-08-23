"""Mouser Search API v1 client.

Credentials via env vars:
  MOUSER_API_KEY

Register at: https://www.mouser.com/api/
API docs:    https://api.mouser.com/api/docs/index
"""

import os
import urllib.parse
from .base import PartResult, RateLimiter, http_post, to_inr

_BASE = "https://api.mouser.com"


class MouserClient:
    def __init__(self, api_key: str | None = None):
        self._key = api_key or os.environ.get("MOUSER_API_KEY", "")
        self._rl  = RateLimiter(calls_per_second=0.5)  # 30/min limit

    @property
    def available(self) -> bool:
        return bool(self._key)

    def search(self, keyword: str, limit: int = 10) -> list[PartResult]:
        if not self.available:
            return []
        self._rl.wait()
        params = urllib.parse.urlencode({"apiKey": self._key})
        data = http_post(
            f"{_BASE}/api/v1.0/search/keyword?{params}",
            body={
                "SearchByKeywordRequest": {
                    "keyword":        keyword,
                    "records":        limit,
                    "startingRecord": 0,
                    "searchOptions":  "InStock",   # bias toward orderable parts
                }
            },
        )
        parts = (data.get("SearchResults") or {}).get("Parts") or []
        return [_parse_mouser(p) for p in parts]

    def part(self, mpn: str) -> PartResult | None:
        if not self.available:
            return None
        self._rl.wait()
        params = urllib.parse.urlencode({"apiKey": self._key})
        data = http_post(
            f"{_BASE}/api/v1.0/search/partnumber?{params}",
            body={
                "SearchByPartNumberRequest": {
                    "mouserPartNumber": mpn,
                    "partSearchOptions": "string",
                }
            },
        )
        parts = (data.get("SearchResults") or {}).get("Parts") or []
        return _parse_mouser(parts[0]) if parts else None


def _parse_mouser(p: dict) -> PartResult:
    stock = None
    raw_qty = p.get("AvailabilityInStock", "")
    if isinstance(raw_qty, int):
        stock = raw_qty
    elif isinstance(raw_qty, str):
        raw_qty = raw_qty.replace(",", "").strip()
        if raw_qty.isdigit():
            stock = int(raw_qty)

    price = None
    price_breaks = []
    for tier in p.get("PriceBreaks") or []:
        # Mouser returns prices in the account's locale currency (here INR);
        # normalise to INR for consistent display + comparison.
        currency = tier.get("Currency", "INR")
        try:
            raw = tier.get("Price", "")
            cleaned = "".join(ch for ch in str(raw) if ch.isdigit() or ch in ".-")
            price_breaks.append({"quantity": int(tier.get("Quantity", 1)),
                                 "unit_price": float(cleaned), "currency": currency})
        except (TypeError, ValueError):
            continue
    if price_breaks:
        first = min(price_breaks, key=lambda x: x["quantity"])
        price = to_inr(first["unit_price"], first["currency"])

    return PartResult(
        mpn=p.get("ManufacturerPartNumber", ""),
        manufacturer=p.get("Manufacturer", ""),
        description=p.get("Description", ""),
        vendor="mouser",
        vendor_sku=p.get("MouserPartNumber", ""),
        datasheet_url=p.get("DataSheetUrl"),
        stock=stock,
        price_inr=price,
        category=p.get("Category"),
        lifecycle=p.get("LifecycleStatus"),
        price_breaks=price_breaks,
        source_url=p.get("ProductDetailUrl") or None,
    )

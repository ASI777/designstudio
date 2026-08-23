"""Base vendor client: rate limiting, retries, shared HTTP."""

import json
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any


# All vendor prices are normalised to INR (the display currency) so DigiKey (USD)
# and Mouser (account-locale) compare apples-to-apples. Rates are coarse — refresh
# if precise costing matters.
_FX_TO_INR = {"INR": 1.0, "USD": 83.0, "EUR": 90.0, "GBP": 105.0,
              "CAD": 61.0, "JPY": 0.53, "CNY": 11.5, "AUD": 55.0}
FX_PROFILE_ID = "designstudio-static-fx-2026-07-12"


def normalize_mpn(value: str) -> str:
    """Canonical comparison key; does not collapse package/tape suffixes."""
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def exact_mpn_match(expected: str, actual: str) -> bool:
    return bool(normalize_mpn(expected)) and normalize_mpn(expected) == normalize_mpn(actual)


def to_inr(value, currency: str = "USD") -> float | None:
    """Normalise a price (numeric or string with a currency symbol) to INR."""
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = "".join(ch for ch in value if ch.isdigit() or ch == ".")
        if not cleaned:
            return None
        value = float(cleaned)
    rate = _FX_TO_INR.get((currency or "USD").upper())
    return round(float(value) * rate, 2) if rate is not None else None


@dataclass
class PartResult:
    mpn: str                    # Manufacturer Part Number
    manufacturer: str
    description: str
    vendor: str                 # "digikey" | "mouser" | "octopart"
    vendor_sku: str             # Vendor's own catalog number
    datasheet_url: str | None
    stock: int | None           # Units in stock (None = unknown)
    price_inr: float | None     # Unit price at qty 1, in INR (₹)
    category: str | None
    lifecycle: str | None       # "active" | "nrnd" | "eol"
    extra: dict = field(default_factory=dict)
    price_breaks: list[dict] = field(default_factory=list)
    retrieved_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_url: str | None = None

    def price_for_quantity(self, quantity: int) -> dict | None:
        """Highest applicable break, preserving original currency and FX evidence."""
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        tiers = [t for t in self.price_breaks if int(t.get("quantity", 0)) <= quantity]
        if tiers:
            tier = max(tiers, key=lambda t: int(t.get("quantity", 0)))
            value = float(tier["unit_price"])
            currency = str(tier.get("currency", "USD")).upper()
            return {"quantity": quantity, "break_quantity": int(tier["quantity"]),
                    "unit_price": value, "currency": currency,
                    "price_inr_estimate": to_inr(value, currency),
                    "fx_profile": FX_PROFILE_ID}
        if self.price_inr is not None:
            return {"quantity": quantity, "break_quantity": 1,
                    "unit_price": self.price_inr, "currency": "INR_ESTIMATE",
                    "price_inr_estimate": self.price_inr, "fx_profile": FX_PROFILE_ID}
        return None


class RateLimiter:
    def __init__(self, calls_per_second: float):
        self._interval = 1.0 / calls_per_second
        self._last = 0.0

    def wait(self):
        now = time.monotonic()
        gap = self._last + self._interval - now
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()


def _request_json(req: urllib.request.Request, timeout: int, attempts: int = 3) -> dict | list:
    last_error = None
    parsed_url = urllib.parse.urlsplit(req.full_url)
    safe_url = urllib.parse.urlunsplit((parsed_url.scheme, parsed_url.netloc, parsed_url.path, "", ""))
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(10 * 1024 * 1024 + 1)
                if len(raw) > 10 * 1024 * 1024:
                    raise RuntimeError("vendor response exceeds 10 MiB safety limit")
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 429 and not 500 <= exc.code < 600:
                raise RuntimeError(f"vendor request to {safe_url} returned HTTP {exc.code}") from None
            retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
            try:
                delay = min(10.0, max(0.0, float(retry_after)))
            except ValueError:
                delay = min(10.0, 0.5 * (2 ** attempt))
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            time.sleep(min(5.0, 0.5 * (2 ** attempt)))
    kind = type(last_error).__name__ if last_error else "unknown error"
    raise RuntimeError(f"vendor request to {safe_url} failed after {attempts} attempts ({kind})")


def http_get(url: str, headers: dict | None = None,
             timeout: int = 15) -> dict | list:
    return _request_json(urllib.request.Request(url, headers=headers or {}), timeout)


def http_post(url: str, body: dict, headers: dict | None = None,
              timeout: int = 15) -> dict | list:
    data = json.dumps(body).encode()
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    return _request_json(req, timeout)


def http_post_form(url: str, fields: dict, timeout: int = 15) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    return _request_json(req, timeout)

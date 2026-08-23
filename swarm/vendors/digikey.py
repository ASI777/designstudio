"""DigiKey Product Information API v4 client.

Auth flow:
  1. First run: user visits the auth URL printed to stderr, approves, pastes the
     ?code= value into DIGIKEY_AUTH_CODE env var (or calls DigikeyClient.exchange(code)).
  2. Tokens are stored in DIGIKEY_TOKEN_FILE (default ~/.config/product_design/dk_tokens.json).
  3. Subsequent runs use the refresh_token automatically (~90-day lifetime).

Env vars:
  DIGIKEY_CLIENT_ID      — required
  DIGIKEY_CLIENT_SECRET  — required
  DIGIKEY_SANDBOX        — "true" to hit sandbox-api.digikey.com (default false)
  DIGIKEY_AUTH_CODE      — one-time: exchange this code then clear it
  DIGIKEY_TOKEN_FILE     — override token storage path
"""

import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

from .base import PartResult, RateLimiter, http_get, http_post, http_post_form, to_inr

_BASE    = "https://api.digikey.com"
_SANDBOX = "https://sandbox-api.digikey.com"
_DEFAULT_TOKEN_FILE = Path.home() / ".config" / "product_design" / "dk_tokens.json"


class DigikeyClient:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None,
                 sandbox: bool | None = None):
        self._id     = client_id     or os.environ.get("DIGIKEY_CLIENT_ID", "")
        self._secret = client_secret or os.environ.get("DIGIKEY_CLIENT_SECRET", "")
        if sandbox is None:
            sandbox = os.environ.get("DIGIKEY_SANDBOX", "").lower() in ("1", "true", "yes")
        self._base = _SANDBOX if sandbox else _BASE
        self._token_file = Path(os.environ.get("DIGIKEY_TOKEN_FILE", str(_DEFAULT_TOKEN_FILE)))

        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expiry: float = 0.0
        self._rl = RateLimiter(calls_per_second=4)

        # Production API supports client_credentials directly — no browser flow needed
        # Only load stored tokens (from prior auth-code flow) if they exist
        code = os.environ.get("DIGIKEY_AUTH_CODE", "").strip()
        if code:
            self.exchange(code)
        else:
            self._load_tokens()
            # If no stored token, we'll get one via client_credentials in _auth()

    @property
    def available(self) -> bool:
        return bool(self._id and self._secret)

    def auth_url(self) -> str:
        params = urllib.parse.urlencode({
            "response_type": "code",
            "client_id": self._id,
            "redirect_uri": "https://localhost",
            "scope": "openid",
        })
        return f"{self._base}/v1/oauth2/authorize?{params}"

    def exchange(self, code: str) -> None:
        """Exchange a one-time auth code for access + refresh tokens and persist them."""
        resp = http_post_form(
            f"{self._base}/v1/oauth2/token",
            {
                "grant_type":    "authorization_code",
                "code":          code,
                "client_id":     self._id,
                "client_secret": self._secret,
                "redirect_uri":  "https://localhost",
            },
        )
        self._store(resp)

    def _refresh(self) -> None:
        if not self._refresh_token:
            raise RuntimeError("No refresh token — re-run OAuth flow")
        resp = http_post_form(
            f"{self._base}/v1/oauth2/token",
            {
                "grant_type":    "refresh_token",
                "refresh_token": self._refresh_token,
                "client_id":     self._id,
                "client_secret": self._secret,
            },
        )
        self._store(resp)

    def _store(self, resp: dict) -> None:
        self._access_token  = resp["access_token"]
        self._refresh_token = resp.get("refresh_token", self._refresh_token)
        self._token_expiry  = time.monotonic() + resp.get("expires_in", 1800)
        self._token_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._token_file.with_suffix(self._token_file.suffix + ".tmp")
        payload = json.dumps({
            "access_token":  self._access_token,
            "refresh_token": self._refresh_token,
            "expires_at":    time.time() + resp.get("expires_in", 1800),
            "refresh_expires_at": time.time() + resp.get("refresh_token_expires_in", 7776000),
        })
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(payload)
        os.replace(tmp, self._token_file)
        os.chmod(self._token_file, 0o600)

    def _load_tokens(self) -> None:
        if not self._token_file.exists():
            return
        try:
            os.chmod(self._token_file, 0o600)
            data = json.loads(self._token_file.read_text())
            self._access_token  = data.get("access_token")
            self._refresh_token = data.get("refresh_token")
            remaining = data.get("expires_at", 0) - time.time()
            self._token_expiry  = time.monotonic() + max(remaining, 0)
        except Exception:
            pass

    def _auth(self) -> str:
        if not self.available:
            raise RuntimeError("DIGIKEY_CLIENT_ID / DIGIKEY_CLIENT_SECRET not set")
        if self._access_token and time.monotonic() < self._token_expiry - 30:
            return self._access_token
        if self._refresh_token:
            try:
                self._refresh()
                return self._access_token  # type: ignore[return-value]
            except Exception:
                pass
        # Fall back to client_credentials (works on production without browser flow)
        resp = http_post_form(
            f"{self._base}/v1/oauth2/token",
            {"grant_type": "client_credentials", "client_id": self._id, "client_secret": self._secret},
        )
        self._store(resp)
        return self._access_token  # type: ignore[return-value]

    def _headers(self) -> dict:
        return {
            "Authorization":             f"Bearer {self._auth()}",
            "X-DIGIKEY-Client-Id":       self._id,
            "X-DIGIKEY-Locale-Site":     "US",
            "X-DIGIKEY-Locale-Language": "en",
            "X-DIGIKEY-Locale-Currency": "USD",
        }

    def search(self, keyword: str, limit: int = 10) -> list[PartResult]:
        if not self.available:
            return []
        self._rl.wait()
        data = http_post(
            f"{self._base}/products/v4/search/keyword",
            body={"keywords": keyword, "limit": limit, "offset": 0},
            headers={**self._headers(), "Content-Type": "application/json"},
        )
        return [_parse_dk(p) for p in data.get("Products", [])]

    def part(self, dkpn: str) -> PartResult | None:
        if not self.available:
            return None
        self._rl.wait()
        encoded = urllib.parse.quote(dkpn, safe="")
        data = http_get(
            f"{self._base}/products/v4/search/{encoded}/productdetails",
            headers=self._headers(),
        )
        return _parse_dk(data.get("Product") or data)


def _parse_dk(p: dict) -> PartResult:
    # v4 API uses ManufacturerProductNumber; v3 used ManufacturerPartNumber
    mpn = (p.get("ManufacturerProductNumber") or p.get("ManufacturerPartNumber") or "").strip()

    # Stock: v4 returns int directly
    qty = p.get("QuantityAvailable")
    if isinstance(qty, list):
        qty = sum(q.get("Quantity", 0) for q in qty)
    elif not isinstance(qty, int):
        qty = None

    # Price: v4 returns float directly; v3 had pricing tiers. DigiKey prices are
    # USD → normalise to INR for consistent display + comparison with Mouser.
    price = p.get("UnitPrice")
    price_breaks = []
    for tier in (p.get("StandardPricing") or p.get("PricingTiers") or []):
        try:
            price_breaks.append({"quantity": int(tier.get("BreakQuantity", 1)),
                                 "unit_price": float(tier.get("UnitPrice")),
                                 "currency": "USD"})
        except (TypeError, ValueError):
            continue
    if price is None:
        for tier in (p.get("StandardPricing") or p.get("PricingTiers") or []):
            if tier.get("BreakQuantity", 9999) <= 1:
                price = tier.get("UnitPrice")
                break
    if price is not None and not price_breaks:
        try:
            price_breaks.append({"quantity": 1, "unit_price": float(price), "currency": "USD"})
        except (TypeError, ValueError):
            pass
    price = to_inr(price, "USD")

    desc = p.get("Description", {})
    desc_str = desc.get("ProductDescription", "") if isinstance(desc, dict) else str(desc)

    # DigiKey part number: v4 may be in ProductVariations
    dkpn = p.get("DigiKeyPartNumber", "")
    if not dkpn:
        for var in (p.get("ProductVariations") or []):
            dkpn = var.get("DigiKeyProductNumber", "")
            if dkpn:
                break

    # ProductStatus: v4 returns string directly or dict
    status = p.get("ProductStatus")
    if isinstance(status, dict):
        status = status.get("Status", "")

    return PartResult(
        mpn=mpn,
        manufacturer=(p.get("Manufacturer") or {}).get("Name", ""),
        description=desc_str,
        vendor="digikey",
        vendor_sku=dkpn,
        datasheet_url=p.get("DatasheetUrl") or p.get("PrimaryDatasheet"),
        stock=qty,
        price_inr=price,
        category=(p.get("Category") or {}).get("Name"),
        lifecycle=status or "",
        extra={"detail_url": p.get("ProductUrl") or ""},
        price_breaks=price_breaks,
        source_url=p.get("ProductUrl") or None,
    )

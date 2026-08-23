"""Multi-vendor aggregator: searches DigiKey + Mouser + Nexar in parallel,
deduplicates by MPN, ranks by availability then price.

Usage:
    agg = Aggregator()
    results = agg.search("ATmega328P-AU", limit=5)
    best    = agg.best(results)          # highest stock, lowest price
    url     = agg.datasheet_url(results) # first non-None datasheet URL
"""

import threading
from datetime import datetime, timezone
from .base import PartResult, exact_mpn_match, normalize_mpn
from .digikey  import DigikeyClient
from .mouser   import MouserClient
from .octopart import OctopartClient


class Aggregator:
    def __init__(self):
        self.digikey  = DigikeyClient()
        self.mouser   = MouserClient()
        self.octopart = OctopartClient()
        self.last_errors: dict[str, str] = {}

    @property
    def vendors_online(self) -> list[str]:
        out = []
        if self.digikey.available:  out.append("digikey")
        if self.mouser.available:   out.append("mouser")
        if self.octopart.available: out.append("octopart")
        return out

    def search(self, keyword: str, limit: int = 8) -> list[PartResult]:
        """Search all configured vendors in parallel, dedup by MPN, rank."""
        results: list[PartResult] = []
        lock = threading.Lock()
        self.last_errors = {}

        def _run(name, fn):
            try:
                parts = fn(keyword, limit)
                with lock:
                    results.extend(parts)
            except Exception as exc:
                with lock:
                    self.last_errors[name] = str(exc)

        threads = []
        if self.digikey.available:
            t = threading.Thread(target=_run, args=("digikey", self.digikey.search), daemon=True)
            threads.append(t); t.start()
        if self.mouser.available:
            t = threading.Thread(target=_run, args=("mouser", self.mouser.search), daemon=True)
            threads.append(t); t.start()
        if self.octopart.available:
            t = threading.Thread(target=_run, args=("octopart", self.octopart.search), daemon=True)
            threads.append(t); t.start()

        for t in threads:
            t.join(timeout=20)

        return _dedup_rank(results)

    def best(self, results: list[PartResult]) -> PartResult | None:
        # Drop sandbox stubs and — crucially — eval boards / kits, so a ₹11k
        # DRV8353RS-EVM never beats the ₹646 chip just for having more stock.
        valid = [r for r in results if r.mpn and r.mpn.strip()]
        real  = [r for r in valid if not _is_kit(r)] or valid
        in_stock = [r for r in real if (r.stock or 0) > 0]
        if in_stock:
            # cheapest in-stock part wins (best price); stock breaks ties.
            return sorted(in_stock, key=lambda r: (r.price_inr or 9e9, -(r.stock or 0)))[0]
        if real:
            return sorted(real, key=lambda r: r.price_inr or 9e9)[0]
        return None

    def datasheet_url(self, results: list[PartResult]) -> str | None:
        for r in results:
            if r.datasheet_url:
                return r.datasheet_url
        return None

    def compare(self, mpn: str) -> list[dict]:
        """Cross-vendor price comparison for one part: every vendor's offer for
        the queried MPN, cheapest (₹) first."""
        return self.quote(mpn, quantity=1)["offers"]

    def quote(self, mpn: str, quantity: int = 1,
              vendors: tuple[str, ...] = ("digikey", "mouser", "octopart")) -> dict:
        """Exact-MPN, quantity-aware quote with source and FX provenance."""
        if not normalize_mpn(mpn) or quantity <= 0:
            raise ValueError("mpn and positive quantity are required")
        unknown = set(vendors) - {"digikey", "mouser", "octopart"}
        if unknown:
            raise ValueError(f"unsupported quote vendors: {sorted(unknown)}")
        results: list[PartResult] = []
        errors: dict[str, str] = {}
        lock = threading.Lock()

        def call(name, fn):
            try:
                result = fn()
                candidates = result if isinstance(result, list) else [result] if result else []
                exact = [r for r in candidates if exact_mpn_match(mpn, r.mpn)]
                with lock:
                    results.extend(exact)
                    if candidates and not exact:
                        errors[name] = "vendor response did not contain the exact requested MPN"
            except Exception as exc:
                with lock:
                    errors[name] = str(exc)

        jobs = []
        if "digikey" in vendors and self.digikey.available:
            def digikey_lookup():
                try:
                    result = self.digikey.part(mpn)
                except Exception:
                    result = None
                return result or self.digikey.search(mpn, 8)
            jobs.append(("digikey", digikey_lookup))
        if "mouser" in vendors and self.mouser.available:
            def mouser_lookup():
                try:
                    result = self.mouser.part(mpn)
                except Exception:
                    result = None
                return result or self.mouser.search(mpn, 8)
            jobs.append(("mouser", mouser_lookup))
        if "octopart" in vendors and self.octopart.available:
            jobs.append(("octopart", lambda: self.octopart.search(mpn, limit=8)))
        threads = [(name, threading.Thread(target=call, args=(name, fn), daemon=True))
                   for name, fn in jobs]
        for _, thread in threads: thread.start()
        for name, thread in threads:
            thread.join(timeout=20)
            if thread.is_alive():
                with lock:
                    errors.setdefault(name, "vendor lookup timed out")

        with lock:
            quoted_results = list(results)
            quote_errors = dict(errors)

        offers = []
        for result in quoted_results:
            price = result.price_for_quantity(quantity)
            offers.append({"vendor": result.vendor, "mpn": result.mpn,
                           "manufacturer": result.manufacturer, "sku": result.vendor_sku,
                           "stock": result.stock, "price": price,
                           "datasheet_url": result.datasheet_url,
                           "lifecycle": result.lifecycle,
                           "retrieved_utc": result.retrieved_utc,
                           "source_url": result.source_url,
                           "exact_mpn_match": True})
        offers.sort(key=lambda offer: (
            0 if (offer.get("stock") or 0) >= quantity else 1,
            ((offer.get("price") or {}).get("price_inr_estimate") or 9e99)))
        return {"schema": "design-studio.procurement-quote/1", "mpn": mpn,
                "quantity": quantity, "retrieved_utc": datetime.now(timezone.utc).isoformat(),
                "offers": offers, "vendor_errors": quote_errors,
                "complete": bool(offers) and len(quote_errors) == 0}

    def check_availability(self, mpn: str) -> dict:
        """Returns {"available": bool, "total_stock": int, "best_price_inr": float|None}."""
        quote = self.quote(mpn, quantity=1)
        total = sum((r.get("stock") or 0) for r in quote["offers"])
        prices = [(r.get("price") or {}).get("price_inr_estimate")
                  for r in quote["offers"] if (r.get("price") or {}).get("price_inr_estimate") is not None]
        return {
            "available":     total > 0,
            "total_stock":   total,
            "best_price_inr": min(prices) if prices else None,
            "vendors":       list({r["vendor"] for r in quote["offers"]}),
            "quote":         quote,
        }


_KIT_HINTS = ("EVM", "EVAL", "-KIT", " KIT", "DEMO", "EVALUATION",
              "DEVELOPMENT BOARD", "BREAKOUT", "DAUGHTER")


def _is_kit(r: PartResult) -> bool:
    """True for eval boards / kits / demos — not the bare component."""
    u = (r.mpn + " " + (r.description or "")).upper()
    return any(h in u for h in _KIT_HINTS)


def _dedup_rank(parts: list[PartResult]) -> list[PartResult]:
    # Group every vendor's offer for the same MPN so we can (a) keep the cheapest
    # in-stock offer and (b) record the competing offers for price comparison.
    groups: dict[str, list[PartResult]] = {}
    for p in parts:
        key = normalize_mpn(p.mpn)
        if key:
            groups.setdefault(key, []).append(p)

    chosen: list[PartResult] = []
    for offers in groups.values():
        in_stock = [o for o in offers if (o.stock or 0) > 0]
        pool = in_stock or offers
        best = sorted(pool, key=lambda o: (o.price_inr if o.price_inr is not None else 9e9))[0]
        if not best.datasheet_url:
            best.datasheet_url = next((o.datasheet_url for o in offers if o.datasheet_url), None)
        # attach every vendor's offer (cheapest ₹ first) for comparison
        best.extra = dict(best.extra or {})
        best.extra["offers"] = sorted(
            [{"vendor": o.vendor, "mpn": o.mpn, "price_inr": o.price_inr, "stock": o.stock,
              "sku": o.vendor_sku, "price_breaks": o.price_breaks,
              "retrieved_utc": o.retrieved_utc} for o in offers],
            key=lambda x: (x["price_inr"] if x["price_inr"] is not None else 9e9))
        chosen.append(best)

    ranked = sorted(
        chosen,
        key=lambda r: (
            0 if (r.stock or 0) > 100 else 1 if (r.stock or 0) > 0 else 2,
            r.price_inr or 9999,
        ),
    )
    return ranked

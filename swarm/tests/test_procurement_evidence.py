#!/usr/bin/env python3
import hashlib
import io
import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from swarm.vendors.aggregator import Aggregator
from swarm.vendors.base import PartResult, exact_mpn_match, normalize_mpn
from swarm.vendors.digikey import DigikeyClient
from swarm.vendors.evidence import (cached_download_pdf, download_pdf, finalize_mpn_evidence,
                                    mpn_mentioned, validate_remote_url)
from swarm.vendors.web_fallback import parse_search_html
from swarm.agents.schema_validator import validate


def check(condition, message):
    if not condition:
        raise AssertionError(message)


class FakeClient:
    def __init__(self, parts=None, available=True):
        self.available = available; self.parts = parts or []
    def part(self, _mpn):
        return self.parts[0] if self.parts else None
    def search(self, _mpn, _limit=8, **_kwargs):
        return self.parts


class Response(io.BytesIO):
    def __init__(self, body, url="https://example.com/ABC-123.pdf", length=None):
        super().__init__(body); self._url = url
        self.headers = {"Content-Length": str(len(body) if length is None else length)}
    def __enter__(self): return self
    def __exit__(self, *_args): self.close()
    def geturl(self): return self._url


def public_resolver(*_args, **_kwargs):
    return [(2, 1, 6, "", ("93.184.216.34", 443))]


def test_exact_mpn_and_quantity_quotes():
    check(exact_mpn_match("ABC-123", "abc123"), "punctuation normalization failed")
    check(not exact_mpn_match("ABC-123", "ABC-123TR"), "packaging suffix was collapsed")
    part = PartResult("ABC-123", "Acme", "sensor", "digikey", "DK1", None,
                      100, 83.0, "Sensors", "Active",
                      price_breaks=[{"quantity": 1, "unit_price": 1.0, "currency": "USD"},
                                    {"quantity": 100, "unit_price": 0.7, "currency": "USD"}])
    quote = part.price_for_quantity(250)
    check(quote["break_quantity"] == 100 and quote["unit_price"] == 0.7,
          "quantity price break was not selected")
    agg = Aggregator.__new__(Aggregator)
    agg.digikey = FakeClient([part])
    agg.mouser = FakeClient([PartResult("ABC-123", "Acme", "sensor", "mouser", "M1",
                                       None, 50, 90.0, "Sensors", "Active")])
    agg.octopart = FakeClient(available=False); agg.last_errors = {}
    result = agg.quote("ABC-123", 100)
    check(result["complete"] and len(result["offers"]) == 2, "exact multi-vendor quote incomplete")
    check(all(o["exact_mpn_match"] for o in result["offers"]), "quote lacks exact-MPN evidence")

    agg.mouser = FakeClient([PartResult("ABC-123TR", "Acme", "sensor", "mouser", "M2",
                                       None, 50, 90.0, "Sensors", "Active")])
    mismatch = agg.quote("ABC-123", 1)
    check(len(mismatch["offers"]) == 1 and "mouser" in mismatch["vendor_errors"],
          "mismatched packaging variant was accepted")


def test_bounded_pdf_evidence():
    pdf = b"%PDF-1.7\nABC-123 datasheet\n%%EOF"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "part.pdf"
        evidence = download_pdf("https://example.com/ABC-123.pdf", path, "ABC-123",
            opener=lambda *_args, **_kwargs: Response(pdf), resolver=public_resolver)
        check(evidence["sha256"] == hashlib.sha256(pdf).hexdigest(), "PDF digest mismatch")
        final = finalize_mpn_evidence(evidence, "ACME ABC-123 electrical data")
        check(final["mpn_match"] == "exact", "exact PDF MPN was not evidenced")
        check(finalize_mpn_evidence(evidence, "ACME ABC-124")["mpn_match"] == "mismatch",
              "wrong PDF MPN was accepted")
        try:
            download_pdf("https://example.com/large.pdf", path,
                opener=lambda *_args, **_kwargs: Response(pdf, length=60 * 1024 * 1024),
                resolver=public_resolver)
        except ValueError:
            pass
        else:
            raise AssertionError("oversize datasheet was accepted")
        calls = [0]
        def opener(*_args, **_kwargs):
            calls[0] += 1
            return Response(pdf)
        cache = Path(directory) / "cache"
        cached_download_pdf("https://example.com/ABC-123.pdf", path, "ABC-123",
                            cache_dir=cache, opener=opener, resolver=public_resolver)
        second = cached_download_pdf("https://example.com/ABC-123.pdf", path, "ABC-123",
                                     cache_dir=cache, opener=opener, resolver=public_resolver)
        check(calls[0] == 1 and second["cache_hit"], "verified datasheet cache was not reused")
    try:
        validate_remote_url("https://127.0.0.1/private.pdf", resolver=public_resolver)
    except ValueError:
        pass
    else:
        raise AssertionError("private URL was accepted")


def test_web_fallback_is_discovery_only():
    body = """
    <a href="https://example.com/ABC-123-datasheet.pdf">ABC-123 datasheet</a>
    <a href="https://example.com/ABC-123-product">ABC-123 product</a>
    <a href="http://bad.example/ABC-123.pdf">ABC-123 insecure</a>
    <a href="https://example.com/XYZ-999.pdf">wrong part</a>
    """
    results = parse_search_html(body, "ABC-123")
    check(len(results) == 1 and not results[0]["trusted"],
          "web fallback did not filter or mark untrusted discovery")

    ordered = parse_search_html("""
      <a href="https://archive.example/ABC-123.pdf">ABC-123 public copy</a>
      <a href="https://www.mouser.com/datasheet/ABC-123.pdf">ABC-123 distributor copy</a>
      <a href="https://www.acme.com/documents/ABC-123.pdf">Acme ABC-123 official datasheet</a>
    """, "ABC-123", manufacturer="Acme")
    check([item["source_tier"] for item in ordered] ==
          ["manufacturer-likely", "distributor-mirror", "public-web"],
          "official-first datasheet discovery order was not preserved")


def test_digikey_token_permissions():
    with tempfile.TemporaryDirectory() as directory:
        client = DigikeyClient.__new__(DigikeyClient)
        client._token_file = Path(directory) / "tokens.json"
        client._refresh_token = None
        client._store({"access_token": "secret", "refresh_token": "refresh", "expires_in": 60})
        mode = stat.S_IMODE(client._token_file.stat().st_mode)
        check(mode == 0o600, f"DigiKey token mode is {oct(mode)}, expected 0600")


def test_component_trust_boundary_requires_evidence():
    component = {"schema": "design-studio.component/2",
                 "component": {"manufacturer": "Acme", "mpn": "ABC-123"},
                 "symbol": {"pins": []}, "footprint": {"name": "TEST", "mount": "smd", "pads": []}}
    failures = validate(component, require_evidence=True)
    check(any("evidence" in failure for failure in failures),
          "component/2 trust boundary accepted missing datasheet evidence")


if __name__ == "__main__":
    test_exact_mpn_and_quantity_quotes()
    test_bounded_pdf_evidence()
    test_web_fallback_is_discovery_only()
    test_digikey_token_permissions()
    test_component_trust_boundary_requires_evidence()
    print("Procurement and datasheet evidence tests passed")

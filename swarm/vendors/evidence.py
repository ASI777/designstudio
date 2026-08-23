"""Trust boundary for supplier PDFs and procurement evidence."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import socket
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .base import normalize_mpn

MAX_DATASHEET_BYTES = 50 * 1024 * 1024


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_remote_url(url: str, resolver=socket.getaddrinfo) -> None:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("datasheet URL must be credential-free HTTPS")
    try:
        addresses = [ipaddress.ip_address(parsed.hostname)]
    except ValueError:
        addresses = []
        for record in resolver(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM):
            addresses.append(ipaddress.ip_address(record[4][0]))
    if not addresses or any(ip.is_private or ip.is_loopback or ip.is_link_local
                            or ip.is_reserved or ip.is_multicast for ip in addresses):
        raise ValueError("datasheet URL resolves to a non-public address")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, resolver):
        super().__init__(); self._resolver = resolver
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_remote_url(newurl, resolver=self._resolver)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def mpn_mentioned(text: str, mpn: str) -> bool:
    key = normalize_mpn(mpn)
    return bool(key) and key in normalize_mpn(text)


def local_pdf_evidence(path: str | Path, expected_mpn: str = "") -> dict:
    source = Path(path).resolve()
    raw = source.read_bytes()
    if len(raw) > MAX_DATASHEET_BYTES or not raw.startswith(b"%PDF"):
        raise ValueError("local datasheet is not a bounded PDF")
    return {"schema": "design-studio.datasheet-evidence/1", "source_kind": "local",
            "source": str(source), "retrieved_utc": _utc_now(), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(), "expected_mpn": expected_mpn,
            "mpn_match": "pending-text-validation" if expected_mpn else "not_requested"}


def download_pdf(url: str, destination: str | Path, expected_mpn: str = "",
                 opener=None, resolver=socket.getaddrinfo,
                 max_bytes: int = MAX_DATASHEET_BYTES, timeout: int = 30) -> dict:
    validate_remote_url(url, resolver=resolver)
    if opener is None:
        opener = urllib.request.build_opener(_SafeRedirect(resolver)).open
    request = urllib.request.Request(url, headers={"User-Agent": "DesignStudio/1.0"})
    digest = hashlib.sha256()
    total = 0
    destination = Path(destination)
    with opener(request, timeout=timeout) as response:
        final_url = response.geturl() if hasattr(response, "geturl") else url
        validate_remote_url(final_url, resolver=resolver)
        length = response.headers.get("Content-Length") if getattr(response, "headers", None) else None
        if length and int(length) > max_bytes:
            raise ValueError("datasheet exceeds download safety limit")
        with destination.open("wb") as output:
            while True:
                chunk = response.read(min(1024 * 1024, max_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("datasheet exceeds download safety limit")
                digest.update(chunk); output.write(chunk)
    with destination.open("rb") as handle:
        if handle.read(4) != b"%PDF":
            destination.unlink(missing_ok=True)
            raise ValueError("downloaded supplier content is not a PDF")
    return {"schema": "design-studio.datasheet-evidence/1", "source_kind": "remote",
            "source": url, "final_url": final_url, "retrieved_utc": _utc_now(),
            "bytes": total, "sha256": digest.hexdigest(), "expected_mpn": expected_mpn,
            "mpn_match": "pending-text-validation" if expected_mpn else "not_requested"}


def cached_download_pdf(url: str, destination: str | Path, expected_mpn: str = "",
                        cache_dir: str | Path | None = None, opener=None,
                        resolver=socket.getaddrinfo, max_bytes: int = MAX_DATASHEET_BYTES,
                        timeout: int = 30) -> dict:
    cache = Path(cache_dir or (Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
                               / "designstudio" / "datasheets"))
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    cached_pdf, cached_json = cache / f"{key}.pdf", cache / f"{key}.json"
    destination = Path(destination)
    if cached_pdf.exists() and cached_json.exists():
        try:
            metadata = json.loads(cached_json.read_text())
            raw_hash = hashlib.sha256(cached_pdf.read_bytes()).hexdigest()
            if (cached_pdf.stat().st_size <= max_bytes and raw_hash == metadata.get("sha256")
                    and cached_pdf.read_bytes()[:4] == b"%PDF"):
                shutil.copyfile(cached_pdf, destination)
                metadata.update({"expected_mpn": expected_mpn,
                                 "mpn_match": "pending-text-validation" if expected_mpn else "not_requested",
                                 "cache_hit": True})
                return metadata
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    temporary = cache / f"{key}.{os.getpid()}.tmp"
    metadata = download_pdf(url, temporary, expected_mpn, opener=opener,
                            resolver=resolver, max_bytes=max_bytes, timeout=timeout)
    os.chmod(temporary, 0o600)
    os.replace(temporary, cached_pdf)
    meta_tmp = cache / f"{key}.{os.getpid()}.json.tmp"
    meta_tmp.write_text(json.dumps(metadata, sort_keys=True))
    os.chmod(meta_tmp, 0o600); os.replace(meta_tmp, cached_json)
    shutil.copyfile(cached_pdf, destination)
    metadata["cache_hit"] = False
    return metadata


def finalize_mpn_evidence(evidence: dict, extracted_text: str) -> dict:
    out = dict(evidence)
    expected = out.get("expected_mpn", "")
    out["mpn_match"] = "exact" if not expected or mpn_mentioned(extracted_text, expected) else "mismatch"
    return out

"""Sequential, deduplicated sourcing and component-CAD review workflow."""
from __future__ import annotations

from collections.abc import Callable, Iterable
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.request import Request, urlopen

from .component_library import ComponentLibrary, normalize_mpn
from swarm.vendors.aggregator import Aggregator
from swarm.vendors.base import exact_mpn_match


class ComponentWorkflowError(RuntimeError):
    pass


def _download_pdf(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "DesignStudio-ComponentWorkflow/1"})
    with urlopen(request, timeout=30) as response:
        content = response.read(32 * 1024 * 1024 + 1)
    if len(content) > 32 * 1024 * 1024 or not content.startswith(b"%PDF"):
        raise ComponentWorkflowError("vendor datasheet is not a bounded PDF")
    return content


class ComponentWorkflow:
    """Resolve local assets first, then process unresolved unique MPNs one at a time.

    ``generator`` is the typed Sol-medium inspection → Luna-xhigh command →
    deterministic footprint/AP242 preview adapter. It is intentionally invoked
    serially, while the vendor aggregator performs DigiKey/Mouser lookups in
    parallel internally.
    """

    def __init__(self, library: ComponentLibrary, *, aggregator: Aggregator | None = None,
                 downloader: Callable[[str], bytes] | None = None):
        self.library = library
        self.aggregator = aggregator or Aggregator()
        self.downloader = downloader or _download_pdf

    def _approved_pair(self, mpn: str) -> dict | None:
        if self.library.get_component(mpn, approved_only=True) is None:
            return None
        footprint = self.library.asset_for(mpn, "footprint")
        step = self.library.asset_for(mpn, "model_3d")
        if not footprint or not step or not footprint["exact_mpn"] or not step["exact_mpn"]:
            return None
        return {"status": "reused", "mpn": mpn,
                "footprint_sha256": footprint["sha256"],
                "step_sha256": step["sha256"],
                "artifact_pair_digest": hashlib.sha256(
                    f"{footprint['sha256']}:{step['sha256']}".encode()).hexdigest()}

    def process(self, mpns: Iterable[str], *,
                generator: Callable[[str, bytes, dict], dict]) -> dict:
        unique: list[str] = []
        spellings: dict[str, str] = {}
        for value in mpns:
            key = normalize_mpn(value)
            if key and key not in spellings:
                spellings[key] = str(value).strip()
                unique.append(key)
        # Complete the authoritative SQLite pass for every unique MPN before
        # allowing the first supplier request.
        approved = {key: self._approved_pair(spellings[key]) for key in unique}
        results, unresolved = [], []
        for key in unique:  # Deliberately sequential and resumable.
            mpn = spellings[key]
            existing = approved[key]
            if existing:
                results.append(existing)
                continue
            quote = self.aggregator.quote(mpn, quantity=1,
                                          vendors=("digikey", "mouser"))
            offers = [offer for offer in quote.get("offers", [])
                      if offer.get("vendor") in ("digikey", "mouser")
                      and offer.get("exact_mpn_match")
                      and exact_mpn_match(mpn, str(offer.get("mpn") or ""))
                      and (offer.get("stock") or 0) > 0]
            if not offers:
                online = set(getattr(self.aggregator, "vendors_online", []))
                reason = ("DigiKey/Mouser credentials are not configured" if
                          not online.intersection({"digikey", "mouser"}) else
                          "No in-stock exact-MPN DigiKey/Mouser offer is available")
                item = {"status": "blocked", "mpn": mpn, "reason": reason,
                        "vendor_errors": quote.get("vendor_errors", {})}
                unresolved.append(item); results.append(item)
                continue
            errors: list[str] = []
            generated = None
            for offer in offers:  # Datasheets are tried sequentially.
                url = str(offer.get("datasheet_url") or "")
                if not url:
                    errors.append(f"{offer['vendor']}: datasheet URL missing")
                    continue
                try:
                    pdf = self.downloader(url)
                    if not pdf.startswith(b"%PDF"):
                        raise ComponentWorkflowError("invalid PDF signature")
                    generated = generator(mpn, pdf, offer)
                    break
                except Exception as exc:
                    errors.append(f"{offer['vendor']}: {exc}")
            if not generated:
                item = {"status": "blocked", "mpn": mpn,
                        "reason": "All exact-MPN vendor datasheets failed sequential validation",
                        "errors": errors}
                unresolved.append(item); results.append(item)
                continue
            manifest = generated.get("manifest") or {}
            manifest_path = generated.get("manifest_path") or manifest.get("manifest_path")
            digest = str(manifest.get("preview_digest") or "")
            if not manifest_path or not re.fullmatch(r"[0-9a-f]{64}", digest):
                item = {"status": "blocked", "mpn": mpn,
                        "reason": "Generator did not return a digest-bound preview manifest"}
                unresolved.append(item); results.append(item)
                continue
            validation = manifest.get("validation") or {}
            ready = manifest.get("state") == "ready" and all(
                validation.get(key) == "passed"
                for key in ("footprint", "pin_pad_map", "brep", "step_roundtrip"))
            blockers = list(manifest.get("blockers") or [])
            if manifest.get("state") == "ready" and not ready:
                blockers.append("deterministic footprint/B-Rep/STEP validation is incomplete")
            review_id = self.library.enqueue_component_review(
                mpn, manifest_path, digest, ready=ready,
                blockers=blockers)
            item = {"status": "review_required" if ready else "blocked", "mpn": mpn,
                    "review_id": review_id, "artifact_digest": digest,
                    "manifest_path": str(Path(manifest_path).resolve()),
                    "supplier": offer.get("vendor"), "errors": errors}
            if not ready:
                unresolved.append(item)
            results.append(item)
        self.library.connection.commit()
        return {"schema": "design-studio.component-workflow/1",
                "unique_mpn_count": len(unique), "results": results,
                "review_queue": self.library.component_review_queue(),
                "unresolved": unresolved,
                "publication_allowed": False}

    def publish_approved(self, review_id: int, *, reviewer: str,
                         artifact_digest: str,
                         publisher: Callable[..., dict]) -> dict:
        row = next((item for item in self.library.component_review_queue(include_completed=True)
                    if item["review_id"] == review_id), None)
        if not row:
            raise KeyError(f"component review {review_id} does not exist")
        if row["state"] != "ready" or row["artifact_digest"] != artifact_digest:
            raise ComponentWorkflowError("review is blocked, complete, or digest-mismatched")
        reviewed = self.library.review_component_asset(
            review_id, reviewer=reviewer, approved=True,
            artifact_digest=artifact_digest,
            rationale="Explicit component asset review before publication")
        self.library.connection.commit()
        try:
            published = publisher(row["manifest_path"], reviewer=reviewer,
                                  library_db=self.library.path)
        except Exception as exc:
            # Preserve the immutable approval record, but return the queue item
            # to ready so publication is resumable after a transient failure.
            self.library.connection.execute(
                "UPDATE component_review_queue SET state='ready',rationale=? WHERE review_id=?",
                (f"Publication failed: {type(exc).__name__}", review_id))
            self.library.connection.commit()
            raise
        return {"review": reviewed, "publication": published}

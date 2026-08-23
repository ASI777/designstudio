#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from swarm.memory.component_library import ComponentLibrary  # noqa: E402
from swarm.memory.component_workflow import ComponentWorkflow  # noqa: E402


class Vendors:
    vendors_online = ["digikey", "mouser"]

    def __init__(self): self.calls = []

    def quote(self, mpn, quantity=1, vendors=("digikey", "mouser")):
        assert vendors == ("digikey", "mouser")
        self.calls.append(mpn)
        return {"offers": [
            {"vendor": "digikey", "mpn": mpn, "exact_mpn_match": True,
             "stock": 5, "datasheet_url": "https://fixture/invalid"},
            {"vendor": "mouser", "mpn": mpn, "exact_mpn_match": True,
             "stock": 7, "datasheet_url": "https://fixture/valid"}], "vendor_errors": {}}


def component(mpn: str) -> dict:
    return {"schema": "design-studio.component/2",
            "component": {"manufacturer": "Fixture", "mpn": mpn},
            "symbol": {"pins": []}, "footprint": {}, "package_3d": {}}


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ds-component-workflow-") as raw:
        root = Path(raw)
        with ComponentLibrary(root / "library.sqlite3") as library:
            with library.connection:
                library.upsert_component(component("LOCAL-1"), status="approved")
                fp = library.put_asset("kicad_v6_footprint", b"(footprint)", format="kicad_mod")
                step = library.put_asset("step", b"ISO-10303-21;END-ISO-10303-21;", format="step")
                library.link_asset("LOCAL-1", "footprint", fp, exact_mpn=True)
                library.link_asset("LOCAL-1", "model_3d", step, exact_mpn=True)
            vendors = Vendors(); downloads = []

            def download(url):
                downloads.append(url)
                if url.endswith("invalid"): raise RuntimeError("invalid PDF")
                return b"%PDF-1.7\nexact fixture\n%%EOF"

            generated = []
            def generate(mpn, pdf, offer):
                generated.append((mpn, offer["vendor"]))
                directory = root / mpn; directory.mkdir()
                manifest_path = directory / "preview-manifest.json"
                manifest = {"schema": "design-studio.component-preview/1", "state": "ready",
                            "component_mpn": mpn, "validation": {
                                "footprint": "passed", "pin_pad_map": "passed",
                                "brep": "passed", "step_roundtrip": "passed"}}
                digest = hashlib.sha256(json.dumps(
                    manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                manifest["preview_digest"] = digest
                manifest_path.write_text(json.dumps(manifest))
                return {"manifest": manifest, "manifest_path": str(manifest_path)}

            workflow = ComponentWorkflow(library, aggregator=vendors, downloader=download)
            result = workflow.process(["REMOTE-2", "LOCAL-1", "remote 2"], generator=generate)
            assert result["unique_mpn_count"] == 2
            assert vendors.calls == ["REMOTE-2"]
            assert generated == [("REMOTE-2", "mouser")]
            assert downloads == ["https://fixture/invalid", "https://fixture/valid"]
            assert result["publication_allowed"] is False
            queued = result["review_queue"]
            assert len(queued) == 1 and queued[0]["state"] == "ready"
            try:
                library.review_component_asset(queued[0]["review_id"], reviewer="EE",
                    approved=True, artifact_digest="0" * 64)
            except ValueError:
                pass
            else:
                raise AssertionError("digest-mismatched component review was accepted")
            approved = library.approve_all_reviewed(
                reviewer="EE", artifact_digests={
                    queued[0]["review_id"]: queued[0]["artifact_digest"]})
            assert approved[0]["state"] == "approved"
    print("Component sourcing and review workflow tests passed")


if __name__ == "__main__":
    main()

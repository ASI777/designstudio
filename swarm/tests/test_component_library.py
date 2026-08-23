#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from swarm.memory.component_library import ComponentLibrary, infer_datasheet_identity


def component(mpn: str = "ABC-123") -> dict:
    return {"schema": "design-studio.component/2",
            "component": {"manufacturer": "Acme", "mpn": mpn},
            "symbol": {"pins": []}, "footprint": {}, "package_3d": {}}


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ds-component-db-") as raw:
        root = Path(raw)
        corpus = root / "corpus" / "part_1"
        corpus.mkdir(parents=True)
        first = corpus / "digikey_ABC-123.pdf"
        duplicate = corpus / "mouser_ABC-123.pdf"
        first.write_bytes(b"%PDF-1.7\nABC-123\n%%EOF")
        duplicate.write_bytes(first.read_bytes())
        invalid = corpus / "digikey_BAD.pdf"
        invalid.write_bytes(b"<html>denied</html>")
        db = root / "library.sqlite3"
        with ComponentLibrary(db) as library:
            summary = library.ingest_datasheet_tree(root / "corpus")
            assert summary["found"] == 3
            assert summary["imported"] == 2
            assert summary["failed"] == 1
            assert summary["unique_assets"] == 1
            again = library.ingest_datasheet_tree(root / "corpus")
            assert again["unchanged"] == 2 and again["failed"] == 1
            vendor, hint = infer_datasheet_identity(first)
            assert (vendor, hint) == ("digikey", "ABC-123")
            row = library.connection.execute(
                "SELECT identity_state FROM datasheet_sources LIMIT 1").fetchone()
            assert row["identity_state"] == "filename_hint"

            with library.connection:
                key = library.upsert_component(component(), status="approved",
                                               evidence_state="exact_mpn")
                pdf_sha = library.put_asset_file("datasheet_pdf", first, format="pdf")
                library.link_asset("ABC-123", "datasheet", pdf_sha, exact_mpn=True)
                library.record_approval(f"component:{key}", "engineering_review",
                                        "a" * 64, "Fixture EE")
            assert library.get_component("abc 123")["component"]["mpn"] == "ABC-123"
            materialized = library.materialize_asset(pdf_sha, root / "cache" / "ABC-123.pdf")
            assert materialized.read_bytes() == first.read_bytes()
            stats = library.stats()
            assert stats["approved_components"] == 1
        connection = sqlite3.connect(db)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        connection.close()
        legacy = root / "legacy.sqlite3"
        connection = sqlite3.connect(legacy)
        connection.execute("PRAGMA user_version=1")
        connection.close()
        with ComponentLibrary(legacy) as migrated:
            assert migrated.connection.execute("PRAGMA user_version").fetchone()[0] == 2
            assert migrated.connection.execute(
                "SELECT name FROM sqlite_master WHERE name='component_review_queue'").fetchone()
    print("Component SQLite library tests passed")


if __name__ == "__main__":
    main()

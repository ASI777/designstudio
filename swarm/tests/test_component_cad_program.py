#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from swarm.memory.component_cad_program import (  # noqa: E402
    ComponentCadError, build_component_cad_program, prepare_component_preview,
    prepare_blocked_component_preview, publish_component_preview, export_kicad_v6_footprint,
)
from swarm.memory.footprint_verify import placement_allowed  # noqa: E402


def inspection(mpn: str) -> dict:
    return {
        "schema": "design-studio.datasheet-inspection/1",
        "requested_mpn": mpn, "identified_mpn": mpn,
        "package_variant": "TEST-PACKAGE", "confidence": 0.98,
        "regions": [
            {"page": 2, "role": "pinout", "bbox_normalized": [0, 0, 1, 1],
             "evidence_summary": "pin table"},
            {"page": 4, "role": "package", "bbox_normalized": [0, 0, 1, 1],
             "evidence_summary": "dimensioned package"},
            {"page": 5, "role": "land_pattern", "bbox_normalized": [0, 0, 1, 1],
             "evidence_summary": "recommended land pattern"},
        ],
        "warnings": [],
    }


def component(mount: str = "smd") -> dict:
    mpn = "DS-SMT-1" if mount == "smd" else "DS-THT-1"
    through = mount == "through_hole"
    pads = [
        {"number": "1", "x_mm": -1.27, "y_mm": 0, "width_mm": 1.2 if not through else 1.8,
         "height_mm": 1.4 if not through else 1.8, "shape": "rect" if not through else "circle"},
        {"number": "2", "x_mm": 1.27, "y_mm": 0, "width_mm": 1.2 if not through else 1.8,
         "height_mm": 1.4 if not through else 1.8, "shape": "rect" if not through else "circle"},
    ]
    if through:
        for pad in pads:
            pad["drill_mm"] = 1.0
    primitives = [{
        "id": "body", "role": "body", "shape": "box",
        "center_mm": [0, 0, 0.8], "size_mm": [3.0, 4.0, 1.4],
        "rotation_deg_xyz": [0, 0, 0], "color_rgba": [0.05, 0.06, 0.07, 1],
        "provenance": ["datasheet:page:4:body A/B/H"],
    }]
    for index, pad in enumerate(pads):
        primitives.append({
            "id": f"lead-{index + 1}", "role": "lead",
            "shape": "cylinder" if through else "box",
            "center_mm": [pad["x_mm"], 0, 0.1 if not through else 0.0],
            "size_mm": [0.7, 0.7, 2.0] if through else [pad["width_mm"], pad["height_mm"], 0.2],
            "rotation_deg_xyz": [0, 0, 0], "color_rgba": [0.7, 0.72, 0.75, 1],
            "provenance": ["datasheet:page:4:lead dimensions"],
        })
    return {
        "schema": "design-studio.component/2",
        "component": {"manufacturer": "Fixture", "mpn": mpn, "category": "other"},
        "symbol": {"pins": [
            {"number": "1", "name": "A", "electrical_type": "passive"},
            {"number": "2", "name": "B", "electrical_type": "passive"},
        ]},
        "electrical": {"parameters": [{"name": "test", "typ": 1, "unit": "V"}]},
        "footprint": {"name": f"{mpn}_FP", "mount": mount,
                      "generated": "vector-trace", "body": {"width_mm": 3.0,
                      "length_mm": 4.0, "height_mm": 1.4}, "pads": pads,
                      "solder_mask_expansion_mm": 0.05,
                      **({"paste_reduction_mm": 0.02} if not through else {}),
                      "outlines": {name: {
                          "points_mm": [[-1.5, -2], [1.5, -2], [1.5, 2], [-1.5, 2]],
                          "provenance": ["datasheet:page:4:package outline"]}
                          for name in ("silkscreen", "fabrication", "assembly")}},
        "package_3d": {"height_mm": 1.4, "standoff_mm": 0.1, "shape": "box",
                       "construction": {"author": "gpt-5.6-luna-xhigh",
                           "method": "parametric", "complexity": "simple",
                           "complexity_reasons": [], "datasheet_pages": [4],
                           "assumptions": [], "primitives": primitives}},
        "evidence": {"schema": "design-studio.datasheet-evidence/1", "source_kind": "local",
                     "source": "/fixture.pdf", "retrieved_utc": "2026-01-01T00:00:00Z",
                     "bytes": 1000, "sha256": "a" * 64, "expected_mpn": mpn,
                     "mpn_match": "exact"},
        "extraction": {"provider": "codex-cli", "model": "gpt-5.6-luna",
                       "reasoning_effort": "xhigh", "grounding": {"status": "passed"},
                       "verification": {"geometry_confidence": "verified",
                           "state": "review_required", "placement_allowed": False,
                           "blockers": [], "warnings": []}},
    }


def test_smt_and_tht_programs() -> None:
    for mount in ("smd", "through_hole"):
        item = component(mount)
        program = build_component_cad_program(item, inspection(item["component"]["mpn"]))
        assert program["schema"] == "design-studio.component-cad-program/1"
        assert len([command for command in program["footprint_commands"]
                    if command["op"] == "footprint.pad.create"]) == 2
        assert program["model_commands"][-1]["op"] == "model.step.export"


def test_kicad_v6_footprint_binds_aligned_step() -> None:
    with tempfile.TemporaryDirectory(prefix="ds-kicad-footprint-") as raw:
        path = export_kicad_v6_footprint(component(), Path(raw) / "part.kicad_mod")
        text = path.read_text()
        assert "(version 20211014)" in text
        assert '(model "package.step"' in text
        assert text.count("(pad ") == 2
        assert '(layer "F.SilkS")' in text
        assert '(layer "F.Fab")' in text
        assert '(layer "Dwgs.User")' in text
        assert "(solder_mask_margin 0.05)" in text
        assert "(solder_paste_margin -0.02)" in text


def test_missing_geometry_fails_closed() -> None:
    item = component()
    item["package_3d"]["construction"]["primitives"][0].pop("provenance")
    try:
        build_component_cad_program(item, inspection(item["component"]["mpn"]))
    except ComponentCadError as exc:
        assert "callout provenance" in str(exc)
    else:
        raise AssertionError("ungrounded geometry was accepted")


def test_missing_geometry_produces_non_publishable_partial_preview() -> None:
    with tempfile.TemporaryDirectory(prefix="ds-cad-blocked-") as raw:
        item = component()
        blocked = prepare_blocked_component_preview(
            item, inspection(item["component"]["mpn"]),
            ["package height callout is missing"], preview_root=raw)
        assert blocked["manifest"]["state"] == "blocked"
        assert blocked["component"]["extraction"]["verification"]["blockers"]
        try:
            publish_component_preview(blocked["manifest_path"])
        except ComponentCadError as exc:
            assert "not publication-ready" in str(exc)
        else:
            raise AssertionError("blocked preview was published")


def test_preview_publish_and_step_roundtrip() -> None:
    compiler = os.environ.get("DESIGNSTUDIO_COMPONENT_CAD")
    if not compiler:
        raise AssertionError("DESIGNSTUDIO_COMPONENT_CAD must name the built OCCT executor")
    with tempfile.TemporaryDirectory(prefix="ds-cad-program-") as raw:
        root = Path(raw)
        item = component("through_hole")
        datasheet = b"%PDF-1.7\nDS-THT-1 exact datasheet\n%%EOF"
        item["evidence"]["sha256"] = hashlib.sha256(datasheet).hexdigest()
        item["evidence"]["bytes"] = len(datasheet)
        preview = prepare_component_preview(
            item, inspection(item["component"]["mpn"]),
            preview_root=root / "previews", compiler=compiler,
            datasheet_bytes=datasheet)
        manifest = preview["manifest"]
        assert not (root / "components").exists() and not (root / "bindings").exists()
        assert manifest["state"] == "ready"
        step_bytes = Path(manifest["step_path"]).read_bytes()
        assert step_bytes.startswith(b"ISO-10303-21")
        assert b"AP242_MANAGED_MODEL_BASED_3D_ENGINEERING" in step_bytes
        assert Path(manifest["footprint_preview"]).read_bytes().startswith(b"\x89PNG")
        assert Path(manifest["kicad_footprint_path"]).suffix == ".kicad_mod"
        allowed, reason = placement_allowed(preview["component"])
        assert not allowed and "approval" in reason

        published = publish_component_preview(
            preview["manifest_path"], component_root=root / "components",
            binding_root=root / "bindings", reviewer="test-reviewer")
        stored = json.loads(Path(published["component_path"]).read_text())
        allowed, reason = placement_allowed(stored)
        assert allowed, reason
        assert Path(published["binding_path"]).is_file()
        assert Path(published["step_path"]).is_file()
        assert Path(published["library_db"]).is_file()
        import sqlite3
        database = sqlite3.connect(published["library_db"])
        kinds = dict(database.execute(
            "SELECT kind,COUNT(*) FROM assets GROUP BY kind").fetchall())
        assert kinds == {"datasheet_pdf": 1, "kicad_v6_footprint": 1, "step": 1}
        assert database.execute("SELECT COUNT(*) FROM approvals WHERE approved=1").fetchone()[0] == 1
        database.close()


def test_publish_rejects_preview_changed_after_review() -> None:
    compiler = os.environ.get("DESIGNSTUDIO_COMPONENT_CAD")
    if not compiler:
        raise AssertionError("DESIGNSTUDIO_COMPONENT_CAD must name the built OCCT executor")
    with tempfile.TemporaryDirectory(prefix="ds-cad-tamper-") as raw:
        preview = prepare_component_preview(
            component(), inspection("DS-SMT-1"), preview_root=Path(raw) / "previews",
            compiler=compiler)
        component_path = Path(preview["manifest"]["component_path"])
        changed = json.loads(component_path.read_text())
        changed["footprint"]["pads"][0]["x_mm"] += 0.1
        component_path.write_text(json.dumps(changed), encoding="utf-8")
        try:
            publish_component_preview(preview["manifest_path"])
        except ComponentCadError as exc:
            assert "component JSON digest mismatch" in str(exc)
        else:
            raise AssertionError("changed preview was published")


if __name__ == "__main__":
    test_smt_and_tht_programs()
    test_kicad_v6_footprint_binds_aligned_step()
    test_missing_geometry_fails_closed()
    test_missing_geometry_produces_non_publishable_partial_preview()
    test_preview_publish_and_step_roundtrip()
    test_publish_rejects_preview_changed_after_review()
    print("Component CAD program tests passed")

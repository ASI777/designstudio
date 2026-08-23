#!/usr/bin/env python3
"""End-to-end production CAM test using an unmistakably synthetic manufacturer."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from swarm.production import ProductionExportError, apply_fabrication_profile, export_package
from swarm.memory.component_binding import bind_component


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def rules() -> dict:
    return {
        "schema_version": 1, "id": "fixture-cm-4layer-2026-07",
        "name": "Synthetic CM four-layer fixture", "ipc_performance_class": 2,
        "producibility_level": "B", "source": "fabricator:synthetic-fixture",
        "source_revision": "2026-07", "fabricator": "Synthetic Fixture PCB Ltd",
        "assembler": "Synthetic Fixture Assembly Ltd",
        "limits_mm": {"default_clearance": 0.15, "min_trace_width": 0.15,
            "min_mechanical_drill": 0.2, "min_annular_ring": 0.125,
            "min_drill_to_drill": 0.25, "min_microvia_drill": 0.1,
            "min_microvia_wall": 0.1, "min_copper_to_edge": 0.25,
            "min_copper_to_hole": 0.2, "min_courtyard_clearance": 0.2,
            "min_mask_sliver": 0.08, "min_silk_width": 0.1},
        "checks": {"connectivity": True, "skew": True,
                   "release_requires_native_drc": True},
        "severity": {"MIN_WIDTH": "error", "TRACE_CLEARANCE": "error"},
    }


def make_component_step(binary: Path, directory: Path) -> Path:
    job = directory / "component-board-job.txt"
    job.write_text("BOARD 0.5 4 -0.5 -0.5 0.5 -0.5 0.5 0.5 -0.5 0.5\n",
                   encoding="ascii")
    step = directory / "TEST-DEVICE.step"
    result = subprocess.run([str(binary), str(job), str(step)], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    assert result.returncode == 0, result.stdout
    return step


def project(component: Path, record: Path, binding: dict) -> dict:
    return {
        "version": 2, "document_id": "project:production-cam-fixture", "revision": 1,
        "board_width_mm": 20.0, "board_height_mm": 10.0,
        "board_outline_pts": [[0, 0], [20, 0], [20, 10], [0, 10]],
        "board_cutouts": [], "copper_layers": 4, "copper_t_mm": 0.035,
        "dielectric_h_mm": 0.18, "dielectric_er": 4.2, "loss_tangent": 0.018,
        "pcb_rules": rules(), "stackup": {}, "fabrication_profile": {},
        "net_table": {"0": {"id": 0, "name": "GND"}},
        "net_classes": [], "nets": [], "copper_zones": [], "rule_areas": [],
        "traces": [{"uuid": "trace-1", "net": 0, "layer": 0, "w_mm": 0.2,
                    "ax_mm": 4.0, "ay_mm": 5.0, "bx_mm": 6.0, "by_mm": 5.0}],
        "vias": [{"uuid": "via-1", "net": 0, "from": 0, "to": 3,
                  "x_mm": 10.0, "y_mm": 5.0, "dia_mm": 0.6, "drill_mm": 0.3,
                  "via_type": "through"}],
        "footprints": [{"uuid": "fp-1", "ref": "U1", "manufacturer": "Fixture Devices",
            "mpn": "TEST-DEVICE", "lib": "Fixture:SOIC2", "value": "fixture",
            "x_mm": 5.0, "y_mm": 5.0, "rot_deg": 90, "side": 0,
            "pads": [
                {"uuid": "pad-1", "name": "1", "net": 0, "x_mm": -1,
                 "y_mm": 0, "w_mm": 1, "h_mm": 1.5, "shape": "roundrect",
                 "corner_r_mm": 0.15, "th": False, "drill_mm": 0},
                {"uuid": "pad-2", "name": "2", "net": 0, "x_mm": 1,
                 "y_mm": 0, "w_mm": 1, "h_mm": 1.5, "shape": "rect",
                 "corner_r_mm": 0, "th": False, "drill_mm": 0}],
            "bound_component": {"schema": "design-studio.bound-component-ref/1",
                "binding_id": binding["binding_id"],
                "binding_digest": binding["binding_digest"],
                "record_uri": record.name,
                "model_3d": binding["model_3d"]}}],
        "unresolved_components": [],
        "release_readiness": {"manufacturing_release": "blocked",
            "blocking_gates": ["named fabricator/assembler profile"]},
    }


def profile(source: Path) -> dict:
    layers = [
        {"order": 0, "name": "L1", "kind": "copper", "material": "copper",
         "thickness_mm": 0.035, "copper_weight_oz": 1},
        {"order": 1, "name": "prepreg-1", "kind": "dielectric", "material": "FR-4",
         "thickness_mm": 0.18, "dielectric_er": 4.2, "loss_tangent": 0.018},
        {"order": 2, "name": "L2", "kind": "copper", "material": "copper",
         "thickness_mm": 0.035, "copper_weight_oz": 1},
        {"order": 3, "name": "core", "kind": "dielectric", "material": "FR-4",
         "thickness_mm": 1.10, "dielectric_er": 4.2, "loss_tangent": 0.018},
        {"order": 4, "name": "L3", "kind": "copper", "material": "copper",
         "thickness_mm": 0.035, "copper_weight_oz": 1},
        {"order": 5, "name": "prepreg-2", "kind": "dielectric", "material": "FR-4",
         "thickness_mm": 0.18, "dielectric_er": 4.2, "loss_tangent": 0.018},
        {"order": 6, "name": "L4", "kind": "copper", "material": "copper",
         "thickness_mm": 0.035, "copper_weight_oz": 1},
    ]
    return {"schema": "design-studio.fabrication-profile/1",
        "id": "synthetic-fixture-profile-do-not-fabricate",
        "fabricator": "Synthetic Fixture PCB Ltd",
        "assembler": "Synthetic Fixture Assembly Ltd",
        "source": {"document_path": source.name, "document_sha256": sha(source),
            "uri": "fixture://synthetic-cm-rules", "revision": "2026-07",
            "retrieved_utc": "2026-07-16T00:00:00Z"},
        "approval": {"status": "approved", "reviewer": "Automated test fixture",
                     "approved_utc": "2026-07-16T00:00:00Z"},
        "pcb_rules": rules(),
        "stackup": {"finished_thickness_mm": 1.6, "thickness_tolerance_mm": 0.1,
            "surface_finish": "fixture-finish", "solder_mask_color": "fixture-green",
            "silkscreen_color": "none", "layers": layers},
        "impedance": {"coupon_required": False, "targets": []},
        "outputs": {"gerber_format": "Gerber X2", "coordinate_precision": 6,
            "origin": "absolute", "separate_pth_npth": True,
            "solder_mask_expansion_mm": 0.05, "paste_reduction_mm": 0.02,
            "silkscreen": "none", "pick_place": {"include_through_hole": True,
                "bottom_rotation": "viewed-from-top", "rotation_corrections_deg": {}}}}


def verification(project_path: Path, board: dict) -> dict:
    categories = {name: {"status": "pass", "required": True, "findings": [], "metrics": {}}
                  for name in ("drc", "connectivity", "signal_integrity", "power_integrity",
                               "thermal", "mechanical")}
    return {"schema": "design-studio.verification/1",
        "generated_utc": "2026-07-16T00:00:00Z", "overall_status": "pass",
        "project": {"document_id": board["document_id"], "revision": board["revision"],
                    "file_sha256": sha(project_path)},
        "constraint_profile": {key: board["pcb_rules"][key]
            for key in ("id", "source", "source_revision", "ipc_performance_class",
                        "producibility_level", "fabricator", "assembler")},
        "categories": categories}


def main(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        component = make_component_step(binary, directory)
        component_definition = {
            "schema": "design-studio.component/2",
            "component": {"mpn": "TEST-DEVICE", "manufacturer": "Fixture Devices"},
            "symbol": {"pins": [
                {"number": "1", "name": "A", "electrical_type": "passive"},
                {"number": "2", "name": "B", "electrical_type": "passive"}]},
            "footprint": {"pads": [
                {"number": "1", "x_mm": -1.0, "y_mm": 0.0,
                 "width_mm": 1.0, "height_mm": 1.5, "shape": "roundrect"},
                {"number": "2", "x_mm": 1.0, "y_mm": 0.0,
                 "width_mm": 1.0, "height_mm": 1.5, "shape": "rect"}],
                "body": {"width_mm": 2.0, "length_mm": 2.0}},
            "electrical": {"kind": "passive-device",
                           "pin_functions": [{"pin": "1", "function": "A"},
                                             {"pin": "2", "function": "B"}]},
            "evidence": {"package_pin_count": 2, "package_variant": "FIXTURE-SOIC2"},
        }
        binding = bind_component(
            component_definition, component, alignment_status="verified",
            model_mpn="TEST-DEVICE", source_uri="fixture:TEST-DEVICE")
        record = directory / "TEST-DEVICE.bound.json"
        write_json(record, binding)
        source = directory / "synthetic-manufacturer-rules.txt"
        source.write_text("SYNTHETIC TEST FIXTURE — NOT A MANUFACTURING SPECIFICATION\n")
        profile_path = directory / "fabrication-profile.json"
        write_json(profile_path, profile(source))
        original = directory / "original.dsproj"
        wrapped_project = {"format": "design-studio.project/3", "units": "nm",
            "materials": [], "parts": [], "board": project(component, record, binding),
            "assembly": {"tree": [], "joints": []}, "constraints": [],
            "tolerances": [], "rationale": []}
        write_json(original, wrapped_project)
        applied = directory / "applied.dsproj"
        result = apply_fabrication_profile(original, profile_path, applied)
        assert result["revision"] == 2
        applied_document = json.loads(applied.read_text())
        assert applied_document["format"] == "design-studio.project/3"
        applied_board = applied_document["board"]
        assert applied_board["pcb_rules"]["fabricator"] == "Synthetic Fixture PCB Ltd"
        verification_path = directory / "verification.json"
        write_json(verification_path, verification(applied, applied_board))
        output = directory / "production"
        manifest = export_package(applied, profile_path, verification_path, output, binary)
        assert manifest["validation"] == {"status": "pass", "gerber_files": 12,
            "pth_hits": 1, "npth_hits": 0, "ipc_test_records": 3, "step_schema": "AP242"}
        assert {item["role"] for item in manifest["artifacts"]} == {
            "gerber_archive", "drill", "npth_drill", "drill_report", "bom",
            "pick_place", "ipc_netlist", "board_step"}
        archive = next(output.glob("*-gerbers.zip"))
        with zipfile.ZipFile(archive) as handle:
            assert len(handle.namelist()) == 12
            assert any(name.endswith("F_Mask.gbr") for name in handle.namelist())
            assert any(name.endswith("B_Paste.gbr") for name in handle.namelist())
        assert "TEST-DEVICE" in next((output / "assembly").glob("*-BOM.csv")).read_text()
        assert next((output / "cam").glob("*-NPTH.drl")).read_text().startswith("M48\n")
        assert next((output / "mechanical").glob("*.step")).read_text(errors="ignore").startswith(
            "ISO-10303-21")

        original_record = record.read_bytes()
        changed_binding = json.loads(original_record)
        changed_binding["datasheet_evidence"]["package_pin_count"] = 3
        write_json(record, changed_binding)
        try:
            export_package(applied, profile_path, verification_path,
                           directory / "must-not-export-semantic-tamper", binary)
            raise AssertionError("tampered component semantics were accepted")
        except ProductionExportError as exc:
            assert "bound-component record" in str(exc)
        record.write_bytes(original_record)

        stale = json.loads(verification_path.read_text())
        stale["project"]["file_sha256"] = "0" * 64
        write_json(verification_path, stale)
        try:
            export_package(applied, profile_path, verification_path,
                           directory / "must-not-exist", binary)
            raise AssertionError("stale verification was accepted")
        except ProductionExportError as exc:
            assert "stale" in str(exc)

        source.write_text("changed\n")
        try:
            apply_fabrication_profile(original, profile_path, directory / "must-not-apply.dsproj")
            raise AssertionError("changed manufacturer source document was accepted")
        except ProductionExportError as exc:
            assert "digest changed" in str(exc)

    print("PRODUCTION_CAM_OK")


if __name__ == "__main__":
    main(Path(sys.argv[1]))

#!/usr/bin/env python3
"""Materialize a compiled robot CAD program in real FreeCAD/OpenCASCADE.

FreeCAD consumes normal command-line arguments itself, so callers provide the
compiled product manifest and destination through environment variables:

    DESIGNSTUDIO_PRODUCT_MANIFEST=/path/product-manifest.json
    DESIGNSTUDIO_CAD_OUTPUT_ROOT=/path/output
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
WORKBENCH = ROOT / "freecad/DesignStudioWorkbench"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(WORKBENCH))

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402
import Mesh  # noqa: E402

from DesignStudio.mechanical_cad import execute_program, program_digest  # noqa: E402
from swarm.memory.authoritative_product import validate_bundle  # noqa: E402


MATERIALIZER_VERSION = 6


def sha256_file(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def safe_name(command_id: str) -> str:
    return "DS_CAD_" + re.sub(r"[^A-Za-z0-9_]", "_", command_id)


def add_string(obj, name: str, value: str) -> None:
    if name not in obj.PropertiesList:
        obj.addProperty("App::PropertyString", name, "DesignStudio Semantic Identity")
    setattr(obj, name, value)


def add_bool(obj, name: str, value: bool) -> None:
    if name not in obj.PropertiesList:
        obj.addProperty("App::PropertyBool", name, "DesignStudio Semantic Identity")
    setattr(obj, name, value)


def style_for(role: str) -> tuple[tuple[float, float, float], int]:
    return {
        "cosmetic_shell": ((0.88, 0.89, 0.86), 0),
        "joint_cover": ((0.12, 0.14, 0.16), 0),
        "tool_flange": ((0.18, 0.20, 0.22), 0),
        "internal_structure": ((0.55, 0.58, 0.61), 0),
        "internal_fastener": ((0.10, 0.10, 0.11), 0),
        "pcb": ((0.08, 0.42, 0.18), 0),
        "harness": ((0.94, 0.34, 0.06), 0),
    }.get(role, ((0.72, 0.72, 0.72), 0))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate_materialization(path: Path) -> dict:
    manifest_path = path / "cad-materialization.json" if path.is_dir() else path
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    if document.get("schema") != "design-studio.cad-materialization/1":
        raise RuntimeError("CAD materialization schema is invalid")
    root = manifest_path.parent
    for record in document.get("artifacts", []):
        artifact = (root / record["path"]).resolve()
        if not artifact.is_relative_to(root) or not artifact.is_file():
            raise RuntimeError(f"CAD artifact is missing: {record['path']}")
        if sha256_file(artifact) != record["sha256"]:
            raise RuntimeError(f"CAD artifact digest mismatch: {record['path']}")
    return document


def main() -> int:
    manifest_value = os.environ.get("DESIGNSTUDIO_PRODUCT_MANIFEST", "")
    output_value = os.environ.get("DESIGNSTUDIO_CAD_OUTPUT_ROOT", "")
    if not manifest_value or not output_value:
        raise RuntimeError(
            "DESIGNSTUDIO_PRODUCT_MANIFEST and DESIGNSTUDIO_CAD_OUTPUT_ROOT are required")
    manifest_path = Path(manifest_value).expanduser().resolve()
    product_manifest = validate_bundle(manifest_path)
    bundle_root = manifest_path.parent
    cad_program_path = bundle_root / "mechanical/mechanical-cad-program.json"
    mechanical_path = bundle_root / "mechanical/mechanical-definition.json"
    electronics_path = bundle_root / "electronics/system-architecture.json"
    cad_program = json.loads(cad_program_path.read_text(encoding="utf-8"))
    mechanical = json.loads(mechanical_path.read_text(encoding="utf-8"))
    electronics = json.loads(electronics_path.read_text(encoding="utf-8"))
    expected_pcb_count = int(electronics["board_count"])
    expected_program_digest = program_digest(cad_program)
    output_root = Path(output_value).expanduser().resolve()
    destination = output_root / (
        f"{product_manifest['product_id']}-cad-v{MATERIALIZER_VERSION}-"
        f"{expected_program_digest[:12]}-{product_manifest['bundle_digest'][:12]}")
    if destination.exists():
        result = validate_materialization(destination)
        if result.get("program_digest") != expected_program_digest:
            raise RuntimeError("cached CAD materialization program digest is stale")
        if result.get("product_bundle_digest") != product_manifest["bundle_digest"]:
            raise RuntimeError("cached CAD materialization product bundle digest is stale")
        if result.get("requirements_sha256") != product_manifest["requirements_sha256"]:
            raise RuntimeError("cached CAD materialization requirement digest is stale")
        print(json.dumps({"ok": True, "reused": True, "path": str(destination),
                          "program_digest": result["program_digest"]}, sort_keys=True))
        return 0

    output_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.stage-", dir=output_root))
    document = None
    try:
        document = App.newDocument("DesignStudioAuthoritativeRobot")
        receipt = execute_program(document, cad_program)
        if receipt["program_digest"] != expected_program_digest or not receipt["solid_valid"]:
            raise RuntimeError("FreeCAD receipt does not match the typed CAD program")

        for obj in document.Objects:
            if getattr(obj, "ViewObject", None) is not None:
                obj.ViewObject.Visibility = False

        semantic_by_id = {record["command_id"]: record
                          for record in mechanical["semantic_parts"]}
        exterior_objects = []
        all_objects = []
        objects_by_role = {}
        semantic_receipts = []
        for command_id, semantic in semantic_by_id.items():
            obj = document.getObject(safe_name(command_id))
            if obj is None or getattr(obj, "Shape", None) is None or obj.Shape.isNull():
                raise RuntimeError(f"semantic CAD object is absent or empty: {command_id}")
            role = semantic["role"]
            add_string(obj, "SemanticRole", role)
            add_bool(obj, "DefaultVisible", bool(semantic["default_visible"]))
            add_string(obj, "FastenerExposure", str(semantic.get("fastener_exposure", "none")))
            add_string(obj, "RequirementsSHA256", product_manifest["requirements_sha256"])
            color, transparency = style_for(role)
            if getattr(obj, "ViewObject", None) is not None:
                obj.ViewObject.ShapeColor = color
                obj.ViewObject.LineColor = tuple(max(0.0, component * 0.45) for component in color)
                obj.ViewObject.Transparency = transparency
                obj.ViewObject.Visibility = bool(semantic["default_visible"])
            all_objects.append(obj)
            objects_by_role.setdefault(role, []).append(obj)
            if semantic["default_visible"]:
                exterior_objects.append(obj)
            semantic_receipts.append({
                "command_id": command_id,
                "object_name": obj.Name,
                "semantic_role": role,
                "default_visible": bool(semantic["default_visible"]),
                "fastener_exposure": semantic.get("fastener_exposure", "none"),
                "solid_count": len(obj.Shape.Solids),
                "volume_mm3": round(float(obj.Shape.Volume), 6),
                "valid": bool(obj.Shape.isValid()),
            })

        if sum(item["fastener_exposure"] == "external" for item in semantic_receipts) != 0:
            raise RuntimeError("materialized CAD exposes a fastener")
        if sum(item["semantic_role"] == "internal_fastener" for item in semantic_receipts) != 7:
            raise RuntimeError("materialized CAD does not contain seven internal fastener groups")
        if sum(item["semantic_role"] == "pcb" for item in semantic_receipts) \
                != expected_pcb_count:
            raise RuntimeError(
                f"materialized CAD does not contain {expected_pcb_count} PCB envelopes")
        if sum(item["semantic_role"] == "harness" for item in semantic_receipts) != 7:
            raise RuntimeError("materialized CAD does not contain seven internal harness routes")
        if any(not item["valid"] or item["solid_count"] < 1 for item in semantic_receipts):
            raise RuntimeError("materialized CAD has an invalid semantic solid")
        harness_clearance_receipts = []
        interior_clearance_receipts = []
        hollow_shell_receipts = []

        def checked_overlap(first_id: str, second_id: str, *, interface: str) -> None:
            first = document.getObject(safe_name(first_id))
            second = document.getObject(safe_name(second_id))
            if first is None or second is None:
                raise RuntimeError(f"interior-clearance objects are missing: {interface}")
            overlap = float(first.Shape.common(second.Shape).Volume)
            if overlap > 1.0e-6:
                raise RuntimeError(
                    f"unintended interior intersection {interface}: {overlap:.9f} mm^3")
            interior_clearance_receipts.append({
                "interface": interface,
                "first_command_id": first_id,
                "second_command_id": second_id,
                "intersection_volume_mm3": round(overlap, 9),
                "status": "pass",
            })

        def checked_hollow_shell(shell_id: str, outer_id: str) -> None:
            shell = document.getObject(safe_name(shell_id))
            outer = document.getObject(safe_name(outer_id))
            if shell is None or outer is None:
                raise RuntimeError(f"hollow-shell objects are missing: {shell_id}")
            shell_volume = float(shell.Shape.Volume)
            outer_volume = float(outer.Shape.Volume)
            removed_volume = outer_volume - shell_volume
            if shell_volume <= 0.0 or removed_volume <= 1.0e-6:
                raise RuntimeError(f"{shell_id} was not materialized as a hollow shell")
            hollow_shell_receipts.append({
                "shell_command_id": shell_id,
                "outer_command_id": outer_id,
                "shell_volume_mm3": round(shell_volume, 6),
                "outer_volume_mm3": round(outer_volume, 6),
                "removed_cavity_volume_mm3": round(removed_volume, 6),
                "status": "pass",
            })

        checked_hollow_shell("base-shell", "base-shell-outer")
        checked_overlap("base-shell", "pcb-control-01", interface="base-shell_to_control-pcb")
        checked_overlap("base-shell", "j1-cover", interface="base-shell_to_J1-cover")
        checked_overlap("base-shell", "j1-hub", interface="base-shell_to_J1-hub")
        checked_overlap("base-shell", "j1-harness", interface="base-shell_to_J1-harness")
        for index in range(1, 8):
            harness = document.getObject(safe_name(f"j{index}-harness"))
            core = document.getObject(safe_name(f"j{index}-structural-core"))
            hub = document.getObject(safe_name(f"j{index}-hub"))
            if harness is None or core is None or hub is None:
                raise RuntimeError(f"joint J{index} harness channel objects are missing")
            core_overlap = float(harness.Shape.common(core.Shape).Volume)
            hub_overlap = float(harness.Shape.common(hub.Shape).Volume)
            if core_overlap > 1.0e-6 or hub_overlap > 1.0e-6:
                raise RuntimeError(
                    f"joint J{index} harness intersects structural material")
            harness_clearance_receipts.append({
                "joint_id": f"J{index}",
                "harness_to_core_intersection_volume_mm3": round(core_overlap, 9),
                "harness_to_hub_intersection_volume_mm3": round(hub_overlap, 9),
                "status": "pass",
            })
            checked_hollow_shell(f"j{index}-cover", f"j{index}-cover-outer")
            checked_hollow_shell(f"j{index}-link-shell", f"j{index}-link-shell-outer")
            for first_id, second_id, label in (
                (f"j{index}-cover", f"j{index}-hub", "cover_to_hub"),
                (f"j{index}-cover", f"j{index}-harness", "cover_to_harness"),
                (f"j{index}-cover", f"j{index}-link-shell", "cover_to_outgoing-shell"),
                (f"j{index}-link-shell", f"j{index}-structural-core", "shell_to_core"),
                (f"j{index}-link-shell", f"j{index}-harness", "shell_to_harness"),
                (f"pcb-joint-{index:02d}-logic", f"j{index}-hub", "logic-pcb_to_hub"),
                (f"pcb-joint-{index:02d}-power", f"j{index}-hub", "power-pcb_to_hub"),
                (f"pcb-joint-{index:02d}-logic", f"j{index}-cover", "logic-pcb_to_cover"),
                (f"pcb-joint-{index:02d}-power", f"j{index}-cover", "power-pcb_to_cover"),
                (f"pcb-joint-{index:02d}-logic", f"j{index}-harness", "logic-pcb_to_harness"),
                (f"pcb-joint-{index:02d}-power", f"j{index}-harness", "power-pcb_to_harness"),
                (f"pcb-joint-{index:02d}-logic", f"pcb-joint-{index:02d}-power",
                 "logic-pcb_to_power-pcb"),
            ):
                checked_overlap(
                    first_id, second_id, interface=f"J{index}:{label}")
            if index > 1:
                checked_overlap(
                    f"j{index}-cover", f"j{index - 1}-link-shell",
                    interface=f"J{index}:cover_to_incoming-shell")

        document.recompute()
        fcstd_path = stage / "ds-cobot-r7.FCStd"
        all_step_path = stage / "ds-cobot-r7-complete.step"
        exterior_step_path = stage / "ds-cobot-r7-exterior.step"
        document.saveAs(str(fcstd_path))
        Part.export(all_objects, str(all_step_path))
        Part.export(exterior_objects, str(exterior_step_path))
        mesh_paths = []
        mesh_root = stage / "meshes"
        mesh_root.mkdir(parents=True, exist_ok=True)
        for role, role_objects in sorted(objects_by_role.items()):
            mesh_path = mesh_root / f"{role}.stl"
            Mesh.export(role_objects, str(mesh_path))
            mesh_paths.append(mesh_path)
        if not all(path.is_file() and path.stat().st_size > 0
                   for path in (fcstd_path, all_step_path, exterior_step_path, *mesh_paths)):
            raise RuntimeError("FreeCAD did not write every CAD artifact")

        App.closeDocument(document.Name)
        document = None
        reopened = App.openDocument(str(fcstd_path))
        controller = reopened.getObject(safe_name(cad_program["program_id"]))
        if controller is None or controller.ProgramDigest != expected_program_digest:
            raise RuntimeError("FCStd round-trip lost its program identity")
        for record in semantic_receipts:
            obj = reopened.getObject(record["object_name"])
            if obj is None or obj.SemanticRole != record["semantic_role"]:
                raise RuntimeError("FCStd round-trip lost semantic object identity")
            if obj.Shape.isNull() or not obj.Shape.isValid():
                raise RuntimeError("FCStd round-trip produced an invalid shape")
        App.closeDocument(reopened.Name)

        artifact_records = []
        for path in (fcstd_path, all_step_path, exterior_step_path, *mesh_paths):
            artifact_records.append({
                "path": str(path.relative_to(stage)), "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "media_type": "application/vnd.freecad" if path.suffix == ".FCStd"
                              else "model/step" if path.suffix == ".step" else "model/stl",
            })
        materialization = {
            "schema": "design-studio.cad-materialization/1",
            "materializer_version": MATERIALIZER_VERSION,
            "product_id": product_manifest["product_id"],
            "product_bundle_digest": product_manifest["bundle_digest"],
            "requirements_sha256": product_manifest["requirements_sha256"],
            "program_sha256": sha256_file(cad_program_path),
            "program_digest": expected_program_digest,
            "freecad_version": App.Version(),
            "kernel": "OpenCASCADE through FreeCAD",
            "default_view": "exterior",
            "externally_visible_fastener_count": 0,
            "semantic_objects": semantic_receipts,
            "hollow_shell_checks": hollow_shell_receipts,
            "harness_clearance_checks": harness_clearance_receipts,
            "interior_clearance_checks": interior_clearance_receipts,
            "checks": receipt["checks"],
            "artifacts": artifact_records,
        }
        write_json(stage / "cad-materialization.json", materialization)
        validate_materialization(stage)
        os.replace(stage, destination)
        print(json.dumps({"ok": True, "reused": False, "path": str(destination),
                          "program_digest": expected_program_digest,
                          "object_count": len(semantic_receipts)}, sort_keys=True))
        return 0
    except Exception:
        if document is not None:
            try:
                App.closeDocument(document.Name)
            except Exception:
                pass
        shutil.rmtree(stage, ignore_errors=True)
        raise


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run the real FreeCAD kernel through the DesignStudio workbench boundary."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

import FreeCAD as App
import Part

ROOT = Path(__file__).resolve().parents[3]
WORKBENCH = ROOT / "freecad" / "DesignStudioWorkbench"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(WORKBENCH))

from DesignStudio.freecad_adapter import (  # noqa: E402
    component_collision_report, ensure_controller, mark_object,
    set_board_region, snapshot_from_document, sync_bound_components,
)
from DesignStudio.mechanical_contract import (  # noqa: E402
    derive_contract, validate_contract, write_contract_and_project,
)
from DesignStudio.enclosure_generator import (  # noqa: E402
    compare_catalog, create_enclosure,
)
from DesignStudio.reconstruction import (  # noqa: E402
    export_reconstructed_package, reconstruct_candidate,
)
from swarm.memory.component_binding import bind_component  # noqa: E402
from swarm.memory.component_3d_assets import materialize_component_asset  # noqa: E402


def component_record() -> dict:
    return {
        "schema": "design-studio.component/2",
        "component": {"manufacturer": "Runtime Fixture", "mpn": "FC-RUNTIME-1",
                      "category": "mcu", "description": "FreeCAD runtime fixture"},
        "symbol": {"ref_des_prefix": "U", "pins": [
            {"number": "1", "name": "VDD", "electrical_type": "power_in"},
            {"number": "2", "name": "GND", "electrical_type": "power_in"}]},
        "electrical": {"power_domains": [{"name": "VDD", "pins": ["1"],
            "vmin_v": 3.0, "vnom_v": 3.3, "vmax_v": 3.6,
            "max_current_a": 0.1}]},
        "footprint": {"name": "FC_RUNTIME_2PAD", "mount": "smd",
            "body": {"width_mm": 2.0, "length_mm": 2.0, "height_mm": 1.0},
            "pads": [
                {"number": "1", "x_mm": -0.75, "y_mm": 0.0,
                 "width_mm": 0.6, "height_mm": 0.8},
                {"number": "2", "x_mm": 0.75, "y_mm": 0.0,
                 "width_mm": 0.6, "height_mm": 0.8}]},
        "package_3d": {"height_mm": 1.0, "standoff_mm": 0.0, "shape": "box"},
    }


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="designstudio-freecad-") as raw:
        directory = Path(raw)
        project_path = directory / "runtime.dsproj"
        project_path.write_text(json.dumps({
            "version": 2, "document_id": "freecad-runtime", "revision": 1,
            "footprints": [], "traces": [], "vias": [], "net_table": [],
            "net_classes": [], "rule_areas": []}), encoding="utf-8")

        document = App.newDocument("DesignStudioRuntime")
        board = document.addObject("Part::Feature", "LegalBoardVolume")
        board.Shape = Part.makeBox(80.0, 45.0, 1.6)
        fixed = document.addObject("Part::Feature", "FixedObstacle")
        fixed.Shape = Part.makeBox(4.0, 4.0, 4.0, App.Vector(60.0, 30.0, 1.6))
        mark_object(fixed, "fixed_item")
        fixed.RefDes = "MECH1"
        document.recompute()

        controller = ensure_controller(document)
        controller.ProjectPath = str(project_path)
        set_board_region(controller, board, "Face1")
        snapshot = snapshot_from_document(document, controller)
        contract = derive_contract(snapshot)
        validate_contract(contract)
        write_contract_and_project(project_path, contract)

        step_path = directory / "FC-RUNTIME-1.step"
        Part.makeBox(2.0, 2.0, 1.0).exportStep(str(step_path))
        binding = bind_component(component_record(), step_path,
                                 model_mpn="FC-RUNTIME-1",
                                 alignment_status="verified")
        generated_component = component_record()
        generated_component["component"]["mpn"] = "FC-RUNTIME-GLB"
        generated_asset = materialize_component_asset(
            generated_component, asset_root=directory / "component-assets")
        project = json.loads(project_path.read_text(encoding="utf-8"))
        project["footprints"] = [{
            "ref": "U1", "lib": "FC_RUNTIME_2PAD", "mpn": "FC-RUNTIME-1",
            "manufacturer": "Runtime Fixture", "x_mm": 20.0, "y_mm": 20.0,
            "rot_deg": 0.0, "side": 0, "h3d_mm": 1.0,
            "body_w_mm": 2.0, "body_h_mm": 2.0,
            "courtyard_pts": binding["footprint"]["courtyard_pts"],
            "pads": [],
            "bound_component": {
                "schema": "design-studio.bound-component-ref/1",
                "binding_id": binding["binding_id"],
                "binding_digest": binding["binding_digest"],
                "record_uri": "runtime.bound.json",
                "model_3d": binding["model_3d"],
            }}, {
            "ref": "U2", "lib": "FC_RUNTIME_2PAD", "mpn": "FC-RUNTIME-GLB",
            "manufacturer": "Runtime Fixture", "x_mm": 30.0, "y_mm": 20.0,
            "rot_deg": 90.0, "side": 0, "h3d_mm": 1.0,
            "body_w_mm": 2.0, "body_h_mm": 2.0, "pads": [],
            "asset_3d": generated_asset}]
        project_path.write_text(json.dumps(project, indent=2), encoding="utf-8")

        synchronized = sync_bound_components(document, controller)
        assert synchronized["imported"] == 2, synchronized
        assert synchronized["collision_status"] == "incomplete", synchronized
        imported = document.getObject("DS_U1")
        assert imported is not None and imported.Shape.Volume > 0
        visual = document.getObject("DS_U2")
        assert visual is not None and visual.TypeId == "Mesh::Feature"
        assert visual.Mesh.CountFacets > 0
        assert document.getObject("DS_PCB_Substrate").Shape.Volume > 0

        fixed.Shape = imported.Shape.copy()
        fixed.Placement = imported.Placement
        document.recompute()
        project_bytes = project_path.read_bytes()
        collided = component_collision_report(
            document, json.loads(project_bytes), project_bytes)
        assert collided["status"] == "fail", collided
        assert any(item["a"] == "U1" and item["b"] == "MECH1"
                   for item in collided["collisions"]), collided
        App.closeDocument(document.Name)

        for template in ("rectangular", "injection_clamshell", "handheld"):
            enclosure = App.newDocument(f"Enclosure_{template}")
            generated = create_enclosure(
                enclosure, template,
                {"width": 120.0, "depth": 80.0, "height": 42.0,
                 "wall": 2.4, "split": 21.0})
            assert generated["controller"].BuildStatus == "valid"
            assert (generated["controller"].Width,
                    generated["controller"].Depth,
                    generated["controller"].Height) == (120.0, 80.0, 42.0)
            assert generated["lower"].Shape.isValid() and generated["lower"].Shape.Volume > 0
            assert generated["upper"].Shape.isValid() and generated["upper"].Shape.Volume > 0
            assert generated["legal_pcb"].Shape.isValid() and generated["legal_pcb"].Shape.Volume > 0
            previous = generated["legal_pcb"].Shape.Volume
            generated["controller"].PcbClearance = 3.0
            enclosure.recompute()
            assert generated["controller"].BuildStatus == "valid"
            assert generated["legal_pcb"].Shape.Volume < previous
            App.closeDocument(enclosure.Name)

        catalog = compare_catalog((90, 55, 24), [
            {"part_number": "TOO-SMALL", "inner_mm": [80, 55, 24], "price": 2.0},
            {"part_number": "FIT-B", "inner_mm": [100, 60, 30], "price": 8.0},
            {"part_number": "FIT-A", "inner_mm": [95, 58, 28], "price": 9.0},
        ])
        assert [item["part_number"] for item in catalog] == ["FIT-A", "FIT-B"]

        candidate_path = directory / "generated-candidate.stl"
        Part.makeBox(100, 70, 35).exportStl(str(candidate_path))
        reconstruction = App.newDocument("CandidateReconstruction")
        rebuilt = reconstruct_candidate(
            reconstruction, candidate_path, bounding_box_mm=(100, 70, 35),
            silhouette_scores=(0.91, 0.92, 0.94), template="injection_clamshell")
        assert rebuilt["controller"].CandidateMeshSha256
        assert rebuilt["controller"].ReconstructionStatus.startswith(
            "editable section-network B-Rep")
        assert rebuilt["controller"].MasterBRep.Shape.isValid()
        assert rebuilt["controller"].MasterBRep.Shape.Volume > 0
        assert rebuilt["controller"].WallThickness == 2.0
        assert rebuilt["controller"].RibThickness == 1.2
        assert rebuilt["controller"].AssemblyClearance == 0.30
        assert rebuilt["legal_pcb"].Shape.isValid()
        assert all(not obj.Visibility for obj in rebuilt["controller"].CandidateEvidenceObjects)
        reconstruction.saveAs(str(directory / "reference-form-enclosure.FCStd"))
        exported = export_reconstructed_package(
            reconstruction, rebuilt, directory / "reference-form-export")
        assert exported["step_ap242"].endswith(".step")
        assert exported["step_schema"] == "AP242DIS"
        assert len(exported["prototype_parts"]) == 3
        for item in exported["prototype_parts"]:
            assert (directory / "reference-form-export" / item["stl"]).is_file()
            assert (directory / "reference-form-export" / item["3mf"]).is_file()
        App.closeDocument(reconstruction.Name)


if __name__ == "__main__":
    run()
    print("FreeCAD runtime workbench smoke passed")

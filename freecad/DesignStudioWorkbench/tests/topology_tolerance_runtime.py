#!/usr/bin/env python3
"""Exercise topology-referenced mates and tolerance-stack verification."""
from __future__ import annotations

import json
import copy
from pathlib import Path
import sys

import FreeCAD as App

ROOT = Path(__file__).resolve().parents[3]
WORKBENCH = ROOT / "freecad" / "DesignStudioWorkbench"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(WORKBENCH))

from DesignStudio.mechanical_cad import MechanicalCadProgramError, execute_program  # noqa: E402


def program():
    return {
        "schema": "design-studio.mechanical-cad-program/1",
        "program_id": "topology-tolerance-fixture",
        "units": "mm",
        "author": "runtime-test",
        "commands": [
            {"id": "base", "op": "part.box",
             "params": {"length_mm": 10, "width_mm": 20, "height_mm": 5},
             "provenance": ["runtime#base"]},
            {"id": "cover", "op": "part.box",
             "params": {"length_mm": 10, "width_mm": 20, "height_mm": 5},
             "provenance": ["runtime#cover"]},
            {"id": "edge-a-source", "op": "part.box",
             "params": {"length_mm": 4, "width_mm": 4, "height_mm": 4},
             "provenance": ["runtime#edge-a"]},
            {"id": "edge-b-source", "op": "part.box",
             "params": {"length_mm": 4, "width_mm": 4, "height_mm": 4},
             "provenance": ["runtime#edge-b"]},
            {"id": "assembly", "op": "assembly.create", "params": {},
             "provenance": ["runtime#assembly"]},
            {"id": "base-component", "op": "assembly.component",
             "params": {"assembly": "assembly", "source": "base",
                        "component_ref": "BASE", "position_mm": [0, 0, 0]},
             "provenance": ["runtime#base-component"]},
            {"id": "cover-component", "op": "assembly.component",
             "params": {"assembly": "assembly", "source": "cover",
                        "component_ref": "COVER", "position_mm": [100, 100, 50]},
             "provenance": ["runtime#cover-component"]},
            {"id": "face-fit", "op": "assembly.mate",
             "params": {
                 "assembly": "assembly", "first": "base-component",
                 "second": "cover-component", "kind": "face_distance",
                 "first_reference": {"component": "base-component", "subelement": "Face6"},
                 "second_reference": {"component": "cover-component", "subelement": "Face5"},
                 "distance_mm": 2,
             },
             "provenance": ["runtime#face-fit"]},
            {"id": "face-clearance", "op": "assembly.check_clearance",
             "params": {"assembly": "assembly", "first": "base-component",
                        "second": "cover-component", "min_clearance_mm": 1},
             "provenance": ["runtime#face-clearance"]},
            {"id": "edge-a", "op": "assembly.component",
             "params": {"assembly": "assembly", "source": "edge-a-source",
                        "component_ref": "EDGE_A", "position_mm": [50, 0, 0]},
             "provenance": ["runtime#edge-a-component"]},
            {"id": "edge-b", "op": "assembly.component",
             "params": {"assembly": "assembly", "source": "edge-b-source",
                        "component_ref": "EDGE_B", "position_mm": [100, 100, 50]},
             "provenance": ["runtime#edge-b-component"]},
            {"id": "edge-fit", "op": "assembly.mate",
             "params": {
                 "assembly": "assembly", "first": "edge-a", "second": "edge-b",
                 "kind": "edge_align",
                 "first_reference": {"component": "edge-a", "subelement": "Edge1"},
                 "second_reference": {"component": "edge-b", "subelement": "Edge1"},
             },
             "provenance": ["runtime#edge-fit"]},
            {"id": "stack", "op": "tolerance.stack.create",
             "params": {"assembly": "assembly", "name": "Cover fit stack"},
             "provenance": ["runtime#stack"]},
            {"id": "wall", "op": "tolerance.stack.item",
             "params": {"stack": "stack", "nominal_mm": 10,
                        "plus_mm": 0.1, "minus_mm": 0.1, "source": "wall-thickness"},
             "provenance": ["runtime#wall"]},
            {"id": "gap", "op": "tolerance.stack.item",
             "params": {"stack": "stack", "nominal_mm": 20,
                        "plus_mm": 0.2, "minus_mm": 0.1, "sense": -1,
                        "source": "cover-datum"},
             "provenance": ["runtime#gap"]},
            {"id": "stack-worst", "op": "tolerance.stack.check",
             "params": {"stack": "stack", "method": "worst_case",
                        "lower_limit_mm": -10.3, "upper_limit_mm": -9.7},
             "provenance": ["runtime#stack-worst"]},
            {"id": "stack-rss", "op": "tolerance.stack.check",
             "params": {"stack": "stack", "method": "rss",
                        "lower_limit_mm": -10.25, "upper_limit_mm": -9.75},
             "provenance": ["runtime#stack-rss"]},
        ],
        "checks": [
            {"kind": "rebuild", "target": "face-fit"},
            {"kind": "tolerance_stack", "target": "stack-worst"},
            {"kind": "tolerance_stack", "target": "stack-rss"},
        ],
    }


def run() -> None:
    document = App.newDocument("TopologyToleranceRuntime")
    receipt = execute_program(document, program())
    face_mate = document.getObject("DS_CAD_face_fit")
    edge_mate = document.getObject("DS_CAD_edge_fit")
    clearance = document.getObject("DS_CAD_face_clearance")
    worst = document.getObject("DS_CAD_stack_worst")
    rss = document.getObject("DS_CAD_stack_rss")
    assert receipt["solid_valid"] is True, receipt
    assert face_mate.SolveStatus == "solved", face_mate.ResultJSON
    assert edge_mate.SolveStatus == "solved", edge_mate.ResultJSON
    assert clearance.Passed is True, clearance.ResultJSON
    assert worst.Passed is True, worst.ResultJSON
    assert rss.Passed is True, rss.ResultJSON
    assert abs(json.loads(worst.ResultJSON)["nominal_mm"] + 10.0) < 1.0e-9, worst.ResultJSON
    base_component = document.getObject("DS_CAD_base_component")
    cover_component = document.getObject("DS_CAD_cover_component")
    base_face = base_component.Shape.Faces[5]
    cover_face = cover_component.Shape.Faces[4]
    assert abs((cover_face.CenterOfMass - base_face.CenterOfMass).z - 2.0) < 1.0e-6
    assert base_face.normalAt(0, 0).dot(cover_face.normalAt(0, 0)) < -0.999999
    clearance_result = json.loads(clearance.ResultJSON)
    assert abs(clearance_result["distance_mm"] - 2.0) < 1.0e-6, clearance.ResultJSON
    edge_a = document.getObject("DS_CAD_edge_a").Shape.Edges[0]
    edge_b = document.getObject("DS_CAD_edge_b").Shape.Edges[0]
    assert (edge_a.CenterOfMass - edge_b.CenterOfMass).Length < 1.0e-6
    assert abs(edge_a.tangentAt(0.5).dot(edge_b.tangentAt(0.5))) > 0.999999
    App.closeDocument(document.Name)

    stale = copy.deepcopy(program())
    stale["commands"][7]["params"]["second_reference"]["subelement"] = "Face999"
    document = App.newDocument("TopologyToleranceFailure")
    try:
        execute_program(document, stale)
        raise AssertionError("stale topology reference was accepted")
    except MechanicalCadProgramError:
        assert not document.Objects, [obj.Name for obj in document.Objects]
    finally:
        App.closeDocument(document.Name)
    print("TOPOLOGY_TOLERANCE_RUNTIME_OK")


if __name__ == "__main__":
    run()

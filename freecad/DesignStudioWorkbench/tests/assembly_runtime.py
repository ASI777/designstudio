#!/usr/bin/env python3
"""Exercise native assembly links, mates, clearance and interference checks."""
from __future__ import annotations

import copy
from pathlib import Path
import sys

import FreeCAD as App

ROOT = Path(__file__).resolve().parents[3]
WORKBENCH = ROOT / "freecad" / "DesignStudioWorkbench"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(WORKBENCH))

from DesignStudio.mechanical_cad import (  # noqa: E402
    MechanicalCadProgramError,
    execute_program,
)


def program():
    return {
        "schema": "design-studio.mechanical-cad-program/1",
        "program_id": "assembly-fit-fixture",
        "units": "mm",
        "author": "runtime-test",
        "commands": [
            {"id": "housing", "op": "part.box",
             "params": {"length_mm": 10, "width_mm": 10, "height_mm": 10},
             "provenance": ["runtime#housing"]},
            {"id": "insert", "op": "part.box",
             "params": {"length_mm": 10, "width_mm": 10, "height_mm": 10},
             "provenance": ["runtime#insert"]},
            {"id": "assembly", "op": "assembly.create",
             "params": {"name": "Fit fixture assembly"},
             "provenance": ["runtime#assembly"]},
            {"id": "housing-component", "op": "assembly.component",
             "params": {"assembly": "assembly", "source": "housing",
                        "component_ref": "HOUSING", "position_mm": [0, 0, 0]},
             "provenance": ["runtime#housing-component"]},
            {"id": "insert-component", "op": "assembly.component",
             "params": {"assembly": "assembly", "source": "insert",
                        "component_ref": "INSERT", "position_mm": [20, 0, 0]},
             "provenance": ["runtime#insert-component"]},
            {"id": "fit-mate", "op": "assembly.mate",
             "params": {"assembly": "assembly", "first": "housing-component",
                        "second": "insert-component", "kind": "distance",
                        "direction": [1, 0, 0], "distance_mm": 20},
             "provenance": ["runtime#fit-mate"]},
            {"id": "clearance", "op": "assembly.check_clearance",
             "params": {"assembly": "assembly", "first": "housing-component",
                        "second": "insert-component", "min_clearance_mm": 5},
             "provenance": ["runtime#clearance"]},
            {"id": "interference", "op": "assembly.check_interference",
             "params": {"assembly": "assembly", "first": "housing-component",
                        "second": "insert-component"},
             "provenance": ["runtime#interference"]},
        ],
        "checks": [
            {"kind": "solid_count", "target": "housing-component"},
            {"kind": "rebuild", "target": "clearance"},
        ],
    }


def run() -> None:
    document = App.newDocument("AssemblyRuntime")
    receipt = execute_program(document, program())
    assert receipt["solid_valid"] is True, receipt
    assert document.getObject("DS_CAD_clearance").Passed is True
    assert document.getObject("DS_CAD_interference").Passed is True
    assembly = document.getObject("DS_CAD_assembly")
    assert assembly.TypeId == "App::Part"
    assert len(assembly.Group) == 5, [item.Name for item in assembly.Group]
    assert document.getObject("DS_CAD_insert_component").Shape.BoundBox.XMin >= 20
    App.closeDocument(document.Name)

    bad = copy.deepcopy(program())
    bad["commands"] = [command for command in bad["commands"]
                       if command["op"] != "assembly.mate"]
    bad["commands"][4]["params"]["position_mm"] = [5, 0, 0]
    bad["commands"][-2]["params"]["min_clearance_mm"] = 1
    document = App.newDocument("AssemblyRuntimeFailure")
    try:
        execute_program(document, bad)
        raise AssertionError("interfering assembly was accepted")
    except MechanicalCadProgramError:
        assert not document.Objects, [obj.Name for obj in document.Objects]
    finally:
        App.closeDocument(document.Name)
    print("ASSEMBLY_RUNTIME_OK")


if __name__ == "__main__":
    run()

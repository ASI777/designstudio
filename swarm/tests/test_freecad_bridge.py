#!/usr/bin/env python3
"""Tests for the FreeCAD-independent mechanical-contract boundary."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
WB = ROOT / "freecad" / "DesignStudioWorkbench"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(WB))

from DesignStudio.mechanical_contract import (  # noqa: E402
    ContractError, apply_contract, derive_contract, validate_contract,
    write_contract_and_project,
)
from DesignStudio.freecad_adapter import component_collision_report  # noqa: E402
from swarm.memory.component_binding import bind_component  # noqa: E402


def snapshot():
    return {
        "source": {"kind": "freecad-document", "path": "enclosure.FCStd",
                   "document_id": "Enclosure", "sha256": "a" * 64},
        "frame_to_world": {"origin_mm": [10, 20, 30], "x_axis": [1, 0, 0],
                           "y_axis": [0, 1, 0], "z_axis": [0, 0, 1]},
        "board": {
            "outline_pts": [[0, 0], [80, 0], [80, 45], [0, 45]],
            "cutouts": [[[35, 18], [45, 18], [45, 27], [35, 27]]],
            "thickness_mm": 1.6, "z_range_mm": [0, 1.6],
            "mounting_holes": [{"id": "H1", "center_mm": [5, 5], "diameter_mm": 3.2}],
            "height_zones": [{"id": "lid", "name": "Low lid",
                              "outline_pts": [[0, 0], [30, 0], [30, 10], [0, 10]],
                              "max_height_mm": 2.5}],
            "cooling_zones": [{"id": "fan", "name": "Fan airflow",
                               "outline_pts": [[50, 5], [75, 5], [75, 25], [50, 25]],
                               "keep_clear": True}],
            "service_clearances": [{"id": "usb_mate", "name": "USB mating",
                                    "outline_pts": [[74, 30], [80, 30], [80, 42], [74, 42]]}],
        },
        "fixed_items": [{"id": "switch", "name": "Switch body", "ref": "SW1",
                         "x_mm": 10, "y_mm": 35, "rot_deg": 0,
                         "keepout_outline_pts": [[5, 30], [15, 30], [15, 40], [5, 40]]}],
        "connector_locations": [{"id": "usb", "name": "USB-C", "ref": "J1",
                                 "x_mm": 78, "y_mm": 36, "rot_deg": 90,
                                 "edge_anchor": "right"}],
    }


def base_project():
    return {
        "version": 2, "board_width_mm": 100, "board_height_mm": 80,
        "footprints": [{"ref": "J1", "x_mm": 1, "y_mm": 1, "rot_deg": 0,
                        "placement": {"functional_group": "external"}},
                       {"ref": "U1", "x_mm": 30, "y_mm": 30}],
        "traces": [{"ax_mm": 1, "ay_mm": 1, "bx_mm": 2, "by_mm": 2, "net": 0}],
        "vias": [], "net_table": [{"id": 0, "name": "VBUS", "class": 3}],
        "net_classes": [{"id": 3, "name": "Power", "trace_width_mm": 1.0}],
        "rule_areas": [{"id": 7, "name": "User keepout", "source": "project",
                        "pts": [[1, 1], [2, 1], [2, 2], [1, 2]]}],
        "schematic": {"symbols": [{"ref": "U1"}], "wires": []},
    }


def test_contract_is_locked_and_tamper_evident():
    contract = derive_contract(snapshot(), revision=4)
    validate_contract(contract)
    assert contract["revision"] == 4 and contract["status"] == "locked"
    tampered = copy.deepcopy(contract)
    tampered["board"]["outline_pts"][1][0] = 81
    try:
        validate_contract(tampered)
    except ContractError as exc:
        assert "digest mismatch" in str(exc)
    else:
        raise AssertionError("tampered mechanical contract was accepted")


def test_contract_applies_geometry_rules_and_locked_connector():
    contract = derive_contract(snapshot())
    result = apply_contract(base_project(), contract, "board.mechanical-contract.json")
    assert result["board_width_mm"] == 80 and result["board_height_mm"] == 45
    assert len(result["board_cutouts"]) == 2  # face cutout + mounting-hole circle
    assert result["schematic"] == base_project()["schematic"]
    assert result["net_classes"] == base_project()["net_classes"]
    assert result["traces"] == base_project()["traces"]
    assert any(a["name"] == "User keepout" for a in result["rule_areas"])
    assert sum(a.get("source") == "freecad-mechanical-contract"
               for a in result["rule_areas"]) == 4
    j1 = next(fp for fp in result["footprints"] if fp["ref"] == "J1")
    assert (j1["x_mm"], j1["y_mm"], j1["rot_deg"]) == (78.0, 36.0, 90.0)
    assert j1["placement"]["locked"] is True
    assert j1["placement"]["edge_anchor"] == "right"


def test_atomic_sidecar_and_project_sync():
    contract = derive_contract(snapshot())
    with tempfile.TemporaryDirectory() as tmp:
        project_path = Path(tmp) / "controller.dsproj"
        project_path.write_text(json.dumps(base_project()), encoding="utf-8")
        written_project, contract_path = write_contract_and_project(project_path, contract)
        result = json.loads(written_project.read_text(encoding="utf-8"))
        sidecar_bytes = contract_path.read_bytes()
        assert result["mechanical_contract"]["contract_digest"] == contract["contract_digest"]
        assert result["verification_requirements"]["enclosure_evidence_path"] == contract_path.name
        assert result["verification_requirements"]["enclosure_evidence_sha256"] == \
            hashlib.sha256(sidecar_bytes).hexdigest()
        assert not list(Path(tmp).glob("*.tmp"))


def test_freecad_solid_collision_report_is_project_bound():
    class Common:
        def __init__(self, volume): self.Volume = volume

    class Shape:
        def __init__(self, name, overlaps=()):
            self.name = name
            self.overlaps = set(overlaps)

        def common(self, other):
            return Common(2.5 if other.name in self.overlaps else 0.0)

    class View:
        Visibility = True

    class Obj:
        def __init__(self, name, ref, role="none", overlaps=()):
            self.Name = name; self.RefDes = ref; self.DesignStudioRole = role
            self.Shape = Shape(name, overlaps); self.ViewObject = View()

    c1 = Obj("DS_U1", "U1", overlaps=("DS_U2", "Obstacle"))
    c2 = Obj("DS_U2", "U2")
    own_fixed = Obj("U1Envelope", "U1", "fixed_item")
    obstacle = Obj("Obstacle", "MECH1", "fixed_item")
    group = type("Group", (), {"Group": [c1, c2]})()
    document = type("Document", (), {
        "Objects": [c1, c2, own_fixed, obstacle],
        "getObject": lambda self, name: group if name == "DesignStudioComponents" else None,
    })()
    project = {"document_id": "doc-1", "revision": 7,
               "mechanical_contract": {"contract_digest": "d" * 64}}
    project_bytes = json.dumps(project).encode()
    report = component_collision_report(document, project, project_bytes)
    assert report["status"] == "fail"
    assert {(item["a"], item["b"]) for item in report["collisions"]} == {
        ("U1", "U2"), ("U1", "MECH1")}
    assert report["project"]["file_sha256"] == hashlib.sha256(project_bytes).hexdigest()
    assert report["contract_digest"] == "d" * 64


def test_harness_board_build_preserves_freecad_authority():
    import swarm.memory.apply_to_board as board_apply

    contract = derive_contract(snapshot())
    base = apply_contract(base_project(), contract, "board.mechanical-contract.json")
    component = {
        "schema": "design-studio.component/2",
        "component": {"mpn": "USB-TEST", "manufacturer": "Fixture"},
        "symbol": {"pins": [{"number": "1", "name": "VBUS"},
                            {"number": "2", "name": "GND"}]},
        "electrical": {"power_domains": [], "required_externals": []},
        "footprint": {"name": "USB_FIXTURE", "mount": "smd",
                      "body": {"width_mm": 8, "length_mm": 6},
                      "pads": [{"number": "1", "x_mm": -1, "y_mm": 0,
                                "width_mm": 1, "height_mm": 1},
                               {"number": "2", "x_mm": 1, "y_mm": 0,
                                "width_mm": 1, "height_mm": 1}]},
        "package_3d": {"height_mm": 3.2},
    }
    old_resolver = board_apply.resolve_component
    old_bound_resolver = board_apply.resolve_bound_component
    with tempfile.TemporaryDirectory() as tmp:
        step = Path(tmp) / "USB-TEST.step"
        step.write_text("ISO-10303-21;\nHEADER;ENDSEC;DATA;ENDSEC;END-ISO-10303-21;\n")
        binding = bind_component(component, step, alignment_status="verified", model_mpn="USB-TEST")
        reference = {"schema": "design-studio.bound-component-ref/1",
                     "binding_id": binding["binding_id"],
                     "binding_digest": binding["binding_digest"],
                     "record_uri": "fixture.bound.json", "model_3d": binding["model_3d"]}
        board_apply.resolve_component = lambda mpn: component if mpn == "USB-TEST" else None
        board_apply.resolve_bound_component = lambda mpn: (binding, reference) \
            if mpn == "USB-TEST" else None
        try:
            result = board_apply.build_dsproj(
                [{"ref": "J1", "mpn": "USB-TEST"}],
                [{"ref": "J1", "pin": "1", "net": "VBUS"},
                 {"ref": "J1", "pin": "2", "net": "GND"}], base=base,
                incremental_analysis={"schematic": {"symbols": [{
                    "ref": "J1", "lib": "component:USB-TEST", "x_mm": 25,
                    "y_mm": 30, "rot_deg": 0, "unit": 1,
                    "pins": [{"num": "1", "name": "VBUS", "etype": "power_in",
                              "side": 2, "order": 0, "net_name": "VBUS"},
                             {"num": "2", "name": "GND", "etype": "power_in",
                              "side": 3, "order": 0, "net_name": "GND"}]}], "wires": []}})
        finally:
            board_apply.resolve_component = old_resolver
            board_apply.resolve_bound_component = old_bound_resolver
    assert result["board_width_mm"] == 80 and result["board_height_mm"] == 45
    assert result["board_outline_pts"] == contract["board"]["outline_pts"]
    assert result["mechanical_contract"]["contract_digest"] == contract["contract_digest"]
    assert result["net_classes"] == base["net_classes"]
    j1 = result["footprints"][0]
    assert j1["placement"]["locked"] is False
    assert j1["placement"]["edge_anchor"] == "right"
    assert 0 <= j1["x_mm"] <= result["board_width_mm"]
    assert 0 <= j1["y_mm"] <= result["board_height_mm"]
    assert result["schematic"]["symbols"][0]["x_mm"] == 25
    assert result["schematic"]["symbols"][0]["pins"][0]["net"] == 0
    assert "net_name" not in result["schematic"]["symbols"][0]["pins"][0]

    tampered = copy.deepcopy(base)
    tampered["mechanical_contract"]["board"]["outline_pts"][1][0] = 90
    try:
        board_apply.build_dsproj([], [], base=tampered)
    except ValueError as exc:
        assert "digest mismatch" in str(exc)
    else:
        raise AssertionError("harness accepted a tampered FreeCAD contract")


def test_locked_board_accepts_approved_published_footprint_without_step_binding():
    import swarm.memory.apply_to_board as board_apply

    contract = derive_contract(snapshot())
    base = apply_contract(base_project(), contract, "board.mechanical-contract.json")
    component = {
        "schema": "design-studio.component/2",
        "component": {"mpn": "AUTO-SMT-1", "manufacturer": "Fixture"},
        "symbol": {"pins": [
            {"number": "1", "name": "IN", "electrical_type": "input"},
            {"number": "2", "name": "GND", "electrical_type": "power_in"},
        ]},
        "footprint": {"name": "SOT_AUTO", "mount": "smd", "generated": "ipc7351",
                      "pads": [
                          {"number": "1", "x_mm": -0.5, "y_mm": 0,
                           "width_mm": 0.4, "height_mm": 0.8},
                          {"number": "2", "x_mm": 0.5, "y_mm": 0,
                           "width_mm": 0.4, "height_mm": 0.8},
                      ]},
        "extraction": {"provider": "codex-cli", "verification": {
            "state": "approved", "placement_allowed": True,
            "blockers": [], "warnings": []}},
        "preview": {"state": "published"},
    }
    old_resolver = board_apply.resolve_component
    old_bound_resolver = board_apply.resolve_bound_component
    board_apply.resolve_component = lambda mpn: component if mpn == "AUTO-SMT-1" else None
    board_apply.resolve_bound_component = lambda _mpn: None
    try:
        result = board_apply.build_dsproj(
            [{"ref": "U1", "mpn": "AUTO-SMT-1"}],
            [{"ref": "U1", "pin": "1", "net": "IN"},
             {"ref": "U1", "pin": "2", "net": "GND"}], base=base)
    finally:
        board_apply.resolve_component = old_resolver
        board_apply.resolve_bound_component = old_bound_resolver
    assert len(result["footprints"]) == 1
    assert "bound_component" not in result["footprints"][0]
    assert result["unresolved_components"] == []


if __name__ == "__main__":
    test_contract_is_locked_and_tamper_evident()
    test_contract_applies_geometry_rules_and_locked_connector()
    test_atomic_sidecar_and_project_sync()
    test_freecad_solid_collision_report_is_project_bound()
    test_harness_board_build_preserves_freecad_authority()
    test_locked_board_accepts_approved_published_footprint_without_step_binding()
    print("FreeCAD bridge tests passed")

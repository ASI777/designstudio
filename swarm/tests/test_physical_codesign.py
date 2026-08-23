#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from swarm.memory.physical_codesign import (
    CoDesignError,
    assess_component_physical_assets,
    approve_component,
    approve_island_plan,
    create_selection_plan,
    create_topology_study,
    derive_mechanical_requirements,
    digest_record,
    rank_component_candidates,
    score_routing,
    topology_source_from_project,
    validate_document,
)


def expect_failure(fn, phrase: str = ""):
    try:
        fn()
    except (CoDesignError, jsonschema.ValidationError) as exc:
        if phrase:
            assert phrase in str(exc), str(exc)
        return
    raise AssertionError("invalid co-design input was accepted")


def component_plan():
    return create_selection_plan(
        plan_id="controller-components", product_id="agent-workflow-controller",
        target_quantity=25, market="IN",
        requirements=[{
            "requirement_id": "main-mcu", "role": "mcu", "quantity": 1,
            "critical": True,
            "hard_constraints": {"supply_v": 3.3, "interface": "USB"},
            "preferences": {"reliability": 1.0},
            "maximum_bounds_mm": [12, 12, 2], "allowed_packages": ["QFN-48"],
        }])


def raw_candidate(candidate_id: str, score: float, *, hard_status: str = "pass",
                  incomplete_placeholder: float = 999999.0):
    hard = {name: {"status": hard_status, "reason": "fixture evidence"}
            for name in ("electrical_compatibility", "package_identity",
                         "mechanical_fit", "datasheet_identity")}
    return {
        "candidate_id": candidate_id, "mpn": candidate_id.upper(),
        "manufacturer": "Fixture Semiconductor", "hard_gates": hard,
        "metrics": {
            "evidence_completeness": {"status": "pass", "value": score, "source": "fixture"},
            "stock_coverage": {"status": "pass", "value": score, "source": "fixture"},
            "cost_preference": {"status": "incomplete", "value": incomplete_placeholder},
        },
        "datasheet": {"status": "exact", "sha256": "a" * 64},
        "assets": {"footprint": "verified", "step": "verified"}, "offers": [],
    }


def test_schemas():
    for path in sorted((ROOT / "docs" / "schemas").glob("*.schema.json")):
        if path.name in {
            "component-selection-plan-v1.schema.json", "component-candidate-set-v1.schema.json",
            "component-decision-v1.schema.json", "pcb-topology-study-v1.schema.json",
            "pcb-island-plan-v1.schema.json", "mechanical-component-requirements-v1.schema.json",
        }:
            jsonschema.Draft202012Validator.check_schema(json.loads(path.read_text()))
    component_schema = json.loads((ROOT / "docs" / "datasheet-extractor" /
                                   "component.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(component_schema)


def test_datasheet_asset_readiness():
    provenance = ["datasheet:" + "a" * 64 + ":page:12:package"]
    outline = {"points_mm": [[-2, -2], [2, -2], [2, 2], [-2, 2]],
               "provenance": provenance}
    component = {
        "component": {"manufacturer": "Fixture", "mpn": "QFN-1", "category": "mcu"},
        "evidence": {"mpn_match": "exact", "sha256": "a" * 64},
        "footprint": {
            "mount": "smd", "body": {"length_mm": 4, "width_mm": 4, "height_mm": 0.9},
            "pads": [{"number": "1", "x_mm": -1.8, "y_mm": 0,
                      "width_mm": 0.6, "height_mm": 0.3}],
            "recommended_land_pattern": {"status": "verified", "provenance": provenance},
            "pin_one_orientation": {"pad_number": "1", "direction_deg": 180,
                                    "provenance": provenance},
            "outlines": {name: copy.deepcopy(outline)
                         for name in ("silkscreen", "fabrication", "assembly")},
            "solder_mask_expansion_mm": 0.05, "paste_reduction_mm": 0.02,
        },
        "package_3d": {"step_asset": {"format": "step", "sha256": "b" * 64,
                                       "solid_count": 1, "roundtrip_valid": True}},
    }
    ready = assess_component_physical_assets(component)
    assert ready["status"] == "verified" and not ready["raster_geometry_authority"]
    incomplete = copy.deepcopy(component)
    incomplete["footprint"].pop("recommended_land_pattern")
    incomplete["footprint"]["pads"][0]["width_mm"] = 999  # pixels/guesses cannot repair evidence
    report = assess_component_physical_assets(incomplete)
    assert report["status"] == "incomplete"
    assert report["checks"]["manufacturer_land_pattern"]["status"] == "incomplete"
    mismatch = copy.deepcopy(component)
    mismatch["evidence"]["mpn_match"] = "mismatch"
    assert assess_component_physical_assets(mismatch)["status"] == "rejected"


def test_component_ranking_and_approval():
    plan = component_plan()
    candidate_set = rank_component_candidates(plan, "main-mcu", [
        raw_candidate("reliable-mcu", 0.95), raw_candidate("weak-mcu", 0.50),
        raw_candidate("wrong-package", 1.0, hard_status="fail"),
    ])
    validate_document(candidate_set)
    ranked = sorted((item for item in candidate_set["candidates"] if item["rank"]),
                    key=lambda item: item["rank"])
    assert [item["candidate_id"] for item in ranked] == ["reliable-mcu", "weak-mcu"]
    rejected = next(item for item in candidate_set["candidates"]
                    if item["candidate_id"] == "wrong-package")
    assert rejected["status"] == "rejected" and rejected["score"] is None

    changed_placeholder = rank_component_candidates(plan, "main-mcu", [
        raw_candidate("reliable-mcu", 0.95, incomplete_placeholder=-12345),
        raw_candidate("weak-mcu", 0.50, incomplete_placeholder=0),
    ])
    assert [(item["candidate_id"], item["score"], item["rank"])
            for item in candidate_set["candidates"] if item["candidate_id"] != "wrong-package"] == [
                (item["candidate_id"], item["score"], item["rank"])
                for item in changed_placeholder["candidates"]]

    decision = approve_component(candidate_set, "reliable-mcu", approved_by="fixture-user",
                                 rationale="best verified reliability")
    validate_document(decision)
    expect_failure(lambda: approve_component(candidate_set, "wrong-package",
                                              approved_by="fixture-user", rationale="invalid"),
                   "valid candidate")
    stale = copy.deepcopy(candidate_set)
    stale["candidates"][0]["mpn"] = "MUTATED"
    expect_failure(lambda: approve_component(stale, "reliable-mcu",
                                             approved_by="fixture-user", rationale="stale"), "stale")


def topology_source(distributed=False, moving=False):
    return {
        "product_id": "agent-workflow-controller",
        "components": [
            {"component_id": "mcu", "functional_group": "logic", "position_mm": [50, 45, 2]},
            {"component_id": "display", "functional_group": "interface", "position_mm": [80, 20, 6]},
            {"component_id": "left-controls", "functional_group": "left-grip", "position_mm": [20, 60, 8]},
            {"component_id": "right-controls", "functional_group": "right-grip", "position_mm": [140, 60, 8]},
        ],
        "nets": [
            {"net_id": "spi", "component_ids": ["mcu", "display"], "criticality_weight": 2.0},
            {"net_id": "left-input", "component_ids": ["mcu", "left-controls"], "criticality_weight": 1.0},
            {"net_id": "right-input", "component_ids": ["mcu", "right-controls"], "criticality_weight": 1.0},
        ],
        "constraints": {"distributed_surfaces": distributed, "moving_crossing": moving,
                        "curvature_required": False, "flex_allowed": True, "harness_allowed": True},
    }


def test_topology_and_island_approval():
    compact = create_topology_study(topology_source())
    assert len(compact["candidates"]) == 3
    assert next(item for item in compact["candidates"] if item["topology"] == "single_rigid")["rank"] == 1
    for candidate in compact["candidates"]:
        assert candidate["metrics"]["thermal"]["value"] is None
        assert "thermal" in candidate["incomplete_analyses"]

    distributed = create_topology_study(topology_source(distributed=True, moving=True))
    single = next(item for item in distributed["candidates"] if item["topology"] == "single_rigid")
    assert single["status"] == "rejected"
    selected = min((item for item in distributed["candidates"] if item["status"] != "rejected"),
                   key=lambda item: item["rank"])
    details = {island["island_id"]: {"geometry_digest": "b" * 64,
               "stackup": {"copper_layers": 4}, "bounds_mm": {"verified": True},
               "connectors": []}
               for island in selected["islands"]}
    expect_failure(lambda: approve_island_plan(distributed, selected["candidate_id"],
        approved_by="fixture-user", rationale="missing link evidence", island_details=details),
        "interconnect evidence")
    link_details = {}
    for link in selected["interconnects"]:
        link_details[link["interconnect_id"]] = {
            "connector_pin_map": [
                {"island_id": island_id, "connector_id": f"connector-{index + 1}", "pin": "1"}
                for index, island_id in enumerate(link["island_ids"])],
            "route_length_mm": 42.0, "minimum_bend_radius_mm": 5.0,
            "estimated_voltage_drop_v": 0.02, "signal_integrity_status": "pass",
            "route_geometry_digest": "c" * 64, "evidence_digest": "d" * 64,
        }
    plan = approve_island_plan(distributed, selected["candidate_id"], approved_by="fixture-user",
                               rationale="distributed controls require separated regions",
                               island_details=details, interconnect_details=link_details)
    validate_document(plan)
    assert all(link["connector_pin_map"] and link["route_length_mm"] == 42.0
               for link in plan["interconnects"])
    bad = copy.deepcopy(details)
    bad[next(iter(bad))]["geometry_digest"] = "unverified"
    expect_failure(lambda: approve_island_plan(distributed, selected["candidate_id"],
        approved_by="fixture-user", rationale="bad", island_details=bad), "geometry digest")


def test_measured_octilinear_routing():
    good = score_routing({
        "nets": [{"net_id": "usb-dp", "high_speed": True, "criticality_weight": 4,
                  "required": True}],
        "segments": [
            {"net_id": "usb-dp", "layer": 0, "a_mm": [0, 0], "b_mm": [5, 5]},
            {"net_id": "usb-dp", "layer": 0, "a_mm": [5, 5], "b_mm": [10, 5]},
        ], "vias": []})
    assert good["status"] == "pass" and good["weighted_actual_length_mm"] > 0
    right_angle = score_routing({
        "nets": [{"net_id": "clock", "high_speed": True, "required": True}],
        "segments": [
            {"net_id": "clock", "layer": 0, "a_mm": [0, 0], "b_mm": [5, 0]},
            {"net_id": "clock", "layer": 0, "a_mm": [5, 0], "b_mm": [5, 5]},
        ], "vias": []})
    assert right_angle["status"] == "fail"
    assert any("right-angle" in failure for failure in right_angle["failures"])
    off_angle = score_routing({
        "nets": [{"net_id": "signal", "required": True}],
        "segments": [{"net_id": "signal", "a_mm": [0, 0], "b_mm": [4, 3]}],
        "vias": []})
    assert off_angle["status"] == "fail"
    assert any("45-degree" in failure for failure in off_angle["failures"])


def test_mechanical_requirements_fail_closed():
    source = {
        "product_id": "agent-workflow-controller",
        "pcb_islands": [{"island_id": "logic"}, {"island_id": "left-grip"}],
        "loads": {"assembly_load_n": 80, "safety_factor": 2.5},
        "mechanisms": [{"kind": "revolute", "interfaces": ["shaft", "housing"],
                        "radial_load_n": 120, "speed_rpm": 600, "life_hours": 10000}],
        "heat_sources": [{"component_id": "regulator", "power_w": 4.0}],
        "thermal": {"ambient_c": 40, "maximum_component_c": 90},
        "cables": [{"cable_id": "usb"}],
    }
    requirements = derive_mechanical_requirements(source)
    validate_document(requirements)
    fastener = next(item for item in requirements["requirements"] if item["kind"] == "fastener")
    assert fastener["screening"]["required_capacity_per_fastener_n"] == 50.0
    bearing = next(item for item in requirements["requirements"] if item["kind"] == "bearing")
    assert bearing["screening"]["required_dynamic_rating_n"] > 0
    strain = next(item for item in requirements["requirements"] if item["kind"] == "strain_relief")
    assert strain["selection_status"] == "blocked"
    assert requirements["analysis_summary"]["structural"] == "incomplete"


def test_agent_workflow_controller_fixture():
    fixture = (
        ROOT
        / "testdata"
        / "work-package-fixture"
        / "acceptance"
        / "agent-workflow-controller"
        / "generated"
        / "agent-workflow-controller.dsproj"
    )
    project = json.loads(fixture.read_text())
    source = topology_source_from_project(project, distributed_surfaces=True)
    assert len(source["components"]) == len(project["footprints"])
    assert source["candidate_evidence"]["single_rigid"]["routing"]["segments"]
    study = create_topology_study(source, study_id="agent-workflow-controller-topology")
    assert len(study["candidates"]) == 3 and study["approval_required"]
    single = next(item for item in study["candidates"] if item["topology"] == "single_rigid")
    assert single["status"] == "rejected"
    assert any("45-degree" in failure or "right-angle" in failure
               for failure in single["hard_failures"])
    assert all("thermal" in candidate["incomplete_analyses"]
               for candidate in study["candidates"])


def test_schema_negative():
    plan = component_plan()
    invalid = copy.deepcopy(plan)
    invalid["ranking_policy"] = "invent_missing_values"
    invalid["plan_digest"] = digest_record(invalid, "plan_digest")
    expect_failure(lambda: validate_document(invalid), "reliability_first")


if __name__ == "__main__":
    test_schemas()
    test_datasheet_asset_readiness()
    test_component_ranking_and_approval()
    test_topology_and_island_approval()
    test_measured_octilinear_routing()
    test_mechanical_requirements_fail_closed()
    test_agent_workflow_controller_fixture()
    test_schema_negative()
    print("PHYSICAL_CODESIGN_CONTRACT_TESTS_OK")

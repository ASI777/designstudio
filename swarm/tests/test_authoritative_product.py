#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import sys

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from swarm.memory.authoritative_product import (  # noqa: E402
    ProductCompileError,
    compile_product,
    validate_bundle,
)


FIXTURE = ROOT / "acceptance/authoritative-seven-axis-robot/source/engineering-requirements.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def write_candidate(root: Path, value: dict) -> Path:
    path = root / "requirements.json"
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return path


def expect_compile_failure(value: dict, expected: str) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        requirements = write_candidate(root, value)
        output = root / "output"
        try:
            compile_product(requirements, output)
        except ProductCompileError as error:
            assert expected in str(error), error
        else:
            raise AssertionError(f"expected product compile failure containing {expected!r}")
        if output.exists():
            assert not list(output.iterdir()), "failed compilation left partial assets"


def test_fixture_compiles_to_deterministic_cross_domain_bundle() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "bundles"
        first = compile_product(FIXTURE, output)
        second = compile_product(FIXTURE, output)
        assert first == second
        manifest = validate_bundle(first)
        assert manifest["product_id"] == "ds-cobot-r7"
        assert manifest["generation_policy"] == "validated_inputs_atomic_publication"
        assert len(manifest["artifacts"]) == 39
        assert len(list(output.iterdir())) == 1
        assert not list(output.glob(".*.stage-*"))

        bundle = first.parent
        rendered = "\n".join(
            path.read_text(encoding="utf-8") for path in bundle.rglob("*.json")
        ).lower()
        assert "release_gate" not in rendered
        assert "release authority" not in rendered

        evidence = json.loads((bundle / "evidence/design-evidence.json").read_text())
        assert len(evidence["sources"]) == 4
        assert all(source["geometry_copy_allowed"] is False for source in evidence["sources"])
        assert all(source["dimensional_measurement_allowed"] is False
                   for source in evidence["sources"])

        mechanics = json.loads((bundle / "mechanical/mechanical-definition.json").read_text())
        assert mechanics["axis_count"] == 7
        assert mechanics["hidden_fastener_policy"]["externally_visible_fastener_count"] == 0
        assert sum(part["role"] == "internal_fastener"
                   for part in mechanics["semantic_parts"]) == 7
        assert sum(part["role"] == "pcb" for part in mechanics["semantic_parts"]) == 15
        assert sum(part["role"] == "harness" for part in mechanics["semantic_parts"]) == 7
        assert all(part.get("fastener_exposure") != "external"
                   for part in mechanics["semantic_parts"])
        assert len(mechanics["harness_channel_checks"]) == 7
        assert all(item["status"] == "pass" for item in mechanics["harness_channel_checks"])
        cad_program = json.loads(
            (bundle / "mechanical/mechanical-cad-program.json").read_text()
        )
        command_by_id = {item["id"]: item for item in cad_program["commands"]}
        assert command_by_id["base-shell"]["op"] == "feature.cut"
        assert len(mechanics["joint_packaging"]) == 7
        for index in range(1, 8):
            assert command_by_id[f"j{index}-hub"]["op"] == "feature.cut"
            assert command_by_id[f"j{index}-structural-core"]["op"] == "feature.cut"
            assert command_by_id[f"j{index}-cover"]["op"] == "feature.cut"
            assert command_by_id[f"j{index}-link-shell"]["op"] == "feature.cut"
            assert command_by_id[f"j{index}-cover-outgoing-port"]["op"] in {
                "part.cone", "feature.fuse"}
            if index > 1:
                assert command_by_id[f"j{index}-cover-incoming-port"]["op"] in {
                    "part.cone", "feature.fuse"}
            packaging = mechanics["joint_packaging"][index - 1]
            assert packaging["electronics_bay_side"] in (-1, 1)
            assert packaging["joint_width_mm"] > packaging["hub_width_mm"]
            for board_role in ("logic", "power"):
                board = command_by_id[f"pcb-joint-{index:02d}-{board_role}"]
                assert board["op"] == "part.box"
                assert board["params"]["basis_z"] == packaging["basis_z"]
                assert {"basis_x", "basis_y", "basis_z"} <= board["params"].keys()

        view = json.loads(
            (bundle / "industrial-design/exterior-and-view-policy.json").read_text()
        )
        assert view["default_view"] == "exterior"
        assert "internal_fastener" in view["view_modes"]["exterior"]["hidden_roles"]
        assert "keepout" in view["view_modes"]["exterior"]["hidden_roles"]
        assert "internal_structure" in view["view_modes"]["electronics_service"]["hidden_roles"]
        assert "internal_structure" in view["view_modes"]["structure"]["visible_roles"]

        electronics = json.loads(
            (bundle / "electronics/system-architecture.json").read_text()
        )
        assert electronics["board_count"] == 15
        assert len(electronics["boards"]) == 15
        smallest_joint_board = json.loads(
            (bundle / "electronics/boards/PCB-JOINT-07-LOGIC.json").read_text()
        )
        assert smallest_joint_board["radial_packaging_check"]["packaging_margin_mm"] > 0
        assert smallest_joint_board["radial_packaging_check"]["stack_depth_margin_mm"] > 0
        component_plan = json.loads(
            (bundle / "electronics/component-evidence-bindings.json").read_text()
        )
        component_plan_schema = json.loads(
            (ROOT / "docs/schemas/electronics-component-plan-v1.schema.json").read_text()
        )
        Draft202012Validator.check_schema(component_plan_schema)
        Draft202012Validator(component_plan_schema).validate(component_plan)
        assert component_plan["capability_receipts"][
            "mcu_package_accessible_fdcan_instances"] >= 2
        assert component_plan["capability_receipts"]["can_fd_transceiver_count"] == 2
        assert component_plan["capability_receipts"]["mosfet_vds_max_v"] >= component_plan[
            "capability_receipts"]["required_semiconductor_voltage_rating_v"]
        assert component_plan["capability_receipts"]["mosfet_voltage_margin_v"] >= 20.0
        assert component_plan["capability_receipts"]["mosfet_voltage_margin_ratio"] >= 1.3
        assert all(item["estimated_utilization"] <= item["maximum_estimated_utilization"]
                   for item in component_plan["placement_estimates"].values())
        assert component_plan["scope_limitations"], "candidate bindings must not imply routed hardware"
        joint_net = json.loads(
            (bundle / "electronics/joint-controller-net-contract.json").read_text()
        )
        joint_net_schema = json.loads(
            (ROOT / "docs/schemas/joint-electronics-net-contract-v1.schema.json").read_text()
        )
        Draft202012Validator.check_schema(joint_net_schema)
        Draft202012Validator(joint_net_schema).validate(joint_net)
        assert len(joint_net["exact_components"]) == 14
        assert sum(len(item["pins"]) for item in joint_net["exact_components"]) == 175
        assert joint_net["readiness"]["exact_active_component_pin_disposition"] == "pass"
        assert joint_net["readiness"]["fabrication_data"] == "incomplete"
        assert joint_net["unresolved_exact_bindings"]
        harness = json.loads((bundle / "electrical/harness.json").read_text())
        harness_schema = json.loads(
            (ROOT / "docs/schemas/robot-harness-definition-v2.schema.json").read_text()
        )
        Draft202012Validator.check_schema(harness_schema)
        Draft202012Validator(harness_schema).validate(harness)
        assert harness["external_wiring_visible_in_normal_operation"] is False
        assert harness["end_of_chain_voltage_drop_percent"] <= harness[
            "maximum_allowed_voltage_drop_percent"]
        assert harness["motion_qualification_status"] == "incomplete"
        assert all(item["route"] == "structural_core_centerline_electrical_length_model"
                   for item in harness["segments"])
        assert all(item["motion_route_status"].startswith("incomplete")
                   for item in harness["segments"])
        harness_motion = json.loads(
            (bundle / "electrical/harness-motion-contract.json").read_text()
        )
        harness_motion_schema = json.loads(
            (ROOT / "docs/schemas/harness-motion-contract-v1.schema.json").read_text()
        )
        Draft202012Validator.check_schema(harness_motion_schema)
        Draft202012Validator(harness_motion_schema).validate(harness_motion)
        assert harness_motion["overall_status"] == "incomplete"
        assert harness_motion["selected_cable"]["part_number"] is None
        assert harness_motion["provisionally_incompatible_radial_loop_joints"] == [
            "J3", "J4", "J5", "J6", "J7"]
        assert {item["finding_id"] for item in harness_motion["blocking_findings"]} == {
            "HARNESS-CABLE-001", "HARNESS-TRAVEL-001", "HARNESS-PACKAGING-001",
            "HARNESS-CAD-001", "HARNESS-TEST-001",
        }
        thermal = json.loads((bundle / "thermal/thermal-budget.json").read_text())
        thermal_schema = json.loads(
            (ROOT / "docs/schemas/robot-thermal-budget-v2.schema.json").read_text()
        )
        Draft202012Validator.check_schema(thermal_schema)
        Draft202012Validator(thermal_schema).validate(thermal)
        assert thermal["motor_winding_analysis_status"].startswith("incomplete")
        assert len(thermal["joint_budgets"]) == 7
        assert all(item["continuous_power_stage_loss_budget_w"]
                   >= item["efficiency_based_continuous_loss_w"]
                   for item in thermal["joint_budgets"])
        assert all(item["continuous_power_stage_loss_budget_w"]
                   >= item["component_screen_continuous_loss_w"]
                   for item in thermal["joint_budgets"])
        j7_thermal = next(item for item in thermal["joint_budgets"]
                          if item["joint_id"] == "J7")
        assert j7_thermal["loss_budget_basis"] == "component_loss_screen"
        assert j7_thermal["component_screen_continuous_loss_w"] \
            > j7_thermal["efficiency_based_continuous_loss_w"]
        coupled_thermal = json.loads(
            (bundle / "thermal/coupled-thermal-contract.json").read_text()
        )
        coupled_thermal_schema = json.loads(
            (ROOT / "docs/schemas/robot-coupled-thermal-contract-v1.schema.json").read_text()
        )
        Draft202012Validator.check_schema(coupled_thermal_schema)
        Draft202012Validator(coupled_thermal_schema).validate(coupled_thermal)
        assert coupled_thermal["power_stage_screen_result"] == "pass_candidate_paths"
        assert coupled_thermal["coupled_network_result"] \
            == "incomplete_exact_motor_and_path_data_missing"
        assert all(item["power_stage_junction_margin_degc"] >= 0
                   for item in coupled_thermal["joints"])
        assert all(item["motor_winding_temperature_degc"] is None
                   for item in coupled_thermal["joints"])
        integrity = json.loads(
            (bundle / "electronics/electrical-integrity.json").read_text()
        )
        integrity_schema = json.loads(
            (ROOT / "docs/schemas/robot-electrical-integrity-v1.schema.json").read_text()
        )
        Draft202012Validator.check_schema(integrity_schema)
        Draft202012Validator(integrity_schema).validate(integrity)
        assert integrity["signal_integrity"]["result"] \
            == "geometric_pass_full_channel_incomplete"
        assert all(channel["geometric_screen_result"] == "pass"
                   for channel in integrity["signal_integrity"]["channels"])
        assert integrity["power_integrity"]["dc_result"] == "pass"
        assert integrity["power_integrity"]["calculated_dynamic_bus_ripple_v"] is None
        assert integrity["emc"]["test_levels_and_operating_modes_bound"] is False
        functional_safety = json.loads(
            (bundle / "safety/functional-safety-contract.json").read_text()
        )
        functional_safety_schema = json.loads(
            (ROOT / "docs/schemas/robot-functional-safety-contract-v1.schema.json").read_text()
        )
        Draft202012Validator.check_schema(functional_safety_schema)
        Draft202012Validator(functional_safety_schema).validate(functional_safety)
        assert functional_safety["target_performance_level"] == "PL_d"
        assert functional_safety["target_architecture_category"] == "Category_3"
        assert functional_safety["compliance_claimed"] is False
        assert len(functional_safety["function_allocations"]) == 5
        assert all(item["achieved_performance_level"] is None
                   for item in functional_safety["function_allocations"])
        accuracy_controls = json.loads(
            (bundle / "controls/accuracy-and-controls.json").read_text()
        )
        accuracy_controls_schema = json.loads(
            (ROOT / "docs/schemas/robot-accuracy-controls-v1.schema.json").read_text()
        )
        Draft202012Validator.check_schema(accuracy_controls_schema)
        Draft202012Validator(accuracy_controls_schema).validate(accuracy_controls)
        assert accuracy_controls["reconstituted_tool_rms_budget_mm"] \
            == accuracy_controls["target_tool_repeatability_mm"]
        assert accuracy_controls["controls"]["mode_separation_result"] \
            == "pass_screening_only"
        assert accuracy_controls["controls"]["measured_command_to_pwm_latency_s"] is None
        assert all(item["exact_encoder_binding"] is None
                   for item in accuracy_controls["joints"])
        materials = json.loads(
            (bundle / "mechanical/material-screening-contract.json").read_text()
        )
        material_schema = json.loads(
            (ROOT / "docs/schemas/material-screening-contract-v1.schema.json").read_text()
        )
        Draft202012Validator.check_schema(material_schema)
        Draft202012Validator(material_schema).validate(materials)
        assert len(materials["materials"]) == 2
        assert materials["all_source_documents_digest_pinned"] is False
        assert materials["qualification_status"] == "incomplete"
        assert all(item["qualification"]["supplier_certificate_required"] is True
                   for item in materials["materials"])
        structural_screen = json.loads(
            (bundle / "mechanical/structural-link-screening.json").read_text()
        )
        structural_schema = json.loads(
            (ROOT / "docs/schemas/structural-link-screening-v1.schema.json").read_text()
        )
        Draft202012Validator.check_schema(structural_schema)
        Draft202012Validator(structural_schema).validate(structural_screen)
        assert len(structural_screen["links"]) == 7
        assert structural_screen["minimum_yield_margin"] > 1.0
        assert structural_screen["yield_screen_result"] == "pass"
        assert structural_screen["stiffness_acceptance_result"] \
            == "pass_via_system_screening"
        assert structural_screen["system_screening_reference"] \
            == "mechanical/robot-system-structural-screening.json"
        assert all(item["yield_screen_status"] == "pass"
                   for item in structural_screen["links"])
        assert all(item["stiffness_acceptance_status"] == "covered_by_system_model"
                   for item in structural_screen["links"])
        system_structural = json.loads(
            (bundle / "mechanical/robot-system-structural-screening.json").read_text()
        )
        system_structural_schema = json.loads(
            (ROOT / "docs/schemas/robot-system-structural-screening-v1.schema.json").read_text()
        )
        Draft202012Validator.check_schema(system_structural_schema)
        Draft202012Validator(system_structural_schema).validate(system_structural)
        assert system_structural["screening_result"] == "pass"
        assert system_structural["static_tool_deflection"]["result"] == "pass"
        assert system_structural["static_tool_deflection"]["magnitude_mm"] <= \
            system_structural["static_tool_deflection"]["maximum_allowed_mm"]
        assert system_structural["frequency_screen"]["result"] == "pass"
        assert system_structural["frequency_screen"]["minimum_estimate_hz"] >= \
            system_structural["frequency_screen"]["minimum_required_hz"]
        brake_sizing = json.loads(
            (bundle / "mechanical/robot-brake-sizing.json").read_text()
        )
        brake_schema = json.loads(
            (ROOT / "docs/schemas/robot-brake-sizing-v1.schema.json").read_text()
        )
        Draft202012Validator.check_schema(brake_schema)
        Draft202012Validator(brake_schema).validate(brake_sizing)
        assert len(brake_sizing["joints"]) == 7
        assert brake_sizing["sizing_result"] == "requirements_computed"
        assert brake_sizing["component_selection_result"] \
            == "incomplete_component_bindings_missing"
        assert brake_sizing["joints"][0]["governing_case"] \
            == "non_gravity_disturbance_floor"
        assert all(item["minimum_motor_side_brake_rating_nm"] > 0
                   for item in brake_sizing["joints"])
        fatigue = json.loads(
            (bundle / "mechanical/robot-fatigue-contract.json").read_text()
        )
        fatigue_schema = json.loads(
            (ROOT / "docs/schemas/robot-fatigue-contract-v1.schema.json").read_text()
        )
        Draft202012Validator.check_schema(fatigue_schema)
        Draft202012Validator(fatigue_schema).validate(fatigue)
        assert fatigue["target_motion_cycles"] == 10_000_000
        assert fatigue["spectrum_definition_status"] == "complete_design_duty_envelope"
        assert fatigue["life_prediction_result"] == "incomplete_required_evidence_missing"
        assert all(item["cumulative_miner_damage"] is None for item in fatigue["joints"])
        assert system_structural["method"]["modal_screen_is_eigenanalysis"] is False
        assert all(item["minimum_realized_wall_mm"] >= 1.5
                   for item in system_structural["link_sections"])

        evaluation = json.loads(
            (bundle / "verification/engineering-evaluation.json").read_text()
        )
        uncertainty = next(item for item in evaluation["computed_checks"]
                           if item["id"] == "explicit-uncertainty-budget")
        assert len(uncertainty["factors"]) == 5
        assert uncertainty["combined_factor"] > 1.0
        stiffness = next(item for item in evaluation["computed_checks"]
                         if item["id"] == "system-structural-stiffness-acceptance")
        assert stiffness["status"] == "pass"
        frequency = next(item for item in evaluation["computed_checks"]
                         if item["id"] == "system-structural-frequency-screen")
        assert frequency["status"] == "pass"
        assert frequency["method_is_eigenanalysis"] is False
        assert evaluation["qualification_work"], "unperformed physics must stay explicit"

        workflow = json.loads((bundle / "workflow/engineering-graph.json").read_text())
        workflow_schema = json.loads(
            (ROOT / "docs/schemas/engineering-workflow-graph-v1.schema.json").read_text()
        )
        Draft202012Validator.check_schema(workflow_schema)
        Draft202012Validator(workflow_schema).validate(workflow)
        node_ids = {node["id"] for node in workflow["nodes"]}
        assert {"domain_synthesis", "deterministic_validation", "repair_scoping", "targeted_repair",
                "physics_qualification", "atomic_publication"} <= node_ids
        assert any(edge["from"] == "targeted_repair"
                   and edge["to"] == "deterministic_validation"
                   for edge in workflow["edges"])
        assert workflow["controls"]["inference_budget_policy"] == "observe_only_no_hard_cap"


def test_visual_media_can_never_supply_dimensions_or_copied_geometry() -> None:
    candidate = load_fixture()
    candidate["visual_evidence"][0]["dimensional_measurement_allowed"] = True
    expect_compile_failure(candidate, "dimensional_measurement_allowed must be false")

    candidate = load_fixture()
    candidate["visual_evidence"][1]["geometry_copy_allowed"] = True
    expect_compile_failure(candidate, "geometry_copy_allowed must be false")


def test_hidden_fastener_and_external_view_policies_are_mandatory() -> None:
    candidate = load_fixture()
    candidate["industrial_design"]["visible_fastener_count"] = 1
    expect_compile_failure(candidate, "visible_fastener_count must be zero")

    candidate = load_fixture()
    candidate["industrial_design"]["default_view"] = "all_objects"
    expect_compile_failure(candidate, "default_view must be exterior")


def test_under_sized_actuator_and_harness_fail_before_publication() -> None:
    candidate = load_fixture()
    candidate["kinematics"]["segments"][1]["selected_output_torque"]["value"] = 1.0
    expect_compile_failure(candidate, "J2 selected output torque margin")

    candidate = load_fixture()
    candidate["electrical"]["trunk_ampacity"]["value"] = 1.0
    candidate["electrical"]["trunk_ampacity"]["tolerance"]["minus"] = 0.0
    expect_compile_failure(candidate, "ampacity margin")


def test_oversized_joint_pcb_fails_before_publication() -> None:
    candidate = load_fixture()
    candidate["electronics"]["joint_board_width"]["value"] = 80.0
    expect_compile_failure(candidate, "joint PCB diagonal")

    candidate = load_fixture()
    candidate["kinematics"]["segments"][6]["link_structural_diameter"]["value"] = 30.0
    expect_compile_failure(candidate, "generated structural diameter is below its requirement")

    candidate = load_fixture()
    candidate["manufacturing"]["minimum_structural_wall"]["value"] = 3.0
    expect_compile_failure(candidate, "structural shell wall falls below the minimum")


def test_whole_robot_structural_targets_reject_under_stiff_designs() -> None:
    candidate = load_fixture()
    candidate["system"]["maximum_design_load_tool_deflection"]["value"] = 0.1
    expect_compile_failure(candidate, "whole-robot structural screen failed")

    candidate = load_fixture()
    candidate["system"]["minimum_screening_first_mode"]["value"] = 100.0
    expect_compile_failure(candidate, "whole-robot structural screen failed")


def test_incompatible_or_unroutable_electronics_fail_before_publication() -> None:
    candidate = load_fixture()
    mcu = next(item for item in candidate["electronics"]["component_bindings"]
               if item["role"] == "motor_control_mcu")
    mcu["capabilities"]["package_accessible_fdcan_instances"] = 1
    expect_compile_failure(candidate, "exposes 1 FDCAN instances")

    candidate = load_fixture()
    mosfet = next(item for item in candidate["electronics"]["component_bindings"]
                  if item["role"] == "power_mosfet")
    mosfet["capabilities"]["vds_max_v"] = 60.0
    expect_compile_failure(candidate, "MOSFET VDS rating is below derated transient requirement")

    candidate = load_fixture()
    candidate["electronics"]["maximum_estimated_board_utilization"]["value"] = 0.7
    expect_compile_failure(candidate, "joint_power estimated placement utilization")


def test_material_provenance_and_structural_screen_are_mandatory() -> None:
    candidate = load_fixture()
    for source in candidate["materials"]["Al_6061_T6"]["sources"]:
        source["property_coverage"] = [
            item for item in source["property_coverage"] if item != "poisson_ratio"]
    expect_compile_failure(candidate, "sources do not cover every screening property")

    candidate = load_fixture()
    candidate["materials"]["Al_6061_T6"]["screening_properties"][
        "minimum_yield_strength"]["value"] = 1.0
    expect_compile_failure(candidate, "first-order link yield margin")


def test_power_stage_thermal_limit_is_not_motor_winding_limit() -> None:
    candidate = load_fixture()
    candidate["thermal"]["maximum_driver_junction"]["value"] = 41.0
    candidate["thermal"]["maximum_motor_winding"]["value"] = 200.0
    expect_compile_failure(candidate, "thermal resistance")


def test_durability_integrity_brake_and_safety_contracts_reject_bad_inputs() -> None:
    candidate = load_fixture()
    candidate["durability"]["load_spectrum"][0]["cycle_fraction"] = 0.40
    expect_compile_failure(candidate, "cycle fractions must sum to one")

    candidate = load_fixture()
    candidate["electrical_integrity"]["minimum_propagation_velocity"]["value"] = 10.0
    expect_compile_failure(candidate, "CAN FD geometric timing margin")

    candidate = load_fixture()
    candidate["braking"]["minimum_hot_torque_retention"]["value"] = 0.0
    expect_compile_failure(candidate, "minimum_hot_torque_retention")

    candidate = load_fixture()
    candidate["functional_safety"]["target_performance_level"] = "PL_c"
    expect_compile_failure(candidate, "target_performance_level must be PL_d")

    candidate = load_fixture()
    candidate["accuracy_and_controls"]["tool_variance_allocation"][
        "thermal_drift"] = 0.10
    expect_compile_failure(candidate, "tool_variance_allocation fractions must sum to one")

    candidate = load_fixture()
    candidate["accuracy_and_controls"]["maximum_joint_position_bandwidth"][
        "value"] = 4.0
    expect_compile_failure(candidate, "structural-mode separation")


def test_manifest_detects_post_generation_tampering() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        manifest_path = compile_product(FIXTURE, Path(temporary))
        target = manifest_path.parent / "electrical/harness.json"
        target.write_text("{}\n", encoding="utf-8")
        try:
            validate_bundle(manifest_path)
        except ProductCompileError as error:
            assert "digest mismatch" in str(error), error
        else:
            raise AssertionError("tampered bundle was accepted")


def main() -> None:
    test_fixture_compiles_to_deterministic_cross_domain_bundle()
    test_visual_media_can_never_supply_dimensions_or_copied_geometry()
    test_hidden_fastener_and_external_view_policies_are_mandatory()
    test_under_sized_actuator_and_harness_fail_before_publication()
    test_oversized_joint_pcb_fails_before_publication()
    test_whole_robot_structural_targets_reject_under_stiff_designs()
    test_incompatible_or_unroutable_electronics_fail_before_publication()
    test_material_provenance_and_structural_screen_are_mandatory()
    test_power_stage_thermal_limit_is_not_motor_winding_limit()
    test_durability_integrity_brake_and_safety_contracts_reject_bad_inputs()
    test_manifest_detects_post_generation_tampering()
    print("Authoritative cross-domain product compiler tests passed")


if __name__ == "__main__":
    main()

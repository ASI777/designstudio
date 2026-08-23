"""Datasheet-traceable electrical topology for one robot joint controller.

The output is an electrically checkable contract, not routed fabrication data.
Every pin of each selected exact active component receives a net or an explicit
no-connect disposition.  Missing safety, sensing, protection, connector and
passive orderable parts remain named work, so a visually complete board cannot
be mistaken for qualified hardware.
"""
from __future__ import annotations

import copy
from typing import Any


JOINT_NET_SCHEMA = "design-studio.joint-electronics-net-contract/1"

_MCU_PINS = {
    1: "VBAT", 2: "PC13", 3: "PC14", 4: "PC15", 5: "PF0", 6: "PF1",
    7: "PG10", 8: "PA0", 9: "PA1", 10: "PA2", 11: "PA3", 12: "PA4",
    13: "PA5", 14: "PA6", 15: "PA7", 16: "PC4", 17: "PB0", 18: "PB1",
    19: "PB2", 20: "VREF+", 21: "VDDA", 22: "PB10", 23: "VDD",
    24: "PB11", 25: "PB12", 26: "PB13", 27: "PB14", 28: "PB15",
    29: "PC6", 30: "PA8", 31: "PA9", 32: "PA10", 33: "PA11",
    34: "PA12", 35: "VDD", 36: "PA13", 37: "PA14", 38: "PA15",
    39: "PC10", 40: "PC11", 41: "PB3", 42: "PB4", 43: "PB5",
    44: "PB6", 45: "PB7", 46: "PB8", 47: "PB9", 48: "VDD", 49: "VSS",
}
_CAN_PINS = {
    1: "TXD", 2: "GND", 3: "VCC", 4: "RXD", 5: "VIO", 6: "CANL",
    7: "CANH", 8: "STB", 9: "THERMAL_PAD",
}
_DRIVER_PINS = {
    1: "GND", 2: "VGLS", 3: "CPL", 4: "CPH", 5: "VM", 6: "VDRAIN",
    7: "VCP", 8: "GHA", 9: "SHA", 10: "GLA", 11: "SPA", 12: "SNA",
    13: "SNB", 14: "SPB", 15: "GLB", 16: "SHB", 17: "GHB", 18: "GHC",
    19: "SHC", 20: "GLC", 21: "SPC", 22: "SNC", 23: "SOC", 24: "SOB",
    25: "SOA", 26: "VREF", 27: "AGND", 28: "nFAULT", 29: "SDO",
    30: "SDI", 31: "SCLK", 32: "nSCS", 33: "ENABLE", 34: "INHA",
    35: "INLA", 36: "INHB", 37: "INLB", 38: "INHC", 39: "INLC",
    40: "DVDD", 41: "DGND", 42: "SW", 43: "VIN", 44: "VCC", 45: "BST",
    46: "RCL", 47: "RT/SD", 48: "FB", 49: "THERMAL_PAD",
}
_MOSFET_PINS = {
    1: "SOURCE", 2: "SOURCE", 3: "SOURCE", 4: "GATE",
    5: "DRAIN", 6: "DRAIN", 7: "DRAIN", 8: "DRAIN",
}
_SHUNT_PINS = {1: "TERMINAL_1", 2: "TERMINAL_2"}
_LDO_PINS = {1: "IN", 2: "GND", 3: "EN", 4: "NC", 5: "OUT"}
_REQUIRED_FINDING_IDS = {
    "ELEC-SAFE-001", "ELEC-SENSE-001", "ELEC-PWR-001", "ELEC-EMC-001",
    "ELEC-BRAKE-001", "ELEC-CONN-001", "ELEC-PASSIVE-001", "ELEC-TEST-001",
}

_PIN_EVIDENCE = {
    "motor_control_mcu": {
        "url": "https://www.st.com/resource/en/datasheet/stm32g474ce.pdf",
        "document_revision": "DS12288 Rev 6",
        "manufacturer_pages": [49, 56, 73],
        "independent_symbol_source": (
            "https://gitlab.com/kicad/libraries/kicad-symbols/-/raw/master/"
            "MCU_ST_STM32G4.kicad_symdir/STM32G474C_B-C-E_Ux.kicad_sym"),
        "independent_symbol_sha256_observed": (
            "783644eb964ba82bb8208277ba7a0134fdddb5a0d61b3484c277b76577fee2de"),
    },
    "can_fd_transceiver": {
        "url": "https://www.ti.com/lit/ds/symlink/tcan1044-q1.pdf",
        "document_revision": "SLLSF17D Rev D",
        "manufacturer_pages": [3],
    },
    "three_phase_gate_driver": {
        "url": "https://www.ti.com/lit/ds/symlink/drv8353r.pdf",
        "document_revision": "SLVSDY6A Rev A",
        "manufacturer_pages": [8, 66],
    },
    "power_mosfet": {
        "url": ("https://www.infineon.com/assets/row/public/documents/24/49/"
                "infineon-bsc050n10ns5-datasheet-en.pdf"),
        "document_revision": "manufacturer current datasheet",
        "manufacturer_pages": [1],
    },
    "phase_current_shunt": {
        "url": "https://www.vishay.com/docs/30122/wslp.pdf",
        "document_revision": "manufacturer datasheet",
        "manufacturer_pages": [1],
    },
    "logic_ldo": {
        "url": "https://www.ti.com/lit/ds/symlink/tps7a20.pdf",
        "document_revision": "SBVS338H Rev H",
        "manufacturer_pages": [4],
    },
}


class JointElectronicsError(ValueError):
    pass


def _component(ref: str, role: str, mpn: str, pins: dict[int, str],
               connections: dict[int, str], *, no_connect: list[int] | None = None) -> dict[str, Any]:
    no_connect = no_connect or []
    overlap = set(connections) & set(no_connect)
    missing = set(pins) - set(connections) - set(no_connect)
    unknown = (set(connections) | set(no_connect)) - set(pins)
    if overlap or missing or unknown:
        raise JointElectronicsError(
            f"{ref} pin disposition invalid: overlap={sorted(overlap)} "
            f"missing={sorted(missing)} unknown={sorted(unknown)}")
    return {
        "ref": ref,
        "role": role,
        "mpn": mpn,
        "pin_evidence": copy.deepcopy(_PIN_EVIDENCE[role]),
        "pins": [
            {"number": number, "name": pins[number],
             "disposition": "connected" if number in connections else "no_connect",
             **({"net": connections[number]} if number in connections else {})}
            for number in sorted(pins)
        ],
    }


def _passive(ref: str, function: str, value: str, a: str, b: str) -> dict[str, Any]:
    return {
        "ref": ref, "function": function, "value": value,
        "binding_status": "engineering_value_candidate_exact_mpn_pending",
        "pins": [{"number": 1, "net": a}, {"number": 2, "net": b}],
    }


def _binding_by_role(bindings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {item["role"]: item for item in bindings}
    required = set(_PIN_EVIDENCE)
    if set(result) != required:
        raise JointElectronicsError(
            f"joint exact-part bindings differ from required roles {sorted(required)}")
    return result


def build_joint_electronics_contract(bindings: list[dict[str, Any]], *,
                                     operating_bus_v: float,
                                     transient_ceiling_v: float) -> dict[str, Any]:
    by_role = _binding_by_role(bindings)
    mcu = _component(
        "U1", "motor_control_mcu", by_role["motor_control_mcu"]["mpn"], _MCU_PINS,
        {
            1: "3V3", 2: "SAFE_MON_A", 5: "HSE_IN", 6: "HSE_OUT", 7: "NRST",
            8: "ISENSE_A", 9: "ISENSE_B", 10: "ISENSE_C", 11: "MOTOR_TEMP",
            12: "DRV_nSCS", 13: "DRV_SCLK", 14: "DRV_MISO", 15: "DRV_MOSI",
            16: "ENCODER_nCS", 17: "BRAKE_FB", 18: "ENCODER_INDEX",
            20: "3V3_ANALOG", 21: "3V3_ANALOG", 22: "SENSOR_SCL", 23: "3V3",
            24: "SENSOR_SDA", 25: "DRV_ENABLE", 26: "PWM_A_L", 27: "PWM_B_L",
            28: "PWM_C_L", 29: "DRV_nFAULT", 30: "PWM_A_H", 31: "PWM_B_H",
            32: "PWM_C_H", 33: "CAN_A_RX", 34: "CAN_A_TX", 35: "3V3",
            36: "SWDIO", 37: "SWCLK", 38: "CAN_A_STB", 39: "CAN_B_STB",
            40: "ENCODER_DATA", 41: "BRAKE_CMD", 42: "SAFE_MON_B",
            43: "CAN_B_RX", 44: "CAN_B_TX", 45: "STATUS_OUT", 48: "3V3",
            49: "DGND",
        },
        no_connect=[3, 4, 19, 46, 47],
    )
    can_a = _component(
        "U2", "can_fd_transceiver", by_role["can_fd_transceiver"]["mpn"], _CAN_PINS,
        {1: "CAN_A_TX", 2: "DGND", 3: "5V", 4: "CAN_A_RX", 5: "3V3",
         6: "CAN_A_L", 7: "CAN_A_H", 8: "CAN_A_STB", 9: "DGND"},
    )
    can_b = _component(
        "U3", "can_fd_transceiver", by_role["can_fd_transceiver"]["mpn"], _CAN_PINS,
        {1: "CAN_B_TX", 2: "DGND", 3: "5V", 4: "CAN_B_RX", 5: "3V3",
         6: "CAN_B_L", 7: "CAN_B_H", 8: "CAN_B_STB", 9: "DGND"},
    )
    driver = _component(
        "U4", "three_phase_gate_driver", by_role["three_phase_gate_driver"]["mpn"],
        _DRIVER_PINS,
        {
            1: "PGND", 2: "VGLS", 3: "CPL", 4: "CPH", 5: "BUS_48V",
            6: "BUS_48V", 7: "VCP", 8: "GATE_A_H", 9: "PHASE_A",
            10: "GATE_A_L", 11: "SHUNT_A_P", 12: "SHUNT_A_N",
            13: "SHUNT_B_N", 14: "SHUNT_B_P", 15: "GATE_B_L", 16: "PHASE_B",
            17: "GATE_B_H", 18: "GATE_C_H", 19: "PHASE_C", 20: "GATE_C_L",
            21: "SHUNT_C_P", 22: "SHUNT_C_N", 23: "ISENSE_C", 24: "ISENSE_B",
            25: "ISENSE_A", 26: "CURRENT_REF_1V65", 27: "AGND",
            28: "DRV_nFAULT", 29: "DRV_MISO", 30: "DRV_MOSI", 31: "DRV_SCLK",
            32: "DRV_nSCS", 33: "DRV_ENABLE", 34: "PWM_A_H", 35: "PWM_A_L",
            36: "PWM_B_H", 37: "PWM_B_L", 38: "PWM_C_H", 39: "PWM_C_L",
            40: "DVDD", 41: "DGND", 42: "BUCK_SW", 43: "BUS_48V", 44: "5V",
            45: "BUCK_BST", 46: "BUCK_RCL", 47: "BUCK_RT_SD", 48: "BUCK_FB",
            49: "PGND",
        },
    )
    ldo = _component(
        "U5", "logic_ldo", by_role["logic_ldo"]["mpn"], _LDO_PINS,
        {1: "5V", 2: "DGND", 3: "5V", 5: "3V3"}, no_connect=[4],
    )

    exact = [mcu, can_a, can_b, driver, ldo]
    phases = ("A", "B", "C")
    for index, phase in enumerate(phases):
        for side, source, drain in (
                ("H", f"PHASE_{phase}", "BUS_48V"),
                ("L", f"SHUNT_{phase}_P", f"PHASE_{phase}")):
            connections = {1: source, 2: source, 3: source, 4: f"Q{phase}{side}_G",
                           5: drain, 6: drain, 7: drain, 8: drain}
            exact.append(_component(
                f"Q{index * 2 + (1 if side == 'H' else 2)}", "power_mosfet",
                by_role["power_mosfet"]["mpn"], _MOSFET_PINS, connections,
            ))
        exact.append(_component(
            f"RSH{index + 1}", "phase_current_shunt",
            by_role["phase_current_shunt"]["mpn"], _SHUNT_PINS,
            {1: f"SHUNT_{phase}_P", 2: f"SHUNT_{phase}_N"},
        ))

    passives = [
        _passive("C1", "driver_vm_high_frequency_bypass", "100 nF / 100 V X7R",
                 "BUS_48V", "PGND"),
        _passive("C2", "driver_vm_local_bulk", ">=10 uF / 100 V",
                 "BUS_48V", "PGND"),
        _passive("C3", "gate_driver_vgls_bypass", "1 uF / 16 V X7R", "VGLS", "PGND"),
        _passive("C4", "charge_pump_flying_capacitor", "47 nF / 100 V X7R", "CPH", "CPL"),
        _passive("C5", "charge_pump_storage", "1 uF / 100 V X7R", "VCP", "BUS_48V"),
        _passive("C6", "driver_digital_supply_bypass", "1 uF / 10 V X7R", "DVDD", "DGND"),
        _passive("L1", "integrated_buck_inductor", "datasheet_calculation_pending", "BUCK_SW", "5V"),
        _passive("D1", "integrated_buck_freewheel_diode", "100 V Schottky candidate", "PGND", "BUCK_SW"),
        _passive("C7", "integrated_buck_output", "datasheet_calculation_pending", "5V", "PGND"),
        _passive("C8", "integrated_buck_bootstrap", "100 nF / 16 V X7R", "BUCK_BST", "BUCK_SW"),
        _passive("R1", "integrated_buck_current_limit", "datasheet_calculation_pending", "BUCK_RCL", "PGND"),
        _passive("R2", "integrated_buck_timing", "datasheet_calculation_pending", "BUCK_RT_SD", "PGND"),
        _passive("R3", "integrated_buck_feedback_upper", "datasheet_calculation_pending", "5V", "BUCK_FB"),
        _passive("R4", "integrated_buck_feedback_lower", "datasheet_calculation_pending", "BUCK_FB", "PGND"),
        _passive("C9", "ldo_input", "1 uF X7R", "5V", "DGND"),
        _passive("C10", "ldo_output", "1 uF X7R", "3V3", "DGND"),
        _passive("R5", "current_reference_upper", "10 kohm 0.1%", "3V3_ANALOG", "CURRENT_REF_1V65"),
        _passive("R6", "current_reference_lower", "10 kohm 0.1%", "CURRENT_REF_1V65", "AGND"),
        _passive("C11", "current_reference_filter", "100 nF C0G", "CURRENT_REF_1V65", "AGND"),
    ]
    for phase in phases:
        passives.extend([
            _passive(f"RG{phase}H", "high_side_gate_slew_control",
                     "datasheet_and_emc_tuning_pending", f"GATE_{phase}_H", f"Q{phase}H_G"),
            _passive(f"RG{phase}L", "low_side_gate_slew_control",
                     "datasheet_and_emc_tuning_pending", f"GATE_{phase}_L", f"Q{phase}L_G"),
            _passive(f"RPD{phase}H", "high_side_gate_source_pulldown", "10 kohm",
                     f"Q{phase}H_G", f"PHASE_{phase}"),
            _passive(f"RPD{phase}L", "low_side_gate_source_pulldown", "10 kohm",
                     f"Q{phase}L_G", f"SHUNT_{phase}_P"),
            _passive(f"NT{phase}", "kelvin_shunt_return_net_tie", "0 ohm net tie",
                     f"SHUNT_{phase}_N", "PGND"),
        ])
    passives.extend([
        _passive("NT1", "analog_ground_star_tie", "0 ohm net tie", "AGND", "PGND"),
        _passive("NT2", "digital_ground_star_tie", "0 ohm net tie", "DGND", "PGND"),
    ])

    unresolved = [
        {
            "finding_id": "ELEC-SAFE-001",
            "function": "dual_channel_hardware_gate_disable_and_diagnostics",
            "severity": "safety_blocking",
            "owner_domains": ["safety", "electronics", "firmware"],
            "known_inputs": ["two_safety_channels_required", "DRV_ENABLE_net_exists"],
            "missing_inputs": [
                "required_performance_level_or_sil", "safe_state_timing",
                "diagnostic_coverage_target", "fault_reaction_and_reset_policy"],
            "blocked_outputs": [
                "independent_hardware_sto_schematic", "fmeca_fmeda",
                "functional_safety_validation_plan"],
            "closure_evidence": [
                "approved_safety_requirements", "independent_disable_path_netlist",
                "fault_injection_test_receipt"],
            "reason": "MCU GPIO monitoring is not an independently qualified safety function",
        },
        {
            "finding_id": "ELEC-SENSE-001",
            "function": "absolute_joint_encoder_and_mechanical_target",
            "severity": "functional_blocking",
            "owner_domains": ["electronics", "mechanical", "controls"],
            "known_inputs": ["ENCODER_DATA_net", "ENCODER_INDEX_net", "ENCODER_nCS_net"],
            "missing_inputs": [
                "absolute_accuracy", "repeatability", "latency", "interface_protocol",
                "magnet_or_ring_geometry", "air_gap_and_mounting_datums"],
            "blocked_outputs": [
                "encoder_part_binding", "target_cad", "calibration_and_error_budget"],
            "closure_evidence": [
                "encoder_requirement_contract", "datasheet_bound_part_and_target",
                "tolerance_stack_and_calibration_test"],
            "reason": "accuracy, interface, magnet/ring and mounting datum are not selected",
        },
        {
            "finding_id": "ELEC-PWR-001",
            "function": "48_v_bus_transient_reverse_polarity_and_inrush_protection",
            "severity": "safety_blocking",
            "owner_domains": ["electrical", "electronics", "emc"],
            "known_inputs": ["48_v_nominal_bus", "60_v_transient_ceiling"],
            "missing_inputs": [
                "pulse_waveforms_and_source_impedance", "reverse_polarity_duration",
                "hot_plug_capacitance", "maximum_inrush_current", "regeneration_clamp_strategy"],
            "blocked_outputs": [
                "tvs_efuse_and_inrush_part_bindings", "surge_energy_check", "input_filter"],
            "closure_evidence": [
                "electrical_disturbance_profile", "soa_and_pulse_energy_calculation",
                "bench_transient_test_receipt"],
            "reason": "system coordination and pulse environment are not yet defined",
        },
        {
            "finding_id": "ELEC-EMC-001",
            "function": "can_fd_common_mode_chokes_esd_and_switchable_end_termination",
            "severity": "verification_blocking",
            "owner_domains": ["electronics", "electrical", "emc"],
            "known_inputs": ["two_can_fd_channels", "eight_mbps_transceiver_capability"],
            "missing_inputs": [
                "cable_impedance", "maximum_stub_length", "node_position",
                "esd_and_eft_environment", "shield_bonding_topology"],
            "blocked_outputs": [
                "can_protection_part_bindings", "termination_population_plan",
                "signal_integrity_and_emc_evidence"],
            "closure_evidence": [
                "harness_transmission_line_contract", "datasheet_bound_emc_network",
                "eye_mask_and_immunity_test_receipts"],
            "reason": "harness impedance, node position and EMC environment need selection inputs",
        },
        {
            "finding_id": "ELEC-BRAKE-001",
            "function": "motor_brake_power_driver_and_flyback_path",
            "severity": "safety_blocking",
            "owner_domains": ["mechanical", "electrical", "electronics", "safety"],
            "known_inputs": ["BRAKE_CMD_net", "BRAKE_FB_net"],
            "missing_inputs": [
                "brake_voltage", "coil_current_and_inductance", "release_time",
                "holding_strategy", "deenergized_safe_state", "flyback_release_tradeoff"],
            "blocked_outputs": [
                "brake_driver_and_clamp_binding", "brake_diagnostic_circuit",
                "stopping_and_holding_validation"],
            "closure_evidence": [
                "selected_brake_datasheet", "coil_transient_calculation",
                "measured_release_and_hold_test"],
            "reason": "brake voltage, current, release time and holding strategy are missing",
        },
        {
            "finding_id": "ELEC-CONN-001",
            "function": "motor_harness_and_logic_power_board_connectors",
            "severity": "fabrication_blocking",
            "owner_domains": ["electrical", "electronics", "mechanical", "manufacturing"],
            "known_inputs": ["internal_connector_location", "positive_latch_required"],
            "missing_inputs": [
                "contact_current_and_temperature_rise", "mating_cycles", "creepage_clearance",
                "retention_load", "keying", "service_and_board_edge_envelopes"],
            "blocked_outputs": [
                "connector_part_bindings", "mating_harness_bom", "board_edge_cad_and_keepouts"],
            "closure_evidence": [
                "connector_requirement_contract", "mated_pair_datasheets_and_cad",
                "retention_and_temperature_rise_tests"],
            "reason": "mating cycle, current, creepage, retention and service envelope need selection",
        },
        {
            "finding_id": "ELEC-PASSIVE-001",
            "function": "all_passive_orderable_mpns_and_verified_land_patterns",
            "severity": "fabrication_blocking",
            "owner_domains": ["electronics", "pcb", "procurement"],
            "known_inputs": ["engineering_value_candidates_and_net_endpoints"],
            "missing_inputs": [
                "buck_calculated_values", "gate_slew_emc_target", "dc_bias_derating",
                "pulse_energy", "temperature_coefficients", "lifecycle_and_supply_constraints"],
            "blocked_outputs": ["orderable_bom", "verified_land_patterns", "fabrication_netlist"],
            "closure_evidence": [
                "calculation_receipts", "manufacturer_datasheet_digests",
                "symbol_footprint_and_3d_model_audit"],
            "reason": "calculated values, tolerances, voltage bias and thermal pulse ratings remain",
        },
        {
            "finding_id": "ELEC-TEST-001",
            "function": "oscillator_reset_debug_and_production_test_circuit",
            "severity": "verification_blocking",
            "owner_domains": ["electronics", "firmware", "manufacturing_test"],
            "known_inputs": ["HSE_IN_and_HSE_OUT", "NRST", "SWDIO", "SWCLK"],
            "missing_inputs": [
                "clock_accuracy_and_startup", "debug_access_policy", "fixture_interface",
                "boundary_scan_and_programming_throughput", "production_test_coverage"],
            "blocked_outputs": [
                "oscillator_and_reset_binding", "debug_fixture_footprint",
                "production_test_specification"],
            "closure_evidence": [
                "clock_and_fixture_requirements", "datasheet_bound_clock_reset_network",
                "programming_and_test_coverage_receipt"],
            "reason": "clock accuracy, startup and fixture requirements remain",
        },
    ]
    topology = {
        "schema": JOINT_NET_SCHEMA,
        "status": "electrically_structured_candidate_incomplete_exact_binding",
        "scope": "one joint controller logic and power board pair",
        "operating_bus_v": operating_bus_v,
        "transient_ceiling_v": transient_ceiling_v,
        "exact_components": exact,
        "engineering_value_components": passives,
        "required_interfaces": {
            "motor": ["PHASE_A", "PHASE_B", "PHASE_C"],
            "communications": ["CAN_A_H", "CAN_A_L", "CAN_B_H", "CAN_B_L"],
            "power": ["BUS_48V", "PGND"],
            "sensing": ["ENCODER_DATA", "ENCODER_INDEX", "MOTOR_TEMP"],
            "safety_monitoring_only": ["SAFE_MON_A", "SAFE_MON_B"],
        },
        "pin_mux_receipts": [
            {"function": "FDCAN1_RX", "mcu_pin": 33, "port": "PA11", "net": "CAN_A_RX"},
            {"function": "FDCAN1_TX", "mcu_pin": 34, "port": "PA12", "net": "CAN_A_TX"},
            {"function": "FDCAN2_RX", "mcu_pin": 43, "port": "PB5", "net": "CAN_B_RX"},
            {"function": "FDCAN2_TX", "mcu_pin": 44, "port": "PB6", "net": "CAN_B_TX"},
            {"function": "TIM1_CH1", "mcu_pin": 30, "port": "PA8", "net": "PWM_A_H"},
            {"function": "TIM1_CH2", "mcu_pin": 31, "port": "PA9", "net": "PWM_B_H"},
            {"function": "TIM1_CH3", "mcu_pin": 32, "port": "PA10", "net": "PWM_C_H"},
            {"function": "TIM1_CH1N", "mcu_pin": 26, "port": "PB13", "net": "PWM_A_L"},
            {"function": "TIM1_CH2N", "mcu_pin": 27, "port": "PB14", "net": "PWM_B_L"},
            {"function": "TIM1_CH3N", "mcu_pin": 28, "port": "PB15", "net": "PWM_C_L"},
            {"function": "SPI1_NSS", "mcu_pin": 12, "port": "PA4", "net": "DRV_nSCS"},
            {"function": "SPI1_SCK", "mcu_pin": 13, "port": "PA5", "net": "DRV_SCLK"},
            {"function": "SPI1_MISO", "mcu_pin": 14, "port": "PA6", "net": "DRV_MISO"},
            {"function": "SPI1_MOSI", "mcu_pin": 15, "port": "PA7", "net": "DRV_MOSI"},
        ],
        "unresolved_exact_bindings": unresolved,
        "readiness": {
            "exact_active_component_pin_disposition": "pass",
            "three_phase_power_topology": "pass",
            "dual_can_fd_logic_topology": "pass",
            "fabrication_data": "incomplete",
            "functional_safety_qualification": "incomplete",
            "hardware_test_evidence": "incomplete",
        },
    }
    validate_joint_electronics_contract(topology)
    return topology


def validate_joint_electronics_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != JOINT_NET_SCHEMA:
        raise JointElectronicsError("joint electronics contract schema is invalid")
    exact = value.get("exact_components")
    if not isinstance(exact, list) or not exact:
        raise JointElectronicsError("joint electronics contract has no exact components")
    refs = [item.get("ref") for item in exact if isinstance(item, dict)]
    if len(refs) != len(exact) or len(set(refs)) != len(refs):
        raise JointElectronicsError("exact component references must be unique")
    expected_counts = {
        "motor_control_mcu": 1, "can_fd_transceiver": 2,
        "three_phase_gate_driver": 1, "power_mosfet": 6,
        "phase_current_shunt": 3, "logic_ldo": 1,
    }
    actual_counts = {role: sum(item.get("role") == role for item in exact)
                     for role in expected_counts}
    if actual_counts != expected_counts:
        raise JointElectronicsError(f"exact component role counts are invalid: {actual_counts}")
    pin_maps = {
        "motor_control_mcu": _MCU_PINS, "can_fd_transceiver": _CAN_PINS,
        "three_phase_gate_driver": _DRIVER_PINS, "power_mosfet": _MOSFET_PINS,
        "phase_current_shunt": _SHUNT_PINS, "logic_ldo": _LDO_PINS,
    }
    net_endpoints: dict[str, list[tuple[str, int]]] = {}
    for component in exact:
        expected = pin_maps[component["role"]]
        pins = component.get("pins")
        if not isinstance(pins, list) or len(pins) != len(expected):
            raise JointElectronicsError(f"{component['ref']} pin count is invalid")
        observed_numbers = set()
        for pin in pins:
            number = pin.get("number")
            observed_numbers.add(number)
            if expected.get(number) != pin.get("name"):
                raise JointElectronicsError(
                    f"{component['ref']} pin {number} name differs from bound pin map")
            if pin.get("disposition") == "connected" and isinstance(pin.get("net"), str):
                net_endpoints.setdefault(pin["net"], []).append((component["ref"], number))
            elif pin.get("disposition") != "no_connect" or "net" in pin:
                raise JointElectronicsError(
                    f"{component['ref']} pin {number} has invalid disposition")
        if observed_numbers != set(expected):
            raise JointElectronicsError(f"{component['ref']} pin numbers are incomplete")
    for passive in value.get("engineering_value_components", []):
        for pin in passive.get("pins", []):
            net_endpoints.setdefault(pin["net"], []).append((passive["ref"], pin["number"]))

    required_endpoint_sets = {
        "CAN_A_TX": {("U1", 34), ("U2", 1)},
        "CAN_A_RX": {("U1", 33), ("U2", 4)},
        "CAN_B_TX": {("U1", 44), ("U3", 1)},
        "CAN_B_RX": {("U1", 43), ("U3", 4)},
        "DRV_nFAULT": {("U1", 29), ("U4", 28)},
        "DRV_ENABLE": {("U1", 25), ("U4", 33)},
        "DRV_nSCS": {("U1", 12), ("U4", 32)},
        "DRV_SCLK": {("U1", 13), ("U4", 31)},
        "DRV_MISO": {("U1", 14), ("U4", 29)},
        "DRV_MOSI": {("U1", 15), ("U4", 30)},
    }
    for net, endpoints in required_endpoint_sets.items():
        if not endpoints <= set(net_endpoints.get(net, [])):
            raise JointElectronicsError(f"required topology net {net} is miswired")
    for phase_index, phase in enumerate(("A", "B", "C")):
        high_ref = f"Q{phase_index * 2 + 1}"
        low_ref = f"Q{phase_index * 2 + 2}"
        required = {
            f"PHASE_{phase}": {(high_ref, 1), (low_ref, 5)},
            f"SHUNT_{phase}_P": {(low_ref, 1), (f"RSH{phase_index + 1}", 1)},
            f"PWM_{phase}_H": {("U4", 34 + phase_index * 2)},
            f"PWM_{phase}_L": {("U4", 35 + phase_index * 2)},
        }
        for net, endpoints in required.items():
            if not endpoints <= set(net_endpoints.get(net, [])):
                raise JointElectronicsError(f"phase {phase} topology net {net} is miswired")
    unresolved = value.get("unresolved_exact_bindings")
    if not isinstance(unresolved, list):
        raise JointElectronicsError("unresolved exact-binding work must be an array")
    finding_ids = [item.get("finding_id") for item in unresolved if isinstance(item, dict)]
    if set(finding_ids) != _REQUIRED_FINDING_IDS or len(finding_ids) != len(set(finding_ids)):
        raise JointElectronicsError(
            "candidate topology must retain every named unresolved exact-binding finding")
    for finding in unresolved:
        for field in (
                "owner_domains", "known_inputs", "missing_inputs", "blocked_outputs",
                "closure_evidence"):
            values = finding.get(field)
            if not isinstance(values, list) or not values or len(values) != len(set(values)) \
                    or any(not isinstance(item, str) or not item for item in values):
                raise JointElectronicsError(
                    f"{finding['finding_id']} {field} must be a non-empty unique string array")
    return value


__all__ = [
    "JOINT_NET_SCHEMA",
    "JointElectronicsError",
    "build_joint_electronics_contract",
    "validate_joint_electronics_contract",
]

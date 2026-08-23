#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from swarm.memory.joint_electronics import (  # noqa: E402
    JointElectronicsError,
    build_joint_electronics_contract,
    validate_joint_electronics_contract,
)


FIXTURE = ROOT / "acceptance/authoritative-seven-axis-robot/source/engineering-requirements.json"


def build() -> dict:
    requirements = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return build_joint_electronics_contract(
        requirements["electronics"]["component_bindings"],
        operating_bus_v=requirements["electrical"]["bus_voltage"]["value"],
        transient_ceiling_v=requirements["electrical"]["bus_transient_ceiling"]["value"],
    )


def expect_error(value: dict, expected: str) -> None:
    try:
        validate_joint_electronics_contract(value)
    except JointElectronicsError as error:
        assert expected in str(error), error
    else:
        raise AssertionError(f"expected joint electronics failure containing {expected!r}")


def test_exact_active_pin_contract_and_schema() -> None:
    contract = build()
    validate_joint_electronics_contract(contract)
    schema = json.loads(
        (ROOT / "docs/schemas/joint-electronics-net-contract-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(contract)
    assert len(contract["exact_components"]) == 14
    assert sum(len(component["pins"]) for component in contract["exact_components"]) == 175
    assert len({receipt["mcu_pin"] for receipt in contract["pin_mux_receipts"]}) == len(
        contract["pin_mux_receipts"])
    assert {item["function"] for item in contract["unresolved_exact_bindings"]} >= {
        "dual_channel_hardware_gate_disable_and_diagnostics",
        "absolute_joint_encoder_and_mechanical_target",
        "48_v_bus_transient_reverse_polarity_and_inrush_protection",
    }
    assert len(contract["unresolved_exact_bindings"]) == 8
    assert all(item["missing_inputs"] and item["blocked_outputs"]
               and item["closure_evidence"]
               for item in contract["unresolved_exact_bindings"])


def test_miswired_can_and_phase_are_rejected() -> None:
    contract = build()
    bad_can = copy.deepcopy(contract)
    mcu = next(item for item in bad_can["exact_components"] if item["ref"] == "U1")
    next(pin for pin in mcu["pins"] if pin["number"] == 34)["net"] = "WRONG_CAN_NET"
    expect_error(bad_can, "CAN_A_TX is miswired")

    bad_phase = copy.deepcopy(contract)
    high_side = next(item for item in bad_phase["exact_components"] if item["ref"] == "Q1")
    next(pin for pin in high_side["pins"] if pin["number"] == 1)["net"] = "WRONG_PHASE"
    expect_error(bad_phase, "phase A topology net PHASE_A is miswired")


def test_pin_name_and_unresolved_work_cannot_be_silently_erased() -> None:
    contract = build()
    bad_name = copy.deepcopy(contract)
    driver = next(item for item in bad_name["exact_components"] if item["ref"] == "U4")
    next(pin for pin in driver["pins"] if pin["number"] == 28)["name"] = "FAULT"
    expect_error(bad_name, "differs from bound pin map")

    erased = copy.deepcopy(contract)
    erased["unresolved_exact_bindings"] = []
    expect_error(erased, "retain every named unresolved exact-binding finding")

    hidden = copy.deepcopy(contract)
    hidden["unresolved_exact_bindings"].pop()
    expect_error(hidden, "retain every named unresolved exact-binding finding")


def main() -> None:
    test_exact_active_pin_contract_and_schema()
    test_miswired_can_and_phase_are_rejected()
    test_pin_name_and_unresolved_work_cannot_be_silently_erased()
    print("Joint electronics net-contract tests passed")


if __name__ == "__main__":
    main()

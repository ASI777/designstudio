#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from swarm.memory.authoritative_dimensions import (  # noqa: E402
    DimensionAuthorityError,
    canonical_digest,
    validate_dimension_set,
)


SOURCE_DIGEST = canonical_digest({"document": "DS-R7-SPEC-001", "revision": "1"})


def quantity(identifier: str, value: float, *, kind: str = "engineering_requirement",
             inputs: list[str] | None = None) -> dict:
    source = {
        "kind": kind,
        "reference": f"DS-R7-SPEC-001#{identifier}",
        "revision": "1",
        "sha256": SOURCE_DIGEST,
    }
    if kind == "derived_calculation":
        source.update({"input_quantity_ids": inputs or [], "method": "sum(inputs)"})
    return {
        "id": identifier,
        "value": value,
        "unit": "mm",
        "tolerance": {"minus": 0.1, "plus": 0.1},
        "criticality": "interface",
        "source": source,
    }


def document(quantities: list[dict]) -> dict:
    return {
        "schema": "design-studio.authoritative-dimension-set/1",
        "set_id": "robot-r7-dimensions",
        "units_policy": "explicit_no_implicit_conversion",
        "quantities": quantities,
    }


def expect_rejected(candidate: dict, expected: str) -> None:
    try:
        validate_dimension_set(candidate)
    except DimensionAuthorityError as error:
        assert expected in str(error), error
    else:
        raise AssertionError(f"expected rejection containing {expected!r}")


def main() -> None:
    valid = document([
        quantity("base.diameter", 260.0),
        quantity("link1.length", 310.0),
        quantity("link2.length", 290.0),
        quantity("reach.nominal", 600.0, kind="derived_calculation",
                 inputs=["link1.length", "link2.length"]),
    ])
    normalized = validate_dimension_set(valid)
    assert len(normalized["quantities"]) == 4
    assert len(normalized["dimension_set_digest"]) == 64
    assert normalized == validate_dimension_set(json.loads(json.dumps(valid)))

    visual = document([quantity("base.diameter", 260.0, kind="image_inference")])
    expect_rejected(visual, "cannot define a dimension")

    missing_tolerance = document([quantity("base.diameter", 260.0)])
    missing_tolerance["quantities"][0].pop("tolerance")
    expect_rejected(missing_tolerance, "missing=['tolerance']")

    stale_calculation = document([
        quantity("reach.nominal", 600.0, kind="derived_calculation",
                 inputs=["link1.length", "link2.length"]),
    ])
    expect_rejected(stale_calculation, "must reference an earlier quantity")

    implicit_units = document([quantity("base.diameter", 260.0)])
    implicit_units["units_policy"] = "guess_from_context"
    expect_rejected(implicit_units, "forbid implicit conversion")

    print("Authoritative dimension contract tests passed")


if __name__ == "__main__":
    main()

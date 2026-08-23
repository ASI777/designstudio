#!/usr/bin/env python3
"""Contract tests for mechanical-cad-program/2 and surface-design/2."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "freecad" / "DesignStudioWorkbench"))

from DesignStudio.vector_native_cad import (  # noqa: E402
    MECHANICAL_SCHEMA,
    SURFACE_SCHEMA,
    VectorNativeCadError,
    control_datum_digest,
    validate_surface_design,
    validate_vector_program,
)
from DesignStudio.mechanical_cad import validate_program as validate_mechanical_program  # noqa: E402


def curve(offset: float = 0.0) -> dict:
    return {
        "degree": 2,
        "knots": [0.0, 0.5, 1.0],
        "multiplicities": [3, 1, 3],
        "weights": [1.0, 1.0, 1.0, 1.0],
        "control_points": [[offset, 0.0, 0.0], [10.0 + offset, 0.0, 0.0],
                           [10.0 + offset, 10.0, 0.0], [offset, 10.0, 0.0]],
        "closed": False,
        "periodic": False,
        "classification": "original-designed",
        "provenance": ["test:vector-native"],
    }


def program() -> dict:
    envelope = {"min_mm": [-1.0, -1.0, -1.0], "max_mm": [20.0, 20.0, 20.0]}
    datum_point = [0.0, 0.0, 0.0]
    return {
        "schema": MECHANICAL_SCHEMA,
        "program_id": "vector-contract",
        "units": "mm",
        "author": "contract-test",
        "envelope": envelope,
        "control_datums": [{"id": "origin", "point_mm": datum_point, "locked": True,
                             "digest": control_datum_digest(datum_point)}],
        "commands": [
            {"id": "section_a", "op": "curve.bspline3d", "params": {"curve": curve()},
             "provenance": ["test:section-a"]},
            {"id": "section_b", "op": "curve.bspline3d", "params": {"curve": curve(0.0)},
             "provenance": ["test:section-b"]},
            {"id": "loft", "op": "feature.loft", "params": {"sections": ["section_a", "section_b"]},
             "provenance": ["test:loft"]},
            {"id": "assembly", "op": "feature.compound", "params": {"members": ["loft"]},
             "provenance": ["test:compound"]},
        ],
        "checks": [{"kind": "valid_shape", "target": "assembly"}],
    }


def surface() -> dict:
    return {
        "schema": SURFACE_SCHEMA,
        "design_id": "surface-contract",
        "units": "mm",
        "author": "contract-test",
        "envelope": {"min_mm": [-1.0, -1.0, -1.0], "max_mm": [20.0, 20.0, 20.0]},
        "sections": [
            {"id": "station_a", "station_mm": 0.0, "curve": curve()},
            {"id": "station_b", "station_mm": 10.0, "curve": curve()},
        ],
        "guides": [{"id": "crown", "curve": curve()}],
        "continuity": {"required": "G1", "max_normal_angle_deg": 1.0},
        "parameters": {"wall_mm": 2.0},
        "provenance": ["test:surface"],
    }


def rib_program() -> dict:
    point = [0.0, 0.0, 0.0]
    return {
        "schema": MECHANICAL_SCHEMA,
        "program_id": "rib-contract",
        "units": "mm",
        "author": "contract-test",
        "envelope": {"min_mm": [-1.0, -1.0, -1.0], "max_mm": [30.0, 30.0, 30.0]},
        "control_datums": [{"id": "origin", "point_mm": point, "locked": True,
                             "digest": control_datum_digest(point)}],
        "commands": [{
            "id": "rib_a", "op": "feature.rib",
            "params": {"start_mm": [0.0, 0.0, 0.0], "end_mm": [20.0, 0.0, 0.0],
                       "thickness_mm": 1.0, "height_mm": 4.0,
                       "draft_deg": 1.0, "root_fillet_mm": 0.4},
            "provenance": ["test:rib"]}],
        "checks": [{"kind": "valid_shape", "target": "rib_a"}],
    }


def expect_invalid(value, label: str) -> None:
    try:
        validate_vector_program(value)
    except VectorNativeCadError:
        return
    raise AssertionError(f"{label} was accepted")


def main() -> None:
    normalized = validate_vector_program(program())
    assert normalized["schema"] == MECHANICAL_SCHEMA
    assert validate_mechanical_program(program())["schema"] == MECHANICAL_SCHEMA
    assert validate_surface_design(surface())["schema"] == SURFACE_SCHEMA
    assert validate_vector_program(rib_program())["commands"][0]["op"] == "feature.rib"

    invalid = copy.deepcopy(program())
    invalid["commands"][0]["params"]["curve"]["weights"][0] = 0.0
    expect_invalid(invalid, "non-positive weight")

    invalid = copy.deepcopy(program())
    invalid["commands"][0]["params"]["curve"]["knots"] = [0.0, 1.0, 0.5]
    expect_invalid(invalid, "unordered knots")

    invalid = copy.deepcopy(program())
    invalid["commands"][0]["params"]["curve"]["control_points"][0] = [float("nan"), 0.0, 0.0]
    expect_invalid(invalid, "non-finite control point")

    invalid = copy.deepcopy(program())
    invalid["commands"][2]["params"]["sections"] = ["missing", "section_b"]
    expect_invalid(invalid, "invalid section order/reference")

    invalid = copy.deepcopy(program())
    invalid["commands"][3]["params"]["members"] = ["missing"]
    expect_invalid(invalid, "invalid compound member reference")

    invalid = copy.deepcopy(program())
    invalid["commands"][3]["params"]["members"] = ["loft", "loft"]
    expect_invalid(invalid, "duplicate compound member reference")

    invalid = copy.deepcopy(program())
    invalid["commands"][2]["params"]["solid"] = "false"
    expect_invalid(invalid, "string used where typed loft boolean is required")

    invalid = copy.deepcopy(program())
    invalid["commands"][2]["params"]["unexpected"] = True
    expect_invalid(invalid, "unknown operation parameter")

    invalid = copy.deepcopy(program())
    invalid["commands"][0]["params"]["curve"]["unexpected"] = True
    expect_invalid(invalid, "unknown nested curve field")

    invalid = copy.deepcopy(program())
    invalid["commands"][2]["params"]["continuity"] = {"required": "G1", "unexpected": 1}
    expect_invalid(invalid, "unknown nested continuity field")

    invalid = copy.deepcopy(program())
    invalid["commands"][2]["params"]["continuity"] = {"required": "G1"}
    expect_invalid(invalid, "incomplete continuity object")

    for plane, constant_axis in (("XY", 2), ("XZ", 1), ("YZ", 0)):
        planar = copy.deepcopy(program())
        command = planar["commands"][0]
        command["op"] = "sketch.bspline"
        command["params"]["plane"] = plane
        points = command["params"]["curve"]["control_points"]
        for point in points:
            point[constant_axis] = 5.0
        assert validate_vector_program(planar)["commands"][0]["params"]["plane"] == plane
        nonplanar = copy.deepcopy(planar)
        nonplanar["commands"][0]["params"]["curve"]["control_points"][1][constant_axis] += 0.1
        expect_invalid(nonplanar, f"non-planar {plane} sketch")

    valid_boolean = copy.deepcopy(program())
    valid_boolean["commands"][2]["params"].update(
        {"solid": True, "ruled": False, "closed": False})
    assert validate_vector_program(valid_boolean)["commands"][2]["params"]["solid"] is True

    invalid = copy.deepcopy(program())
    invalid["commands"][0]["params"]["curve"]["control_points"][0] = [25.0, 0.0, 0.0]
    expect_invalid(invalid, "control point outside envelope")

    invalid = copy.deepcopy(program())
    invalid["commands"][0]["params"]["curve"]["multiplicities"] = [2, 1, 3]
    expect_invalid(invalid, "inconsistent knot multiplicity sum")

    invalid_surface = surface()
    invalid_surface["sections"][1]["station_mm"] = -1.0
    try:
        validate_surface_design(invalid_surface)
    except VectorNativeCadError:
        pass
    else:
        raise AssertionError("contradictory section stations were accepted")
    import json
    from jsonschema.validators import Draft202012Validator
    from jsonschema import ValidationError
    mechanical_schema_validator = None
    for schema_path, value in ((ROOT / "docs/schemas/mechanical-cad-program-v2.schema.json", program()),
                               (ROOT / "docs/schemas/mechanical-cad-program-v2.schema.json", rib_program()),
                               (ROOT / "docs/schemas/surface-design-v2.schema.json", surface())):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
        assert isinstance(schema.get("$defs"), dict) and schema["$defs"]
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        validator.validate(value)
        if schema_path.name.startswith("mechanical"):
            mechanical_schema_validator = validator
    invalid = copy.deepcopy(program())
    del invalid["commands"][0]["params"]["curve"]
    if mechanical_schema_validator is not None:
        try:
            mechanical_schema_validator.validate(invalid)
        except ValidationError:
            pass
        else:
            raise AssertionError("public mechanical schema accepted missing curve parameters")
        invalid_public = copy.deepcopy(program())
        invalid_public["commands"][0]["params"]["curve"]["weights"][0] = 0.0
        try:
            mechanical_schema_validator.validate(invalid_public)
        except ValidationError:
            pass
        else:
            raise AssertionError("public mechanical schema accepted a non-positive weight")
    expect_invalid(invalid, "missing typed curve parameters")
    print("VECTOR_NATIVE_CAD_CONTRACT_OK")


if __name__ == "__main__":
    main()

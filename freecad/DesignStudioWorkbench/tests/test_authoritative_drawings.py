#!/usr/bin/env python3
"""Public-contract checks for authoritative drawing packages."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "freecad" / "DesignStudioWorkbench"))

from DesignStudio.authoritative_drawings import SCHEMA, PhysicalDesignError, validate_authoritative_drawing_package


def digest(seed):
    return (seed * 64)[:64]


def request():
    return {
        "schema": SCHEMA, "package_id": "drawing-contract", "revision": 1,
        "document": {"document_id": "doc", "path": "mechanical/doc.FCStd", "file_sha256": digest("a")},
        "source": {"semantic_id": "target-body", "object_name": "TargetBody", "shape_sha256": digest("b"), "role": "enclosure", "source_of_truth": "freecad_brep"},
        "views": ["front", "rear", "left", "right", "top", "bottom"],
        "sections": [{"section_id": "mid", "plane_origin_mm": [0, 0, 5], "plane_normal": [0, 0, 1], "label": "Mid section"}],
        "annotations": {"selected_faces": ["Face1"], "selected_edges": ["Edge1"], "datums": [],
                        "dimensions": [{"dimension_id": "overall", "kind": "overall", "value_mm": 10.0, "label": "Overall"}],
                        "tolerances": [], "gdt": [], "continuity": []},
        "output": {"directory": "drawings", "formats": ["svg", "dxf"], "sheet_capacity": 100, "additional_sheets_policy": "deterministic_complexity_split"},
        "provenance": {"created_by": "test", "created_utc": "2026-08-13T00:00:00+00:00", "geometry_authority": "freecad_brep", "raster_geometry_authority": False},
    }


def invalid(value, message):
    try:
        validate_authoritative_drawing_package(value)
    except PhysicalDesignError:
        return
    raise AssertionError(message)


def main():
    value = request()
    assert validate_authoritative_drawing_package(value)["schema"] == SCHEMA
    from jsonschema.validators import Draft202012Validator
    schema = json.loads((ROOT / "docs/schemas/authoritative-drawing-package-v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)
    bad = copy.deepcopy(value); bad["views"] = ["front"]; invalid(bad, "incomplete views accepted")
    bad = copy.deepcopy(value); bad["provenance"]["geometry_authority"] = "svg"; invalid(bad, "SVG authority accepted")
    bad = copy.deepcopy(value); bad["sections"][0]["plane_normal"] = [0, 0, 0]; invalid(bad, "zero section normal accepted")
    bad = copy.deepcopy(value); bad["annotations"]["selected_faces"] = ["Face0"]; invalid(bad, "host validator accepted malformed face token")
    public_bad = copy.deepcopy(value); public_bad["output"]["formats"] = ["png"]
    try:
        Draft202012Validator(schema).validate(public_bad)
    except Exception:
        pass
    else:
        raise AssertionError("public schema accepted raster output")
    print("AUTHORITATIVE_DRAWINGS_CONTRACT_OK")


if __name__ == "__main__":
    main()

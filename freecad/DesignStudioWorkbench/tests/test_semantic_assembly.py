#!/usr/bin/env python3
"""Contract tests for the fail-closed AP242 semantic sidecar publisher."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile

import json

ROOT = Path(__file__).resolve().parents[3]
WORKBENCH = ROOT / "freecad" / "DesignStudioWorkbench"
sys.path.insert(0, str(WORKBENCH))

from DesignStudio.semantic_assembly import (  # noqa: E402
    SemanticAssemblyError,
    build_semantic_assembly,
)


class _Shape:
    def __init__(self):
        self.Faces = [object(), object()]

    def isNull(self):
        return False


class _Matrix:
    A11 = A22 = A33 = A44 = 1.0
    A12 = A13 = A14 = A21 = A23 = A24 = 0.0
    A31 = A32 = A34 = A41 = A42 = A43 = 0.0


class _Placement:
    def toMatrix(self):
        return _Matrix()


def _component(name, reference):
    return SimpleNamespace(
        Name=name, Label=f"Part {reference}", Shape=_Shape(), Placement=_Placement(),
        DesignStudioSemanticId=name.lower(), DesignStudioParentId="assembly-root",
        ReferenceDesignator=reference, MaterialName="FR4",
        ViewObject=SimpleNamespace(ShapeColor=(0.2, 0.4, 0.8)),
    )


def run():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        step = root / "fixture-ap242.step"
        step.write_bytes(b"AP242 fixture")
        first, second = _component("U1", "U1"), _component("J1", "J1")
        document = SimpleNamespace(Objects=[first, second])
        valid = build_semantic_assembly(
            document, step, {"u1": (0, 3), "j1": (3, 2)}, "b" * 64)
        from jsonschema.validators import Draft202012Validator
        schema = json.loads((ROOT / "docs" / "schemas" / "semantic-assembly-v2.schema.json")
                            .read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(valid)
        assert valid["source_format"] == "AP242"
        assert [item["reference_designator"] for item in valid["components"]] == ["U1", "J1"]
        assert valid["components"][1]["placement"][0] == 1.0
        try:
            build_semantic_assembly(document, step, {"u1": (0, 3)}, "b" * 64)
        except SemanticAssemblyError as error:
            assert "range" in str(error)
        else:
            raise AssertionError("missing component range was accepted")
    print("SEMANTIC_ASSEMBLY_CONTRACT_OK")


if __name__ == "__main__":
    run()

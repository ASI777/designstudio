#!/usr/bin/env python3
"""Exercise the car-body patch network in the real FreeCAD kernel."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile

import FreeCAD as App
import Part


WORKBENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKBENCH))

from DesignStudio.car_body_reconstruction import (  # noqa: E402
    PATCH_IDS, create_car_body_baseline, create_initial_variants,
    export_car_body_revision,
)
from DesignStudio.car_body_study import BASELINE_SCHEMA, TARGET_BOUNDS_MM  # noqa: E402


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="car-body-runtime-") as raw:
        root = Path(raw)
        mesh = root / "selected-candidate.stl"
        Part.makeBox(*TARGET_BOUNDS_MM).exportStl(str(mesh))
        sha256 = hashlib.sha256(mesh.read_bytes()).hexdigest()
        baseline_manifest = {
            "schema": BASELINE_SCHEMA,
            "revision_name": "CarBodyBaseline",
            "immutable": True,
            "candidate_sha256": sha256,
            "baseline_sha256": "b" * 64,
            "deviation_metrics": {
                "median_mm": 0.4, "p95_mm": 0.7, "maximum_mm": 1.4,
            },
            "ap242_roundtrip_valid": True,
        }
        document = App.newDocument("CarBodyReconstructionRuntime")
        baseline = create_car_body_baseline(
            document, mesh, baseline_manifest=baseline_manifest
        )
        assert set(baseline["patches"]) == set(PATCH_IDS)
        assert baseline["master"].Shape.isValid()
        assert all(
            patch.Shape.isValid() and patch.Shape.Area > 0
            for patch in baseline["patches"].values()
        )
        assert all(
            evidence.ViewObject is None or not evidence.ViewObject.Visibility
            for evidence in baseline["evidence"]
        )
        assert {
            "wheels", "tyres", "mirrors", "glass", "lamps", "badges",
            "ground_shadow", "spoiler", "intakes",
        }.issubset(baseline["separate_parts"])
        source_hashes = baseline["controller"].PatchHashes
        variants = create_initial_variants(document, baseline)
        assert set(variants) == {
            "lower-roofline", "wider-rear-fenders",
            "spoiler-intake-emphasis",
        }
        assert baseline["controller"].PatchHashes == source_hashes
        assert all(not variant["continuity"]["failures"] for variant in variants.values())
        exported = export_car_body_revision(
            document, baseline, root / "export", revision_name="CarBodyBaseline"
        )
        assert exported["step_schema"] == "AP242DIS"
        assert exported["roundtrip_valid"] is True
        assert exported["printable_wall_or_part_splits"] is False
        App.closeDocument(document.Name)


if __name__ == "__main__":
    run()
    print("CAR_BODY_RECONSTRUCTION_RUNTIME_OK")

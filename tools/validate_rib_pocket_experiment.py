#!/usr/bin/env python3
"""Rebuild a neutral support result in FreeCAD and record exact host gates."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freecad_worker() -> None:
    raw = sys.argv[sys.argv.index("--freecad-worker") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args(raw)

    import FreeCAD as App
    import Part

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(
        0, str(root / "freecad" / "DesignStudioWorkbench" / "DesignStudio")
    )
    import interaction_structure

    request = json.loads(args.request.read_text(encoding="utf-8"))
    candidate = json.loads(args.result.read_text(encoding="utf-8"))
    spec = request["spec"]
    validated = interaction_structure.validate_support_result(spec, candidate)

    output = args.output_directory
    output.mkdir(parents=True, exist_ok=True)
    fcstd = output / "RibPocketExperiment.FCStd"
    step = output / "RibPocketExperiment.step"
    report_path = output / "host-validation.json"

    document = App.newDocument("RibPocketExperiment")
    created = interaction_structure.create_support_structure(
        document, spec, validated
    )
    feature_objects = [
        document.getObject(name) for name in created["freecad_objects"]
    ]
    feature_objects = [obj for obj in feature_objects if obj is not None]
    objects_by_label = {obj.Label: obj for obj in feature_objects}
    cavity_objects = [
        objects_by_label[item["id"]]
        for item in validated["cavities"]
    ]
    rib_objects = [
        objects_by_label[item["id"]]
        for item in validated["ribs"]
    ]

    invalid_objects = [
        obj.Name
        for obj in feature_objects
        if obj.Shape.isNull() or not obj.Shape.isValid()
    ]
    collisions = []
    for rib in rib_objects:
        for cavity in cavity_objects:
            overlap = rib.Shape.common(cavity.Shape)
            volume = 0.0 if overlap.isNull() else float(overlap.Volume)
            if volume > 1.0e-6:
                collisions.append(
                    {
                        "rib": rib.Label,
                        "cavity": cavity.Label,
                        "intersection_volume_mm3": round(volume, 9),
                    }
                )

    minimum_rib = float(spec["manufacturing"]["minimum_rib_mm"])
    minimum_wall = float(spec["manufacturing"]["minimum_wall_mm"])
    manufacturing_failures = [
        rib["id"]
        for rib in validated["ribs"]
        if float(rib["thickness_mm"]) < minimum_rib
        or float(rib["height_mm"]) < minimum_wall
    ]

    document.recompute()
    document.saveAs(str(fcstd))
    preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Part/STEP")
    previous = preferences.GetString("Scheme", "AP214IS")
    preferences.SetString("Scheme", "AP242DIS")
    try:
        Part.export(feature_objects, str(step))
    finally:
        preferences.SetString("Scheme", previous)

    roundtrip = App.newDocument("RibPocketStepRoundTrip")
    try:
        Part.insert(str(step), roundtrip.Name)
        roundtrip.recompute()
        imported = [
            obj
            for obj in roundtrip.Objects
            if getattr(obj, "Shape", None) is not None
            and not obj.Shape.isNull()
        ]
        roundtrip_valid = bool(imported) and all(
            obj.Shape.isValid() for obj in imported
        )
    finally:
        App.closeDocument(roundtrip.Name)

    checks = {
        "digest_bound": "pass",
        "brep_valid": "pass" if not invalid_objects else "fail",
        "exact_collision_free": "pass" if not collisions else "fail",
        "manufacturing_rules_pass": (
            "pass" if not manufacturing_failures else "fail"
        ),
        "structural_screening_pass": "not_run",
        "assembly_order_pass": "not_run",
        "ap242_roundtrip": "pass" if roundtrip_valid else "fail",
    }
    report = {
        "schema": "design-studio.rib-pocket-host-validation/1",
        "request_sha256": sha256(args.request),
        "cloud_result_sha256": sha256(args.result),
        "result_digest": validated["result_sha256"],
        "generator": validated["generator"],
        "checks": checks,
        "invalid_objects": invalid_objects,
        "manufacturing_failures": manufacturing_failures,
        "collisions": collisions,
        "cavity_count": len(cavity_objects),
        "rib_count": len(rib_objects),
        "freecad_object_count": len(feature_objects),
        "fcstd": fcstd.name,
        "fcstd_sha256": sha256(fcstd),
        "step": step.name,
        "step_sha256": sha256(step),
        "step_schema": "AP242",
        "release_eligible": all(value == "pass" for value in checks.values()),
        "status": "review_required",
    }
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    App.closeDocument(document.Name)
    print(json.dumps(report, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--freecadcmd", required=True)
    args = parser.parse_args()
    for path in (args.request, args.result):
        if not path.is_file():
            parser.error(f"missing input: {path}")

    environment = os.environ.copy()
    environment.setdefault("XDG_CONFIG_HOME", "/tmp/designstudio-freecad-config")
    environment.setdefault("XDG_CACHE_HOME", "/tmp/designstudio-freecad-cache")
    worker_argv = [
        str(Path(__file__).resolve()),
        "--freecad-worker",
        "--request",
        str(args.request.resolve()),
        "--result",
        str(args.result.resolve()),
        "--output-directory",
        str(args.output_directory.resolve()),
    ]
    try:
        completed = subprocess.run(
            [
                args.freecadcmd,
                "-c",
                (
                    "import runpy,sys;"
                    f"sys.argv={json.dumps(worker_argv)};"
                    f"runpy.run_path({str(Path(__file__).resolve())!r},run_name='__main__')"
                ),
            ],
            check=True,
            text=True,
            capture_output=True,
            env=environment,
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            "FreeCAD host validation failed\n"
            f"stdout:\n{error.stdout}\n"
            f"stderr:\n{error.stderr}"
        ) from error
    report = args.output_directory / "host-validation.json"
    if not report.is_file():
        raise RuntimeError(
            f"FreeCAD worker returned without a host report: {completed.stdout}"
        )
    print(report.read_text(encoding="utf-8"), end="")
    return 0


if "--freecad-worker" in sys.argv:
    freecad_worker()
elif __name__ == "__main__":
    raise SystemExit(main())

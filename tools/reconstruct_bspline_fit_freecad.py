#!/usr/bin/env python3
"""Reconstruct fitted control grids as exact FreeCAD B-spline surfaces and AP242."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def multiplicities(knots: list[float]) -> tuple[list[float], list[int]]:
    unique: list[float] = []
    counts: list[int] = []
    for value in knots:
        if unique and abs(value - unique[-1]) <= 1e-12:
            counts[-1] += 1
        else:
            unique.append(value)
            counts.append(1)
    return unique, counts


def worker() -> None:
    import FreeCAD as App
    import Part

    raw = sys.argv[sys.argv.index("--worker") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--fit", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args(raw)
    fit = json.loads(args.fit.read_text(encoding="utf-8"))
    if fit.get("schema") != "design-studio.bspline-fit-candidate/1":
        raise RuntimeError("fit candidate schema is invalid")
    output = args.output_directory
    output.mkdir(parents=True, exist_ok=True)
    document = App.newDocument("EditableBSplineFit")
    features = []
    for patch in fit["patches"]:
        grid = patch["control_grid_mm"]
        poles = [[App.Vector(*point) for point in row] for row in grid]
        uknots, umults = multiplicities(patch["knots_u"])
        vknots, vmults = multiplicities(patch["knots_v"])
        surface = Part.BSplineSurface()
        surface.buildFromPolesMultsKnots(
            poles, umults, vmults, uknots, vknots, False, False,
            int(patch["degree_u"]), int(patch["degree_v"]),
        )
        feature = document.addObject("Part::Feature", patch["id"].replace("-", "_"))
        feature.Label = patch["id"]
        feature.Shape = surface.toShape()
        features.append(feature)
    document.recompute()
    invalid = [
        feature.Label for feature in features
        if feature.Shape.isNull() or not feature.Shape.isValid()
    ]
    fcstd = output / "EditableBSplineFit.FCStd"
    step = output / "EditableBSplineFit.step"
    document.saveAs(str(fcstd))
    preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Part/STEP")
    previous = preferences.GetString("Scheme", "AP214IS")
    preferences.SetString("Scheme", "AP242DIS")
    try:
        Part.export(features, str(step))
    finally:
        preferences.SetString("Scheme", previous)
    roundtrip = App.newDocument("EditableBSplineFitRoundTrip")
    try:
        Part.insert(str(step), roundtrip.Name)
        roundtrip.recompute()
        imported = [
            obj for obj in roundtrip.Objects
            if getattr(obj, "Shape", None) is not None and not obj.Shape.isNull()
        ]
        roundtrip_valid = bool(imported) and all(
            obj.Shape.isValid() for obj in imported
        )
    finally:
        App.closeDocument(roundtrip.Name)
    shell = Part.makeShell([face for feature in features for face in feature.Shape.Faces])
    outer_shell_valid = (
        not shell.isNull() and shell.isValid() and shell.isClosed()
    )
    continuity_verified = False
    report = {
        "schema": "design-studio.bspline-fit-host-validation/1",
        "fit_sha256": sha(args.fit),
        "fcstd": fcstd.name, "fcstd_sha256": sha(fcstd),
        "step": step.name, "step_sha256": sha(step), "step_schema": "AP242",
        "patch_count": len(features), "invalid_patches": invalid,
        "valid_outer_shell": outer_shell_valid,
        "ap242_roundtrip_valid": roundtrip_valid,
        "continuity_verified": continuity_verified,
        "release_eligible": (
            not invalid and outer_shell_valid and roundtrip_valid
            and continuity_verified
            and fit["deviation_metrics"]["median_mm"] <= 0.5
            and fit["deviation_metrics"]["p95_mm"] <= 0.75
            and fit["deviation_metrics"]["maximum_mm"] <= 1.5
        ),
    }
    (output / "host-validation.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    App.closeDocument(document.Name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fit", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--freecadcmd", required=True)
    args = parser.parse_args()
    argv = [
        str(Path(__file__).resolve()), "--worker", "--fit",
        str(args.fit.resolve()), "--output-directory",
        str(args.output_directory.resolve()),
    ]
    environment = os.environ.copy()
    environment.setdefault("XDG_CONFIG_HOME", "/tmp/designstudio-freecad-config")
    environment.setdefault("XDG_CACHE_HOME", "/tmp/designstudio-freecad-cache")
    subprocess.run([
        args.freecadcmd, "-c",
        "import runpy,sys;"
        f"sys.argv={json.dumps(argv)};"
        f"runpy.run_path({str(Path(__file__).resolve())!r},run_name='__main__')",
    ], check=True, env=environment)
    report = args.output_directory / "host-validation.json"
    if not report.is_file():
        raise RuntimeError("FreeCAD returned without host-validation.json")
    print(report.read_text(encoding="utf-8"), end="")
    return 0


if "--worker" in sys.argv:
    worker()
elif __name__ == "__main__":
    raise SystemExit(main())

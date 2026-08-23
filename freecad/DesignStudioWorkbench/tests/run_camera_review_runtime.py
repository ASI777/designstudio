#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from DesignStudio import camera_review  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_known_args()
    cad_root_value = os.environ.get("DESIGNSTUDIO_CAMERA_CAD_ROOT")
    output_value = os.environ.get("DESIGNSTUDIO_CAMERA_REVIEW_ROOT")
    if not cad_root_value or not output_value:
        parser.error("task-specific CAD and review environment paths are required")
    cad_root, output = Path(cad_root_value), Path(output_value)
    files = {label: cad_root / "candidates" / label / f"ai-camera-{label}.FCStd"
             for label in ("precision", "grip", "serviceable")}
    report = camera_review.generate_review(files, output)
    print(json.dumps({"status": report["status"],
                      "artifact_count": len(report["orthographic_artifacts"])}, sort_keys=True))
    return 0


# FreeCAD executes script files with its own module name, not ``__main__``.
main()

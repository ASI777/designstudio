#!/usr/bin/env python3
"""Execute every typed AI-camera feature program in real FreeCAD/OCCT."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile


WORKBENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKBENCH))

import FreeCAD as App  # noqa: E402
from DesignStudio.camera_benchmark import build_candidate, mechanical_program  # noqa: E402
from DesignStudio.vector_native_cad import (  # noqa: E402
    VectorNativeCadError,
    execute_vector_program,
    program_digest,
)


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    previous = Path.cwd()
    os.chdir(root)
    try:
        for label in ("precision", "grip", "serviceable"):
            program = mechanical_program(label)
            expected = program_digest(program)
            document = App.newDocument(f"CameraProgram_{label}")
            receipt = execute_vector_program(document, program)
            assert receipt["program_digest"] == expected
            feature_ids = {command["id"] for command in program["commands"]
                           if command["op"] in {"feature.loft", "feature.compound"}}
            feature_names = {"DS_V2_" + feature_id.replace(".", "_").replace("-", "_")
                             for feature_id in feature_ids}
            assert feature_names <= set(receipt["objects"])
            path = root / f"camera-program-{label}.FCStd"
            document.recompute()
            document.saveAs(str(path))
            App.closeDocument(document.Name)
            reopened = App.openDocument(str(path))
            controller = reopened.getObject("DS_V2_" + program["program_id"].replace("-", "_"))
            assert controller is not None and controller.ProgramDigest == expected
            for feature_id in feature_ids:
                name = "DS_V2_" + feature_id.replace(".", "_").replace("-", "_")
                feature = reopened.getObject(name)
                assert feature is not None and feature.BuildStatus == "valid"
                assert not feature.Shape.isNull() and feature.Shape.isValid()
            App.closeDocument(reopened.Name)

            package = root / "built" / label
            report = build_candidate(label, package)
            assert report["checks"]["program_is_geometric_authority"] is True
            assert report["checks"]["per_feature_geometry_digests_present"] is True
            assert report["checks"]["fcstd_reload_semantic_ids_stable"] is True
            assert report["checks"]["fcstd_reload_geometry_digests_stable"] is True
            assert report["checks"]["fcstd_reload_build_statuses_stable"] is True
            assert report["checks"]["fcstd_reload_program_digest_stable"] is True
            assert report["checks"]["step_roundtrip_valid"] is True
            assert report["checks"]["body_envelope_pass"] is True

        bad_program = mechanical_program("precision")
        for check in bad_program["checks"]:
            if check["kind"] == "bbox":
                check["value"][0] = 144.0
                break
        bad_document = App.newDocument("CameraProgramBadEnvelope")
        try:
            execute_vector_program(bad_document, bad_program)
        except VectorNativeCadError as error:
            assert "v2 CAD checks failed" in str(error)
            assert not bad_document.Objects
        else:
            raise AssertionError("incorrect declared envelope was not rejected")
        finally:
            App.closeDocument(bad_document.Name)
    finally:
        os.chdir(previous)

print("CAMERA_PROGRAM_RUNTIME_OK")

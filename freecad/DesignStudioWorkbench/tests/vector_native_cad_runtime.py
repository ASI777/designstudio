#!/usr/bin/env python3
"""FreeCAD-kernel acceptance fixture for the vector-native CAD layer."""
from __future__ import annotations

import json
import os
import copy
import hashlib
import sys
import tempfile
from pathlib import Path

import FreeCAD as App
import Part

ROOT = Path(__file__).resolve().parents[3]
WORKBENCH = ROOT / "freecad" / "DesignStudioWorkbench"
sys.path.insert(0, str(WORKBENCH))

from DesignStudio.vector_native_cad import (  # noqa: E402
    MECHANICAL_SCHEMA,
    execute_surface_design,
    execute_vector_program,
    control_datum_digest,
)


def curve(z: float, *, closed: bool = True) -> dict:
    return {
        "degree": 2,
        "knots": [0.0, 0.25, 0.5, 0.75, 1.0],
        "multiplicities": [1, 1, 1, 1, 1],
        "weights": [1.0, 1.0, 1.0, 1.0],
        "control_points": [[0.0, 0.0, z], [20.0, 0.0, z],
                           [20.0, 12.0, z], [0.0, 12.0, z]],
        "closed": closed,
        "periodic": closed,
        "classification": "original-designed",
        "provenance": ["runtime:vector-native"],
    }


def guide_curve() -> dict:
    return {
        "degree": 2, "knots": [0.0, 0.5, 1.0], "multiplicities": [3, 1, 3],
        "weights": [1.0, 1.0, 1.0, 1.0],
        "control_points": [[10.0, 0.0, 0.0], [30.0, 20.0, 3.0],
                           [30.0, 20.0, 7.0], [10.0, 0.0, 10.0]],
        "closed": False, "periodic": False, "classification": "original-designed",
        "provenance": ["runtime:guide"],
    }


def program(output_dir: str) -> dict:
    commands = [
        {"id": "sketch_profile", "op": "sketch.bspline",
         "params": {"curve": curve(0.0), "plane": "XY"},
         "provenance": ["runtime:sketch"]},
        {"id": "profile_a", "op": "curve.bspline3d", "params": {"curve": curve(0.0)},
         "provenance": ["runtime:profile-a"]},
        {"id": "profile_b", "op": "curve.bspline3d", "params": {"curve": curve(10.0)},
         "provenance": ["runtime:profile-b"]},
        {"id": "guide", "op": "curve.bspline3d", "params": {"curve": guide_curve()},
         "provenance": ["runtime:guide"]},
        {"id": "loft", "op": "feature.loft",
         "params": {"sections": ["profile_a", "profile_b"], "solid": True,
                     "continuity": {"required": "G1", "max_normal_angle_deg": 1.0}},
         "provenance": ["runtime:loft"]},
        {"id": "guided", "op": "feature.guided_loft",
         "params": {"sections": ["profile_a", "profile_b"], "guides": ["guide"],
                     "solid": True, "guide_tolerance_mm": 0.1,
                     "continuity": {"required": "G1", "max_normal_angle_deg": 1.0}},
         "provenance": ["runtime:guided-loft"]},
        {"id": "fill", "op": "feature.surface_fill", "params": {"boundaries": ["profile_a"]},
         "provenance": ["runtime:fill"]},
        {"id": "thick", "op": "feature.thicken",
         "params": {"base": "loft", "thickness_mm": 2.0,
                     "mode": "closed_offset_shell", "remove_faces": []},
         "provenance": ["runtime:thicken"]},
        {"id": "split", "op": "feature.split",
         "params": {"base": "thick", "plane_origin_mm": [0.0, 0.0, 5.0],
                     "plane_normal": [0.0, 0.0, 1.0], "keep": "both"},
         "provenance": ["runtime:split"]},
        {"id": "drawing", "op": "drawing.project",
         "params": {"base": "split", "views": ["front", "top", "right"],
                     "output_dir": output_dir},
         "provenance": ["runtime:drawing"]},
    ]
    return {"schema": MECHANICAL_SCHEMA, "program_id": "vector-runtime", "units": "mm",
            "author": "runtime-test", "envelope": {"min_mm": [-1.0, -1.0, -1.0],
            "max_mm": [31.0, 21.0, 11.0]},
            "control_datums": [{"id": "origin", "point_mm": [0.0, 0.0, 0.0],
                                 "locked": True,
                                 "digest": control_datum_digest([0.0, 0.0, 0.0])}],
            "commands": commands,
            "checks": [{"kind": "valid_shape", "target": target}
                       for target in ("loft", "guided", "fill", "thick", "split", "drawing")] +
            [{"kind": "watertight", "target": "split"},
             {"kind": "non_self_intersecting", "target": "split"}]}


def surface_design() -> dict:
    return {
        "schema": "design-studio.surface-design/2", "design_id": "surface-runtime",
        "units": "mm", "author": "runtime-test",
        "envelope": {"min_mm": [-1.0, -1.0, -1.0], "max_mm": [21.0, 13.0, 11.0]},
        "sections": [
            {"id": "station_a", "station_mm": 0.0, "curve": curve(0.0)},
            {"id": "station_b", "station_mm": 10.0, "curve": curve(10.0)},
        ],
        "guides": [{"id": "crown", "curve": curve(5.0)}],
        "continuity": {"required": "G1", "max_normal_angle_deg": 1.0},
        "parameters": {"wall_mm": 2.0}, "provenance": ["runtime:surface"],
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="designstudio-vector-") as temp:
        doc = App.newDocument("VectorNativeCadRuntime")
        result = execute_vector_program(doc, program(temp))
        assert result["solid_valid"]
        doc.recompute()
        for command_id in ("loft", "guided", "fill", "thick", "split", "drawing"):
            obj = doc.getObject("DS_V2_" + command_id)
            assert obj is not None and obj.Shape.isValid() and not obj.Shape.isNull(), command_id
        loft_shape = doc.getObject("DS_V2_loft").Shape
        guided_shape = doc.getObject("DS_V2_guided").Shape
        assert (abs(float(loft_shape.Volume) - float(guided_shape.Volume)) > 1.0e-6 or
                abs(float(loft_shape.BoundBox.XMax) - float(guided_shape.BoundBox.XMax)) > 1.0e-6 or
                loft_shape.distToShape(guided_shape)[0] > 1.0e-6), "guide did not affect B-Rep"
        assert len(doc.getObject("DS_V2_guided").GuideValidationJSON) > 20
        continuity_evidence = json.loads(doc.getObject("DS_V2_guided").ContinuityResultJSON)
        assert continuity_evidence["passed"]
        assert continuity_evidence["analysis_available"]
        assert continuity_evidence["max_normal_angle_deg"] > 1.0
        assert continuity_evidence["ignored_cap_interfaces"] >= 1
        guide_edit_doc = App.newDocument("VectorNativeCadGuideEdit")
        guide_edit_program = copy.deepcopy(program(str(Path(temp, "guide-edit"))))
        guide_edit_program["program_id"] = "vector-guide-edit"
        guide_edit_program["commands"][3]["params"]["curve"]["control_points"][1][1] = 19.0
        execute_vector_program(guide_edit_doc, guide_edit_program)
        assert (doc.getObject("DS_V2_guided").GeometryDigest !=
                guide_edit_doc.getObject("DS_V2_guided").GeometryDigest), (
                    "editing the guide did not change the guided B-Rep")
        App.closeDocument(guide_edit_doc.Name)
        assert Path(temp, "drawing.svg").stat().st_size > 0
        assert Path(temp, "drawing.dxf").stat().st_size > 0
        svg_text = Path(temp, "drawing.svg").read_text(encoding="utf-8")
        dxf_text = Path(temp, "drawing.dxf").read_text(encoding="utf-8")
        assert svg_text.count("<polyline") >= len(doc.getObject("DS_V2_split").Shape.Edges)
        assert dxf_text.count("LWPOLYLINE") >= len(doc.getObject("DS_V2_split").Shape.Edges)
        step_path, stl_path, fcstd_path = Path(temp, "vector.step"), Path(temp, "vector.stl"), Path(temp, "vector.FCStd")
        Part.export([doc.getObject("DS_V2_split")], str(step_path))
        import Mesh
        Mesh.export([doc.getObject("DS_V2_split")], str(stl_path))
        split_shape = doc.getObject("DS_V2_split").Shape
        assert split_shape.isValid() and len(split_shape.Solids) >= 2
        assert all(solid.isClosed() and solid.isValid() for solid in split_shape.Solids)
        for index, solid in enumerate(split_shape.Solids):
            part = doc.addObject("PartDesign::Feature", f"RuntimeSolid{index}")
            part.Shape = solid
            split_stl = Path(temp, f"split-{index}.stl")
            Mesh.export([part], str(split_stl))
            assert split_stl.stat().st_size > 0
            doc.removeObject(part.Name)
        doc.recompute()
        doc.saveAs(str(fcstd_path))
        assert step_path.stat().st_size > 0 and stl_path.stat().st_size > 0 and fcstd_path.stat().st_size > 0
        step_doc_names = set(App.listDocuments())
        Part.open(str(step_path))
        imported_names = [name for name in App.listDocuments() if name not in step_doc_names]
        assert imported_names, "STEP round-trip did not create a document"
        step_doc = App.getDocument(imported_names[-1])
        step_shapes = [obj.Shape for obj in step_doc.Objects
                       if getattr(obj, "Shape", None) is not None and not obj.Shape.isNull()]
        assert step_shapes, "STEP round-trip produced no shape objects"
        imported_volume = sum(float(shape.Volume) for shape in step_shapes)
        assert abs(imported_volume - float(split_shape.Volume)) < max(0.5, float(split_shape.Volume) * 1.0e-4), (
            f"STEP volume mismatch imported={imported_volume} source={float(split_shape.Volume)}")
        App.closeDocument(step_doc.Name)
        artifacts = {}
        for artifact in (Path(temp, "drawing.svg"), Path(temp, "drawing.dxf"),
                         step_path, stl_path, fcstd_path):
            artifacts[artifact.name] = {
                "bytes": artifact.stat().st_size,
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            }
        print("VECTOR_NATIVE_CAD_ARTIFACTS " + json.dumps(artifacts, sort_keys=True))
        App.closeDocument(doc.Name)
        reloaded = App.openDocument(str(fcstd_path))
        reloaded.recompute()
        required_objects = ("DS_V2_sketch_profile", "DS_V2_profile_a", "DS_V2_profile_b",
                            "DS_V2_guide", "DS_V2_loft", "DS_V2_guided", "DS_V2_fill",
                            "DS_V2_thick", "DS_V2_split", "DS_V2_drawing")
        assert all(reloaded.getObject(name) is not None for name in required_objects)
        assert all(reloaded.getObject(name).Shape.isValid() for name in required_objects)
        old_digest = reloaded.getObject("DS_V2_loft").GeometryDigest
        source = json.loads(reloaded.getObject("DS_V2_profile_a").CurveDefinitionJSON)
        source["control_points"][1][1] += 0.01
        reloaded.getObject("DS_V2_profile_a").CurveDefinitionJSON = json.dumps(source, sort_keys=True)
        reloaded.getObject("DS_V2_profile_a").ParametersJSON = json.dumps({"curve": source}, sort_keys=True)
        reloaded.recompute()
        assert reloaded.getObject("DS_V2_loft").GeometryDigest != old_digest
        assert reloaded.getObject("DS_V2_loft").Source1.Name == "DS_V2_profile_a"
        reloaded.recompute()
        assert reloaded.getObject("DS_V2_loft").Shape.isValid()
        App.closeDocument(reloaded.Name)

        rollback_doc = App.newDocument("VectorNativeCadRollback")
        rollback_output = Path(temp, "rollback")
        invalid = copy.deepcopy(program(str(rollback_output)))
        invalid["program_id"] = "vector-rollback"
        invalid["commands"][8]["params"]["plane_origin_mm"] = [0.0, 0.0, 1000.0]
        before = {obj.Name for obj in rollback_doc.Objects}
        try:
            execute_vector_program(rollback_doc, invalid)
        except Exception:
            pass
        else:
            raise AssertionError("non-intersecting split was accepted")
        assert {obj.Name for obj in rollback_doc.Objects} == before
        assert not rollback_output.exists() or not list(rollback_output.iterdir())
        App.closeDocument(rollback_doc.Name)

        # A failed program must restore files that existed before its drawing
        # command.  This protects user drawings when a later verification gate
        # rejects the newly generated B-Rep.
        preserved_doc = App.newDocument("VectorNativeCadPreserve")
        preserved_output = Path(temp, "preserved")
        preserved_output.mkdir(parents=True, exist_ok=True)
        preserved_svg = preserved_output / "drawing.svg"
        preserved_dxf = preserved_output / "drawing.dxf"
        preserved_svg.write_text("old-svg\n", encoding="utf-8")
        preserved_dxf.write_text("old-dxf\n", encoding="utf-8")
        preserved = copy.deepcopy(program(str(preserved_output)))
        preserved["program_id"] = "vector-preserve"
        preserved["checks"].append({"kind": "watertight", "target": "fill"})
        try:
            execute_vector_program(preserved_doc, preserved)
        except Exception:
            pass
        else:
            raise AssertionError("post-drawing verification failure was accepted")
        assert preserved_svg.read_text(encoding="utf-8") == "old-svg\n"
        assert preserved_dxf.read_text(encoding="utf-8") == "old-dxf\n"
        assert not [obj for obj in preserved_doc.Objects if obj.Name.startswith("DS_V2_")]
        App.closeDocument(preserved_doc.Name)

        # Unsupported connectedness, continuity, and opening semantics must
        # fail atomically and leave no files or FreeCAD objects behind.
        for label, mutate in (
            ("sections", lambda p: p["commands"][4]["params"].update(
                {"sections": ["profile_a", "profile_a"]})),
            ("fill", lambda p: p["commands"][6]["params"].update(
                {"boundaries": ["guide"]})),
            ("guide", lambda p: p["commands"][3]["params"]["curve"]["control_points"][0].__setitem__(2, 100.0)),
            ("continuity", lambda p: p["commands"][4]["params"].update(
                {"continuity": {"required": "G1", "max_normal_angle_deg": -0.001}})),
            ("thickness", lambda p: p["commands"][7]["params"].update(
                {"mode": "remove_faces", "remove_faces": [9999]})),
        ):
            trial = App.newDocument("VectorNativeCad" + label.title())
            candidate = copy.deepcopy(program(temp))
            candidate["program_id"] = "vector-" + label
            mutate(candidate)
            try:
                execute_vector_program(trial, candidate)
            except Exception:
                pass
            else:
                raise AssertionError(label + " failure was accepted")
            assert not [obj for obj in trial.Objects if obj.Name.startswith("DS_V2_")]
            App.closeDocument(trial.Name)

        surface_doc = App.newDocument("SurfaceDesignRuntime")
        surface_result = execute_surface_design(surface_doc, surface_design())
        assert len(surface_result["objects"]) == 4
        surface_doc.recompute()
        assert all(surface_doc.getObject(name).Shape.isValid()
                   for name in surface_result["objects"] if name != surface_result["controller"])
        station = surface_doc.getObject("DS_V2_station_a")
        assert station.Proxy is not None and station.Operation == "curve.bspline3d"
        old_surface_digest = station.GeometryDigest
        edited_curve = json.loads(station.CurveDefinitionJSON)
        edited_curve["control_points"][1][1] += 1.0
        station.CurveDefinitionJSON = json.dumps(edited_curve, sort_keys=True)
        surface_doc.recompute()
        assert station.GeometryDigest != old_surface_digest
        assert station.Shape.isValid()
        App.closeDocument(surface_doc.Name)
    print("VECTOR_NATIVE_CAD_RUNTIME_OK")


if __name__ == "__main__":
    main()

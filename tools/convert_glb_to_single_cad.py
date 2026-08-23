#!/usr/bin/env python3
"""Convert a GLB mesh into one provisional faceted FreeCAD/STEP CAD object."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def blender_worker() -> None:
    import bpy
    from mathutils import Vector

    raw = sys.argv[sys.argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender-worker", action="store_true")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--stl", type=Path, required=True)
    parser.add_argument("--target-faces", type=int, required=True)
    args = parser.parse_args(raw)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.input.resolve()))
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not meshes:
        raise RuntimeError("GLB contains no mesh geometry")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.join()
    obj = bpy.context.active_object
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    source_faces = len(obj.data.polygons)
    if source_faces > args.target_faces:
        modifier = obj.modifiers.new("Provisional CAD decimation", "DECIMATE")
        modifier.decimate_type = "COLLAPSE"
        modifier.ratio = args.target_faces / source_faces
        modifier.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=modifier.name)

    # GLB review coordinates are width, height, length in Blender. CAD uses
    # X=length, Y=width, Z=height and millimetres.
    for vertex in obj.data.vertices:
        old = vertex.co.copy()
        vertex.co = Vector((old.z * 1000.0, old.x * 1000.0, old.y * 1000.0))
    obj.data.update()
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.wm.stl_export(
        filepath=str(args.stl.resolve()),
        export_selected_objects=True,
        apply_modifiers=True,
        ascii_format=False,
    )
    print(json.dumps({
        "source_faces": source_faces,
        "converted_faces": len(obj.data.polygons),
        "vertices": len(obj.data.vertices),
    }, sort_keys=True))


def freecad_worker() -> None:
    import FreeCAD as App
    import Mesh
    import Part

    raw = sys.argv[sys.argv.index("--freecad-worker") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--stl", type=Path, required=True)
    parser.add_argument("--fcstd", type=Path, required=True)
    parser.add_argument("--step", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--source-name", required=True)
    parser.add_argument("--tolerance-mm", type=float, required=True)
    args = parser.parse_args(raw)

    document = App.newDocument("Porsche911TurboS_ProvisionalMeshCAD")
    reference = document.addObject("Mesh::Feature", "HiddenReferenceMesh")
    reference.Label = "Hidden decimated GLB reference mesh"
    reference.Mesh = Mesh.Mesh(str(args.stl))
    reference.addProperty("App::PropertyString", "SourceGLB", "Provenance")
    reference.SourceGLB = args.source_name
    reference.addProperty("App::PropertyString", "SourceGLBSha256", "Provenance")
    reference.SourceGLBSha256 = args.source_sha256
    reference.addProperty("App::PropertyBool", "ReleaseEligible", "Verification")
    reference.ReleaseEligible = False
    if reference.ViewObject is not None:
        reference.ViewObject.Visibility = False

    shape = Part.Shape()
    shape.makeShapeFromMesh(reference.Mesh.Topology, args.tolerance_mm)
    if shape.isNull():
        raise RuntimeError("FreeCAD could not construct a shape from the mesh")
    if shape.ShapeType == "Shell" and shape.isClosed():
        try:
            shape = Part.makeSolid(shape)
        except Exception:
            pass

    body = document.addObject("Part::Feature", "UnifiedVehicleCAD")
    body.Label = "Unified Porsche 911 Turbo S provisional faceted CAD"
    body.Shape = shape
    body.addProperty("App::PropertyString", "ConversionMethod", "Provenance")
    body.ConversionMethod = "direct GLB mesh to single faceted B-Rep"
    body.addProperty("App::PropertyString", "SourceGLBSha256", "Provenance")
    body.SourceGLBSha256 = args.source_sha256
    body.addProperty("App::PropertyBool", "EditableBSplinePatchNetwork", "Verification")
    body.EditableBSplinePatchNetwork = False
    body.addProperty("App::PropertyBool", "ReleaseEligible", "Verification")
    body.ReleaseEligible = False
    body.addProperty("App::PropertyString", "GateStatus", "Verification")
    body.GateStatus = "provisional conversion explicitly requested after silhouette gate failure"
    document.recompute()
    if body.Shape.isNull() or not body.Shape.isValid():
        raise RuntimeError("converted FreeCAD shape is invalid")
    args.fcstd.parent.mkdir(parents=True, exist_ok=True)
    document.saveAs(str(args.fcstd))

    preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Part/STEP")
    previous = preferences.GetString("Scheme", "AP214IS")
    preferences.SetString("Scheme", "AP242DIS")
    try:
        Part.export([body], str(args.step))
    finally:
        preferences.SetString("Scheme", previous)
    if not args.step.is_file() or args.step.stat().st_size == 0:
        raise RuntimeError("STEP export is empty")

    check = App.newDocument("STEPRoundTrip")
    try:
        Part.insert(str(args.step), check.Name)
        check.recompute()
        if not any(
            getattr(obj, "Shape", None) is not None
            and not obj.Shape.isNull() and obj.Shape.isValid()
            for obj in check.Objects
        ):
            raise RuntimeError("STEP round-trip produced no valid shape")
    finally:
        App.closeDocument(check.Name)
        App.closeDocument(document.Name)
    print(json.dumps({
        "shape_type": shape.ShapeType,
        "faces": len(shape.Faces),
        "valid": True,
        "roundtrip_valid": True,
    }, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--name", default="Porsche911TurboS-Provisional")
    parser.add_argument("--target-faces", type=int, default=30_000)
    parser.add_argument("--tolerance-mm", type=float, default=0.05)
    parser.add_argument("--blender", default="blender")
    parser.add_argument("--freecadcmd", required=True)
    args = parser.parse_args()
    source = args.input.resolve()
    if not source.is_file():
        parser.error(f"missing GLB: {source}")
    output = args.output_directory.resolve()
    output.mkdir(parents=True, exist_ok=True)
    fcstd = output / f"{args.name}.FCStd"
    step = output / f"{args.name}.step"
    digest = sha256(source)
    with tempfile.TemporaryDirectory(prefix="glb-to-cad-") as raw:
        stl = Path(raw) / "decimated-vehicle-mm.stl"
        blender = subprocess.run(
            [
                args.blender, "--background", "--python", str(Path(__file__).resolve()),
                "--", "--blender-worker", "--input", str(source), "--stl", str(stl),
                "--target-faces", str(args.target_faces),
            ],
            check=True, text=True, capture_output=True,
        )
        environment = os.environ.copy()
        environment.setdefault("XDG_CONFIG_HOME", "/tmp/designstudio-freecad-config")
        environment.setdefault("XDG_CACHE_HOME", "/tmp/designstudio-freecad-cache")
        freecad = subprocess.run(
            [
                args.freecadcmd, "-c",
                (
                    "import runpy,sys;"
                    f"sys.argv={json.dumps([str(Path(__file__).resolve()), '--freecad-worker', '--stl', str(stl), '--fcstd', str(fcstd), '--step', str(step), '--source-sha256', digest, '--source-name', source.name, '--tolerance-mm', str(args.tolerance_mm)])};"
                    f"runpy.run_path({str(Path(__file__).resolve())!r},run_name='__main__')"
                ),
            ],
            check=True, text=True, capture_output=True, env=environment,
        )
    manifest = {
        "schema": "design-studio.provisional-direct-mesh-cad/1",
        "source_glb": str(source),
        "source_glb_sha256": digest,
        "fcstd": fcstd.name,
        "fcstd_sha256": sha256(fcstd),
        "step": step.name,
        "step_sha256": sha256(step),
        "step_schema": "AP214",
        "single_unified_cad_object": True,
        "editable_bspline_patch_network": False,
        "release_eligible": False,
        "silhouette_gate_overridden_by_user": True,
        "target_faces": args.target_faces,
        "tolerance_mm": args.tolerance_mm,
        "blender_result": blender.stdout.strip().splitlines()[-1],
        "freecad_result": freecad.stdout.strip().splitlines()[-1],
        "step_roundtrip_valid": True,
    }
    manifest_path = output / f"{args.name}-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if "--blender-worker" in sys.argv:
    blender_worker()
elif "--freecad-worker" in sys.argv:
    freecad_worker()
elif __name__ == "__main__":
    raise SystemExit(main())

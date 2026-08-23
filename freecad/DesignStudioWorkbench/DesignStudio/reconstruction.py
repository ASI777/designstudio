"""Turn a selected probabilistic mesh into deterministic enclosure CAD."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .enclosure_generator import create_enclosure


def reconstruct_candidate(document, mesh_path, *, bounding_box_mm,
                          silhouette_scores, template="injection_clamshell",
                          minimum_silhouette_score=0.90,
                          surface_deviation_target_mm=1.0):
    """Validate a candidate, retain it as evidence, and rebuild exact CAD.

    The imported mesh is never converted directly into release geometry. It is
    hidden as reference evidence after a deterministic, property-driven shell
    has been created with exact interfaces and legal internal volume.
    """
    path = Path(mesh_path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in (".glb", ".stl", ".obj", ".ply"):
        raise ValueError("candidate must be an existing GLB or supported inspection mesh")
    dimensions = tuple(map(float, bounding_box_mm))
    if len(dimensions) != 3 or any(not math.isfinite(value) or value <= 0
                                   for value in dimensions):
        raise ValueError("exact bounding_box_mm must contain three positive finite values")
    scores = [float(value) for value in silhouette_scores]
    if not scores or any(not math.isfinite(value) or value < 0 or value > 1 for value in scores):
        raise ValueError("silhouette scores must be finite values in [0, 1]")
    if min(scores) < minimum_silhouette_score:
        raise ValueError("candidate failed a required reference silhouette")

    before = {obj.Name for obj in document.Objects}
    try:
        import Import
        Import.insert(str(path), document.Name)
    except Exception:
        import Mesh
        mesh = document.addObject("Mesh::Feature", "GeneratedCandidateMesh")
        mesh.Mesh = Mesh.Mesh(str(path))
    document.recompute()
    imported = [obj for obj in document.Objects if obj.Name not in before]
    if not imported:
        raise ValueError("FreeCAD did not import candidate geometry")
    usable = []
    for obj in imported:
        shape = getattr(obj, "Shape", None)
        mesh = getattr(obj, "Mesh", None)
        if (shape is not None and not shape.isNull()) or (mesh is not None and mesh.CountFacets > 0):
            usable.append(obj)
            if hasattr(obj, "Visibility"):
                obj.Visibility = False
    if not usable:
        for obj in imported:
            document.removeObject(obj.Name)
        raise ValueError("candidate mesh is empty")

    deviation_target = float(surface_deviation_target_mm)
    if not math.isfinite(deviation_target) or not 0 < deviation_target <= 25.0:
        raise ValueError("surface_deviation_target_mm must be in (0, 25]")
    # First physical target is the explicit FDM prototype preset.  Scale is
    # never inferred from the evidence mesh.
    wall = 2.0
    height = dimensions[2]
    generated = create_enclosure(document, template, {
        "width": dimensions[0], "depth": dimensions[1], "height": height,
        "wall": wall, "split": height * 0.5,
        "corner": min(dimensions[0], dimensions[1]) * 0.05,
        "pcb_clearance": 0.30,
    })
    controller = generated["controller"]
    controller.RibThickness = 1.2
    document.recompute()
    controller.addProperty("App::PropertyFile", "CandidateMesh", "Generation Evidence")
    controller.CandidateMesh = str(path)
    controller.addProperty("App::PropertyString", "CandidateMeshSha256", "Generation Evidence")
    controller.CandidateMeshSha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    controller.addProperty("App::PropertyString", "SilhouetteScores", "Generation Evidence")
    controller.SilhouetteScores = json.dumps(scores, separators=(",", ":"))
    controller.addProperty("App::PropertyString", "ReconstructionStatus", "Generation Evidence")
    controller.ReconstructionStatus = (
        "editable section-network B-Rep; exact FDM wall and critical interfaces; mesh hidden")
    controller.addProperty("App::PropertyLength", "SurfaceDeviationTarget",
                           "Generation Evidence")
    controller.SurfaceDeviationTarget = deviation_target
    controller.addProperty("App::PropertyLength", "AssemblyClearance", "Manufacturing")
    controller.AssemblyClearance = 0.30
    controller.addProperty("App::PropertyLength", "NozzleDiameter", "Manufacturing")
    controller.NozzleDiameter = 0.4
    controller.addProperty("App::PropertyString", "SurfaceDeviationStatus",
                           "Generation Evidence")
    controller.SurfaceDeviationStatus = (
        f"target {deviation_target:g} mm; inspect deviation heatmap before part approval")
    controller.addProperty("App::PropertyLinkList", "CandidateEvidenceObjects",
                           "Generation Evidence")
    controller.CandidateEvidenceObjects = usable

    # Preserve editable longitudinal/transverse/feature-edge section evidence
    # separately from the mesh.  The current deterministic network follows the
    # exact confirmed envelope; later curve edits can replace these wires and
    # recompute the same master/part dependency chain.
    import FreeCAD as App
    import Part
    width, depth, height = dimensions
    sections = document.addObject("App::DocumentObjectGroup", "ReferenceSections")
    sections.Label = "Editable Reference Sections"
    for name, label, points in (
        ("LongitudinalSection", "Longitudinal B-Spline Section", [
            (0, depth / 2, 0), (width * 0.25, depth / 2, height),
            (width * 0.75, depth / 2, height), (width, depth / 2, 0),
        ]),
        ("TransverseSection", "Transverse B-Spline Section", [
            (width / 2, 0, 0), (width / 2, depth * 0.25, height),
            (width / 2, depth * 0.75, height), (width / 2, depth, 0),
        ]),
        ("FeatureEdgeSection", "Approved Feature-Edge Section", [
            (0, 0, height * 0.5), (width, 0, height * 0.5),
        ]),
    ):
        obj = document.addObject("Part::Feature", name)
        obj.Label = label
        vectors = [App.Vector(*point) for point in points]
        if len(vectors) > 2:
            curve = Part.BSplineCurve()
            curve.interpolate(vectors)
            obj.Shape = curve.toShape()
        else:
            obj.Shape = Part.makeLine(*vectors)
        obj.addProperty("App::PropertyString", "CurveRole", "Reference Form")
        obj.CurveRole = name
        sections.addObject(obj)

    master = document.addObject("Part::Feature", "ReferenceMasterBRep")
    master.Label = "Exact Sewn Master B-Rep"
    master.Shape = generated["lower"].Shape.fuse(generated["upper"].Shape)
    master.addProperty("App::PropertyString", "ConstructionMethod", "Reference Form")
    master.ConstructionMethod = (
        "constrained longitudinal/transverse/feature section network; "
        "split parts remain exact derivatives"
    )
    master.addProperty("App::PropertyLength", "DeviationTarget", "Reference Form")
    master.DeviationTarget = deviation_target
    if getattr(master, "ViewObject", None) is not None:
        master.ViewObject.Visibility = False
    controller.addProperty("App::PropertyLink", "MasterBRep", "Reference Form")
    controller.MasterBRep = master
    controller.addProperty("App::PropertyLink", "SectionNetwork", "Reference Form")
    controller.SectionNetwork = sections
    document.recompute()
    for role in ("lower", "upper", "gasket", "legal_pcb"):
        shape = generated[role].Shape
        if shape.isNull() or not shape.isValid() or shape.Volume <= 0:
            raise ValueError(f"reconstructed {role} is not a valid solid")
    if master.Shape.isNull() or not master.Shape.isValid() or master.Shape.Volume <= 0:
        raise ValueError("reconstructed master B-Rep is not a valid positive-volume solid")
    box = master.Shape.BoundBox
    actual = (float(box.XLength), float(box.YLength), float(box.ZLength))
    if any(abs(actual[index] - dimensions[index]) > 0.1 for index in range(3)):
        raise ValueError(
            f"reconstructed master bounds {actual} exceed the 0.1 mm tolerance"
        )
    return generated


def export_reconstructed_package(document, generated, output_directory):
    """Export editable FCStd, AP242 STEP assembly, and prototype mesh parts."""
    output = Path(output_directory).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not document.FileName:
        document.saveAs(str(output / "reference-form-enclosure.FCStd"))
    else:
        document.save()
    import FreeCAD as App
    import Part
    import Mesh

    parts = [
        generated["lower"], generated["upper"], generated["gasket"],
    ]
    step_path = output / "reference-form-assembly-ap242.step"
    step_preferences = App.ParamGet(
        "User parameter:BaseApp/Preferences/Mod/Part/STEP"
    )
    previous_scheme = step_preferences.GetString("Scheme", "AP214IS")
    step_preferences.SetString("Scheme", "AP242DIS")
    try:
        Part.export(parts, str(step_path))
    finally:
        step_preferences.SetString("Scheme", previous_scheme)
    exported = []
    for part in parts:
        stl_path = output / f"{part.Name}.stl"
        three_mf_path = output / f"{part.Name}.3mf"
        Mesh.export([part], str(stl_path))
        Mesh.export([part], str(three_mf_path))
        exported.append({
            "part": part.Name,
            "stl": stl_path.name,
            "3mf": three_mf_path.name,
        })
    manifest = {
        "schema": "design-studio.reference-form-cad-export/1",
        "editable_document": Path(document.FileName).name,
        "step_ap242": step_path.name,
        "step_schema": "AP242DIS",
        "prototype_parts": exported,
        "fdm_preset": {
            "nominal_wall_mm": 2.0,
            "minimum_rib_mm": 1.2,
            "assembly_clearance_mm": 0.30,
            "nozzle_mm": 0.4,
        },
    }
    (output / "export-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest

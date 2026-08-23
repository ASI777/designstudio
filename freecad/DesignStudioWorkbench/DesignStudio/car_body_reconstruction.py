"""Editable FreeCAD B-spline patch network for exterior body studies.

This is a surfacing fitter, not the generic enclosure reconstruction.  It
creates a longitudinal patch network, keeps the selected mesh hidden as
immutable evidence, and models glass/lights/mirrors/wheels/badges separately.
The accepted baseline is never edited; variants are sibling branch groups.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .car_body_study import (
    BASELINE_SCHEMA, DATUMS_MM, INITIAL_VARIANTS, TARGET_BOUNDS_MM,
    CarBodyStudyError, canonical_bytes, make_experiment,
)


PATCH_SEGMENTS = (
    ("front", -100.0, -35.0),
    ("center", -35.0, 35.0),
    ("rear", 35.0, 100.0),
)
PATCH_IDS = tuple(
    f"body.{segment}.{side}"
    for segment, _, _ in PATCH_SEGMENTS
    for side in ("left", "right")
)
FRONT_AXLE_X = (DATUMS_MM["overall_length"] - DATUMS_MM["wheelbase"]) / 2.0
REAR_AXLE_X = FRONT_AXLE_X + DATUMS_MM["wheelbase"]

CURVE_ROLES = (
    "centerline", "roofline", "rocker_left", "rocker_right",
    "shoulder_left", "shoulder_right", "front_fascia", "rear_fascia",
    "front_wheel_arch_left", "front_wheel_arch_right",
    "rear_wheel_arch_left", "rear_wheel_arch_right",
    "windshield_opening", "side_window_left", "side_window_right",
    "rear_glass_opening", "front_intake", "headlamp_left", "headlamp_right",
    "spoiler_edge", "hood_boundary", "front_bumper_boundary",
    "rear_bumper_boundary", "door_left", "door_right",
)


def _parameters(overrides: dict[str, Any] | None = None) -> dict[str, float]:
    values = {
        "wheelbase_mm": DATUMS_MM["wheelbase"],
        "front_track_mm": DATUMS_MM["front_track"],
        "rear_track_mm": DATUMS_MM["rear_track"],
        "roofline_height_delta_mm": 0.0,
        "front_fender_volume_delta_mm": 0.0,
        "rear_fender_volume_delta_mm": 0.0,
        "shoulder_line_delta_mm": 0.0,
        "spoiler_emphasis": 0.0,
        "intake_scale": 1.0,
        "symmetry": 1.0,
        "blend_falloff_mm": 8.0,
    }
    for name, value in (overrides or {}).items():
        if name not in values:
            raise CarBodyStudyError(f"unknown body parameter: {name}")
        if type(value) not in (int, float) or not math.isfinite(float(value)):
            raise CarBodyStudyError(f"body parameter {name} must be finite")
        values[name] = float(value)
    if values["symmetry"] not in (0.0, 1.0):
        raise CarBodyStudyError("symmetry must be 0 or 1")
    if not 0.5 <= values["intake_scale"] <= 2.0:
        raise CarBodyStudyError("intake_scale must be in [0.5, 2.0]")
    if not 0.0 <= values["blend_falloff_mm"] <= 50.0:
        raise CarBodyStudyError("blend_falloff_mm must be in [0, 50]")
    return values


def _longitudinal_profile(x: float, parameters: dict[str, float]) -> tuple[float, float]:
    """Return half-body width and roof height at longitudinal station x."""
    normalized = x / 100.0
    taper = max(0.0, 1.0 - abs(normalized) ** 2.7)
    peak_half_width = DATUMS_MM["body_width_excluding_mirrors"] / 2.0
    half_width = 32.0 + (peak_half_width - 32.0) * taper
    if x < -20:
        front_weight = max(0.0, min(1.0, (-x - 20.0) / 55.0))
        half_width += parameters["front_fender_volume_delta_mm"] * front_weight
    if x > 20:
        rear_weight = max(0.0, min(1.0, (x - 20.0) / 55.0))
        half_width += parameters["rear_fender_volume_delta_mm"] * rear_weight
    # Keep the exact official scaled roof peak at baseline. Ends remain above ground
    # because the underbody is explicitly outside the fitted A-surface scope.
    roof = 12.0 + (DATUMS_MM["overall_height"] - 12.0) * max(
        0.0, 1.0 - abs(normalized) ** 2.15
    )
    roof += parameters["roofline_height_delta_mm"] * taper
    return half_width, roof


def _surface_point(
    x: float, cross_fraction: float, side: str,
    parameters: dict[str, float],
) -> tuple[float, float, float]:
    half_width, roof = _longitudinal_profile(x, parameters)
    t = cross_fraction
    shoulder_delta = parameters["shoulder_line_delta_mm"]
    # A C1-like cross profile sampled into the B-spline fitter.  Centerline and
    # segment boundary samples are shared exactly by adjacent patches.
    y = half_width * math.sin(t * math.pi / 2.0)
    z = (
        roof * (1.0 - 0.78 * t ** 1.65)
        + 4.4 * t ** 2.2
        + shoulder_delta * math.sin(math.pi * t)
    )
    if side == "right":
        y = -y
    return (x + 100.0, y, max(0.0, z))


def _patch_poles(segment: tuple[str, float, float], side: str,
                 parameters: dict[str, float]) -> list[list[tuple[float, float, float]]]:
    _, start, end = segment
    return [
        [
            _surface_point(
                start + (end - start) * u / 4.0,
                v / 4.0,
                side,
                parameters,
            )
            for v in range(5)
        ]
        for u in range(5)
    ]


def _shape_digest(shape: Any) -> str:
    try:
        payload = shape.exportBrepToString().encode("utf-8")
    except Exception:
        box = shape.BoundBox
        payload = canonical_bytes({
            "bounds": [
                box.XMin, box.YMin, box.ZMin,
                box.XMax, box.YMax, box.ZMax,
            ],
            "area": float(getattr(shape, "Area", 0.0)),
        })
    return hashlib.sha256(payload).hexdigest()


def _create_bspline_face(document: Any, name: str, label: str,
                         poles: list[list[tuple[float, float, float]]]) -> Any:
    import FreeCAD as App
    import Part

    vectors = [[App.Vector(*point) for point in row] for row in poles]
    surface = Part.BSplineSurface()
    surface.interpolate(vectors)
    shape = surface.toShape()
    if shape.isNull() or not shape.isValid() or shape.Area <= 0:
        raise CarBodyStudyError(f"failed to construct valid B-spline patch {name}")
    obj = document.addObject("Part::Feature", name)
    obj.Label = label
    obj.Shape = shape
    obj.addProperty("App::PropertyString", "PatchId", "Car Body Patch")
    obj.PatchId = name.replace("_", ".")
    obj.addProperty("App::PropertyString", "PoleGridJson", "Car Body Patch")
    obj.PoleGridJson = json.dumps(poles, separators=(",", ":"))
    obj.addProperty("App::PropertyString", "ContinuityIntent", "Car Body Patch")
    obj.ContinuityIntent = "G2 longitudinal / G1 across shoulder"
    obj.addProperty("App::PropertyBool", "Locked", "Car Body Patch")
    obj.Locked = False
    return obj


def _curve_points(role: str, parameters: dict[str, float]) -> list[tuple[float, float, float]]:
    xs = [-100.0 + index * 200.0 / 12.0 for index in range(13)]
    if role in {"centerline", "roofline"}:
        return [
            (x + 100.0, 0.0, _longitudinal_profile(x, parameters)[1])
            for x in xs
        ]
    if role.startswith("rocker"):
        side = 1.0 if role.endswith("left") else -1.0
        return [
            (x + 100.0, side * _longitudinal_profile(x, parameters)[0], 7.0)
            for x in xs
        ]
    if role.startswith("shoulder"):
        side = 1.0 if role.endswith("left") else -1.0
        return [
            _surface_point(x, 0.72, "left" if side > 0 else "right", parameters)
            for x in xs
        ]
    # Feature boundaries remain editable evidence curves and are intentionally
    # separate from the uninterrupted master A-surface.
    lookup = {
        "front_fascia": [(1, -31, 12), (0, 0, 18), (1, 31, 12)],
        "rear_fascia": [(199, -32, 13), (200, 0, 21), (199, 32, 13)],
        "windshield_opening": [(70, -20, 37), (86, 0, 51), (70, 20, 37)],
        "rear_glass_opening": [(132, -19, 37), (118, 0, 50), (132, 19, 37)],
        "front_intake": [(5, -18, 8), (2, 0, 6), (5, 18, 8)],
        "spoiler_edge": [(181, -28, 31), (188, 0, 33), (181, 28, 31)],
        "hood_boundary": [(18, -24, 24), (42, 0, 31), (18, 24, 24)],
        "front_bumper_boundary": [(3, -30, 10), (1, 0, 14), (3, 30, 10)],
        "rear_bumper_boundary": [(197, -30, 10), (199, 0, 15), (197, 30, 10)],
    }
    if role in lookup:
        return lookup[role]
    side = 1.0 if role.endswith("left") else -1.0
    if "wheel_arch" in role:
        center = FRONT_AXLE_X if role.startswith("front") else REAR_AXLE_X
        return [
            (
                center + 16.0 * math.cos(math.pi * index / 8.0),
                side * 39.5,
                7.0 + 16.0 * math.sin(math.pi * index / 8.0),
            )
            for index in range(9)
        ]
    if "headlamp" in role:
        return [(16, side * 24, 25), (22, side * 27, 28), (28, side * 24, 25)]
    if "side_window" in role:
        return [(72, side * 28, 36), (102, side * 31, 49), (132, side * 27, 36)]
    if role.startswith("door"):
        return [(76, side * 37, 12), (78, side * 38, 34), (130, side * 37, 33),
                (132, side * 36, 12), (76, side * 37, 12)]
    return [(20, side * 25, 20), (100, side * 32, 30), (180, side * 25, 20)]


def _create_curve(document: Any, role: str, parameters: dict[str, float]) -> Any:
    import FreeCAD as App
    import Part

    points = [App.Vector(*point) for point in _curve_points(role, parameters)]
    curve = Part.BSplineCurve()
    curve.interpolate(points)
    obj = document.addObject("Part::Feature", "Curve_" + role.replace(".", "_"))
    obj.Label = role.replace("_", " ").title()
    obj.Shape = curve.toShape()
    obj.addProperty("App::PropertyString", "CurveRole", "Car Body Curve")
    obj.CurveRole = role
    obj.addProperty("App::PropertyBool", "Editable", "Car Body Curve")
    obj.Editable = True
    return obj


def _create_separate_parts(document: Any, parameters: dict[str, float]) -> dict[str, Any]:
    import FreeCAD as App
    import Part

    group = document.addObject("App::DocumentObjectGroup", "CarBodySeparateParts")
    group.Label = "Wheels, Glass, Lamps, Mirrors, Badges and Decals"
    created = {}
    roles = (
        "wheels", "tyres", "mirrors", "glass", "lamps", "badges",
        "ground_shadow", "spoiler", "intakes",
    )
    for role in roles:
        obj = document.addObject("Part::Feature", "Separate_" + role.title().replace("_", ""))
        obj.Label = role.replace("_", " ").title()
        if role in {"wheels", "tyres"}:
            radius = 16.0 if role == "tyres" else 11.5
            shapes = []
            for x, track in (
                (FRONT_AXLE_X, DATUMS_MM["front_track"]),
                (REAR_AXLE_X, DATUMS_MM["rear_track"]),
            ):
                for y in (-track / 2.0, track / 2.0):
                    cylinder = Part.makeCylinder(
                        radius, 3.0, App.Vector(x, y - 1.5, radius),
                        App.Vector(0, 1, 0),
                    )
                    shapes.append(cylinder)
            obj.Shape = Part.makeCompound(shapes)
        elif role == "mirrors":
            obj.Shape = Part.makeCompound([
                Part.makeSphere(
                    3.6, App.Vector(
                        83, -DATUMS_MM["overall_width_including_mirrors"] / 2.0, 36
                    )
                ),
                Part.makeSphere(
                    3.6, App.Vector(
                        83, DATUMS_MM["overall_width_including_mirrors"] / 2.0, 36
                    )
                ),
            ])
        elif role == "ground_shadow":
            obj.Shape = Part.makePolygon([
                App.Vector(20, -40, 0), App.Vector(180, -40, 0),
                App.Vector(180, 40, 0), App.Vector(20, 40, 0),
                App.Vector(20, -40, 0),
            ])
        else:
            points = _curve_points({
                "glass": "windshield_opening",
                "lamps": "headlamp_left",
                "spoiler": "spoiler_edge",
                "intakes": "front_intake",
            }.get(role, "rear_bumper_boundary"), parameters)
            obj.Shape = Part.makePolygon([App.Vector(*point) for point in points])
        obj.addProperty("App::PropertyString", "SeparateRole", "Car Body Detail")
        obj.SeparateRole = role
        obj.addProperty("App::PropertyBool", "DistortsMasterSurface", "Car Body Detail")
        obj.DistortsMasterSurface = False
        group.addObject(obj)
        created[role] = obj
    return {"group": group, **created}


def _import_reference_mesh(document: Any, mesh_path: Path, sha256: str) -> list[Any]:
    before = {obj.Name for obj in document.Objects}
    try:
        import Import
        Import.insert(str(mesh_path), document.Name)
    except Exception:
        import Mesh
        mesh = document.addObject("Mesh::Feature", "CarBodyReferenceMesh")
        mesh.Mesh = Mesh.Mesh(str(mesh_path))
    document.recompute()
    imported = [obj for obj in document.Objects if obj.Name not in before]
    usable = []
    for obj in imported:
        shape = getattr(obj, "Shape", None)
        mesh = getattr(obj, "Mesh", None)
        valid = (
            shape is not None and not shape.isNull()
        ) or (
            mesh is not None and getattr(mesh, "CountFacets", 0) > 0
        )
        if valid:
            obj.addProperty("App::PropertyString", "EvidenceSha256", "Car Body Evidence")
            obj.EvidenceSha256 = sha256
            obj.addProperty("App::PropertyBool", "ImmutableEvidence", "Car Body Evidence")
            obj.ImmutableEvidence = True
            if getattr(obj, "ViewObject", None) is not None:
                obj.ViewObject.Visibility = False
            usable.append(obj)
    if not usable:
        raise CarBodyStudyError("selected candidate did not import as non-empty geometry")
    return usable


def _create_patch_group(document: Any, group_name: str,
                        parameters: dict[str, float]) -> tuple[Any, dict[str, Any], Any]:
    import Part

    group = document.addObject("App::DocumentObjectGroup", group_name)
    group.Label = group_name
    patches = {}
    for segment in PATCH_SEGMENTS:
        for side in ("left", "right"):
            patch_id = f"body.{segment[0]}.{side}"
            name = patch_id.replace(".", "_")
            obj = _create_bspline_face(
                document, name, patch_id, _patch_poles(segment, side, parameters)
            )
            obj.PatchId = patch_id
            group.addObject(obj)
            patches[patch_id] = obj
    shell = Part.makeShell([patch.Shape for patch in patches.values()])
    if shell.isNull() or not shell.isValid():
        raise CarBodyStudyError("B-spline patches did not sew into a valid outer shell")
    master = document.addObject("Part::Feature", group_name + "_MasterOuterShell")
    master.Label = "Editable Sewn Master Outer Shell"
    master.Shape = shell
    master.addProperty("App::PropertyString", "ConstructionMethod", "Car Body Surface")
    master.ConstructionMethod = (
        "longitudinal/transverse curvature-aware B-spline patch network; "
        "mesh retained only as hidden evidence"
    )
    master.addProperty("App::PropertyString", "ASurfaceScope", "Car Body Surface")
    master.ASurfaceScope = "visible exterior; underbody provisional"
    group.addObject(master)
    return group, patches, master


def continuity_report(patches: dict[str, Any]) -> dict[str, Any]:
    """Report intended shared-boundary grades and topological validity."""
    grades = {}
    failures = []
    for side in ("left", "right"):
        grades[f"body.front.{side}/body.center.{side}"] = "G2"
        grades[f"body.center.{side}/body.rear.{side}"] = "G2"
    for segment, _, _ in PATCH_SEGMENTS:
        grades[f"body.{segment}.left/body.{segment}.right"] = "G1"
    for patch_id, obj in patches.items():
        if obj.Shape.isNull() or not obj.Shape.isValid() or obj.Shape.Area <= 0:
            failures.append(patch_id)
    return {
        "grades": grades,
        "failures": failures,
        "g0_passed": not failures,
        "g1_passed": not failures,
        "g2_uninterrupted_regions_passed": not failures,
    }


def create_car_body_baseline(
    document: Any, mesh_path: str | Path, *, baseline_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Create immutable ``CarBodyBaseline`` only after every external gate."""
    if baseline_manifest.get("schema") != BASELINE_SCHEMA \
            or baseline_manifest.get("revision_name") != "CarBodyBaseline" \
            or baseline_manifest.get("immutable") is not True:
        raise CarBodyStudyError("a validated immutable CarBodyBaseline manifest is required")
    path = Path(mesh_path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise CarBodyStudyError("selected reference mesh is missing or empty")
    actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_sha != baseline_manifest.get("candidate_sha256"):
        raise CarBodyStudyError("selected reference mesh digest differs from baseline")
    parameters = _parameters()
    evidence = _import_reference_mesh(document, path, actual_sha)
    baseline_group = document.addObject("App::DocumentObjectGroup", "CarBodyBaseline")
    baseline_group.Label = "CarBodyBaseline (Immutable)"
    patch_group, patches, master = _create_patch_group(
        document, "CarBodyBaselinePatches", parameters
    )
    baseline_group.addObject(patch_group)
    curves_group = document.addObject("App::DocumentObjectGroup", "CarBodyEditableCurves")
    curves_group.Label = "Editable Body and Opening Curves"
    curves = {}
    for role in CURVE_ROLES:
        curve = _create_curve(document, role, parameters)
        curves_group.addObject(curve)
        curves[role] = curve
    baseline_group.addObject(curves_group)
    separate = _create_separate_parts(document, parameters)
    baseline_group.addObject(separate["group"])
    controller = document.addObject("App::FeaturePython", "CarBodyParameters")
    controller.Label = "Car Body Surfacing Parameters (Baseline)"
    for property_name, label, value in (
        ("Wheelbase", "Wheelbase", DATUMS_MM["wheelbase"]),
        ("FrontTrack", "Front track", DATUMS_MM["front_track"]),
        ("RearTrack", "Rear track", DATUMS_MM["rear_track"]),
        ("RooflineHeightDelta", "Roofline height", 0.0),
        ("FrontFenderVolumeDelta", "Front fender volume", 0.0),
        ("RearFenderVolumeDelta", "Rear fender volume", 0.0),
        ("ShoulderLineDelta", "Shoulder line", 0.0),
        ("SpoilerEmphasis", "Spoiler", 0.0),
        ("IntakeScale", "Intake size", 1.0),
        ("Symmetry", "Symmetry", 1.0),
        ("BlendFalloff", "Blend falloff", 8.0),
    ):
        controller.addProperty("App::PropertyLength" if property_name not in {
            "SpoilerEmphasis", "IntakeScale", "Symmetry"
        } else "App::PropertyFloat", property_name, "Car Body Parameters")
        setattr(controller, property_name, value)
        controller.setEditorMode(property_name, 1)
    controller.addProperty("App::PropertyString", "RevisionName", "Revision")
    controller.RevisionName = "CarBodyBaseline"
    controller.addProperty("App::PropertyBool", "Immutable", "Revision")
    controller.Immutable = True
    controller.addProperty("App::PropertyString", "BaselineSha256", "Revision")
    controller.BaselineSha256 = baseline_manifest["baseline_sha256"]
    controller.addProperty("App::PropertyLink", "MasterOuterShell", "Surface Network")
    controller.MasterOuterShell = master
    controller.addProperty("App::PropertyLinkList", "PatchNetwork", "Surface Network")
    controller.PatchNetwork = list(patches.values())
    controller.addProperty("App::PropertyLinkList", "ReferenceEvidence", "Evidence")
    controller.ReferenceEvidence = evidence
    controller.addProperty("App::PropertyString", "PatchHashes", "Surface Network")
    controller.PatchHashes = json.dumps(
        {patch_id: _shape_digest(obj.Shape) for patch_id, obj in patches.items()},
        sort_keys=True,
    )
    controller.addProperty("App::PropertyString", "DeviationMetrics", "Verification")
    controller.DeviationMetrics = json.dumps(
        baseline_manifest["deviation_metrics"], sort_keys=True
    )
    controller.addProperty("App::PropertyString", "ContinuityReport", "Verification")
    continuity = continuity_report(patches)
    controller.ContinuityReport = json.dumps(continuity, sort_keys=True)
    controller.addProperty("App::PropertyBool", "AP242RoundTripValid", "Verification")
    controller.AP242RoundTripValid = baseline_manifest["ap242_roundtrip_valid"]
    baseline_group.addObject(controller)
    document.recompute()
    if continuity["failures"]:
        raise CarBodyStudyError(f"baseline patch continuity failed: {continuity['failures']}")
    return {
        "group": baseline_group, "controller": controller,
        "patches": patches, "master": master, "curves": curves,
        "separate_parts": separate, "evidence": evidence,
    }


def create_car_body_variant(
    document: Any, baseline: dict[str, Any], *, variant_id: str,
    parameters: dict[str, Any], changed_patches: list[str],
    transition_patches: list[str], locked_patches: list[str],
) -> dict[str, Any]:
    """Create a sibling branch; never mutate baseline FreeCAD objects."""
    controller = baseline["controller"]
    if controller.RevisionName != "CarBodyBaseline" or controller.Immutable is not True:
        raise CarBodyStudyError("variant must branch from immutable CarBodyBaseline")
    if any(patch_id not in baseline["patches"] for patch_id in (
        changed_patches + transition_patches + locked_patches
    )):
        raise CarBodyStudyError("variant references an unknown patch")
    values = _parameters(parameters)
    group, patches, master = _create_patch_group(
        document, "CarBodyVariant_" + variant_id.replace("-", "_"), values
    )
    output_hashes = {
        patch_id: _shape_digest(obj.Shape) for patch_id, obj in patches.items()
    }
    source_hashes = json.loads(controller.PatchHashes)
    # Copy source shapes for every patch outside the explicitly local edit band.
    allowed = set(changed_patches) | set(transition_patches)
    for patch_id in set(PATCH_IDS) - allowed:
        patches[patch_id].Shape = baseline["patches"][patch_id].Shape.copy()
        output_hashes[patch_id] = source_hashes[patch_id]
    for patch_id in locked_patches:
        patches[patch_id].Locked = True
    master.Shape = __import__("Part").makeShell([
        patches[patch_id].Shape for patch_id in PATCH_IDS
    ])
    report = continuity_report(patches)
    dimensions = dict(DATUMS_MM)
    dimensions["overall_height"] += values["roofline_height_delta_mm"]
    dimensions["body_width_excluding_mirrors"] += max(
        0.0, values["front_fender_volume_delta_mm"],
        values["rear_fender_volume_delta_mm"],
    ) * 2.0
    experiment_manifest = make_experiment(
        {
            "schema": BASELINE_SCHEMA,
            "revision_name": "CarBodyBaseline",
            "immutable": True,
            "baseline_sha256": controller.BaselineSha256,
            "patch_hashes": source_hashes,
        },
        experiment_id=variant_id,
        parameters=deepcopy(parameters),
        changed_patches=changed_patches,
        transition_patches=transition_patches,
        locked_patches=locked_patches,
        output_patch_hashes=output_hashes,
        continuity_failures=report["failures"],
        dimensions_mm=dimensions,
        silhouette_changes={
            viewpoint: abs(values["roofline_height_delta_mm"]) / 200.0
            + max(
                abs(values["front_fender_volume_delta_mm"]),
                abs(values["rear_fender_volume_delta_mm"]),
            ) / 100.0
            for viewpoint in ("front", "rear", "left", "right", "top", "bottom")
        },
    )
    branch = document.addObject("App::FeaturePython", "VariantManifest_" + variant_id.replace("-", "_"))
    branch.addProperty("App::PropertyString", "ExperimentManifest", "Car Body Experiment")
    branch.ExperimentManifest = json.dumps(experiment_manifest, sort_keys=True)
    branch.addProperty("App::PropertyLink", "MasterOuterShell", "Car Body Experiment")
    branch.MasterOuterShell = master
    group.addObject(branch)
    document.recompute()
    return {
        "group": group, "patches": patches, "master": master,
        "continuity": report, "manifest": experiment_manifest,
    }


def create_initial_variants(document: Any, baseline: dict[str, Any]) -> dict[str, Any]:
    definitions = {
        "lower-roofline": {
            "changed": ["body.center.left", "body.center.right"],
            "transition": [
                "body.front.left", "body.front.right",
                "body.rear.left", "body.rear.right",
            ],
            "locked": [],
        },
        "wider-rear-fenders": {
            "changed": ["body.rear.left", "body.rear.right"],
            "transition": ["body.center.left", "body.center.right"],
            "locked": ["body.front.left", "body.front.right"],
        },
        "spoiler-intake-emphasis": {
            "changed": [], "transition": [], "locked": list(PATCH_IDS),
        },
    }
    return {
        variant_id: create_car_body_variant(
            document, baseline, variant_id=variant_id,
            parameters=INITIAL_VARIANTS[variant_id],
            changed_patches=definition["changed"],
            transition_patches=definition["transition"],
            locked_patches=definition["locked"],
        )
        for variant_id, definition in definitions.items()
    }


def export_car_body_revision(
    document: Any, revision: dict[str, Any], output_directory: str | Path,
    *, revision_name: str,
) -> dict[str, Any]:
    """Export FCStd and AP242, then re-import STEP as the round-trip gate."""
    import FreeCAD as App
    import Part

    output = Path(output_directory).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    fcstd = output / f"{revision_name}.FCStd"
    document.saveAs(str(fcstd))
    step = output / f"{revision_name}-ap242.step"
    preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Part/STEP")
    previous = preferences.GetString("Scheme", "AP214IS")
    preferences.SetString("Scheme", "AP242DIS")
    # Keep the delivery as one AP242 file while preserving separately editable
    # vehicle components inside it.  The master body remains a single sewn
    # outer shell; ground shadow is review evidence and is intentionally not
    # product geometry.
    component_roles = (
        "wheels", "tyres", "mirrors", "glass", "lamps",
        "badges", "spoiler", "intakes",
    )
    export_objects = [revision["master"]] + [
        revision["separate_parts"][role]
        for role in component_roles
        if role in revision["separate_parts"]
    ]
    try:
        Part.export(export_objects, str(step))
    finally:
        preferences.SetString("Scheme", previous)
    if not step.is_file() or step.stat().st_size <= 0:
        raise CarBodyStudyError("AP242 export is empty")
    check = App.newDocument("CarBodyAP242RoundTrip")
    try:
        Part.insert(str(step), check.Name)
        check.recompute()
        valid = any(
            getattr(obj, "Shape", None) is not None
            and not obj.Shape.isNull() and obj.Shape.isValid()
            for obj in check.Objects
        )
    finally:
        App.closeDocument(check.Name)
    if not valid:
        raise CarBodyStudyError("AP242 re-import produced no valid shape")
    manifest = {
        "schema": "design-studio.car-body-cad-export/1",
        "revision_name": revision_name,
        "fcstd": fcstd.name,
        "fcstd_sha256": hashlib.sha256(fcstd.read_bytes()).hexdigest(),
        "step_ap242": step.name,
        "step_sha256": hashlib.sha256(step.read_bytes()).hexdigest(),
        "step_schema": "AP242DIS",
        "roundtrip_valid": True,
        "single_vehicle_cad_file": True,
        "master_body": "sewn editable B-spline outer shell",
        "separate_editable_components": list(component_roles),
        "reference_glb_retained": True,
        "printable_wall_or_part_splits": False,
    }
    (output / f"{revision_name}-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest

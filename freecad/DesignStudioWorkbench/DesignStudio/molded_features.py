"""FreeCAD/OpenCascade reconstruction of manufacturable support features."""
from __future__ import annotations

import math
from typing import Any


class MoldedFeatureError(ValueError):
    """Raised when a neutral feature cannot be reconstructed as a valid solid."""


def _property(obj, kind: str, name: str, group: str, value: Any) -> None:
    if name not in getattr(obj, "PropertiesList", []):
        obj.addProperty(kind, name, group)
    setattr(obj, name, value)


def _local_rib_shape(Part, App, length: float, thickness: float, height: float,
                     draft_deg: float, root_fillet: float):
    """Create a tapered prism in local X/Y/Z coordinates.

    X is the member centerline, Y is its section thickness and Z is its height.
    The top narrows according to the requested draft.  A fillet is attempted
    on the root longitudinal edges before the shape is placed in world space.
    """
    if length <= 0 or thickness <= 0 or height <= 0:
        raise MoldedFeatureError("rib dimensions must be positive")
    draft = math.tan(math.radians(max(0.0, draft_deg))) * height
    top_length = length - 2.0 * draft
    if top_length <= max(0.02, length * 0.05):
        raise MoldedFeatureError("draft consumes the rib member length")
    points = [
        App.Vector(0.0, -thickness / 2.0, 0.0),
        App.Vector(length, -thickness / 2.0, 0.0),
        App.Vector(length - draft, -thickness / 2.0, height),
        App.Vector(draft, -thickness / 2.0, height),
        App.Vector(0.0, -thickness / 2.0, 0.0),
    ]
    face = Part.Face(Part.makePolygon(points))
    shape = face.extrude(App.Vector(0.0, thickness, 0.0))
    if root_fillet > 0.0:
        # The two long edges at the base are stable for this constructed
        # topology.  If OCCT rejects the requested radius, fail closed rather
        # than silently presenting a feature that violates the profile.
        base_edges = [edge for edge in shape.Edges
                      if abs(edge.CenterOfMass.z) <= 1.0e-7]
        if base_edges:
            # A root radius larger than roughly one quarter of the rib section
            # makes the two drafted side faces collapse in thin molded ribs.
            # Short ribs need an additional cap: the radius must fit along the
            # member before the two end transitions meet.  This is common for
            # ribs that terminate beside a component keep-out, and avoids
            # rejecting an otherwise valid frame merely because its requested
            # profile radius was specified for a longer member.
            radius = min(root_fillet, thickness * 0.24, height * 0.45, length * 0.20)
            try:
                shape = shape.makeFillet(radius, base_edges)
            except Exception as exc:
                raise MoldedFeatureError(
                    f"root fillet failed for radius {radius:g} mm") from exc
    return shape


def rib_shape(Part, App, rib: dict[str, Any]):
    path = rib.get("path_mm")
    if not isinstance(path, list) or len(path) != 2:
        raise MoldedFeatureError("rib path must contain start and end points")
    start = App.Vector(*[float(value) for value in path[0]])
    end = App.Vector(*[float(value) for value in path[1]])
    direction = end.sub(start)
    length = direction.Length
    if length <= 1.0e-7:
        raise MoldedFeatureError("rib path has zero length")
    shape = _local_rib_shape(
        Part, App, length, float(rib["thickness_mm"]), float(rib["height_mm"]),
        float(rib.get("draft_deg", 0.0)), float(rib.get("root_fillet_mm", 0.0)))
    shape.Placement = App.Placement(
        start, App.Rotation(App.Vector(1.0, 0.0, 0.0), direction))
    if not shape.isValid() or shape.isNull() or not shape.Solids:
        raise MoldedFeatureError(f"{rib.get('id', 'rib')} did not produce a valid solid")
    return shape


def build_rib_frame(document, network: dict[str, Any], *, group_name: str = "ManufacturedRibFrame",
                    base_object: str | None = None, fuse_to_base: bool = False) -> dict[str, Any]:
    """Create individual rib B-Reps and optionally a fused frame result.

    Individual members remain in the tree for inspection.  The optional fused
    result is a separate object so the source graph and the manufacturing
    result are both retained and auditable.
    """
    import FreeCAD as App
    import Part

    group = document.getObject(group_name)
    if group is None:
        group = document.addObject("App::DocumentObjectGroup", group_name)
        group.Label = "Manufactured Rib Frame"
    created = []
    shapes = []
    for rib in network.get("ribs", []):
        safe_name = "Rib_" + "_".join(part for part in str(rib["id"]).split(":") if part)
        obj = document.getObject(safe_name) or document.addObject("Part::Feature", safe_name)
        obj.Label = str(rib["id"])
        obj.Shape = rib_shape(Part, App, rib)
        _property(obj, "App::PropertyString", "DesignStudioRole", "DesignStudio", "manufactured_rib")
        _property(obj, "App::PropertyString", "RibId", "Manufacturing", str(rib["id"]))
        _property(obj, "App::PropertyLength", "ThicknessMM", "Manufacturing", float(rib["thickness_mm"]))
        _property(obj, "App::PropertyLength", "HeightMM", "Manufacturing", float(rib["height_mm"]))
        _property(obj, "App::PropertyAngle", "DraftDeg", "Manufacturing", float(rib.get("draft_deg", 0.0)))
        _property(obj, "App::PropertyLength", "RootFilletMM", "Manufacturing", float(rib.get("root_fillet_mm", 0.0)))
        if getattr(obj, "ViewObject", None) is not None:
            obj.ViewObject.ShapeColor = (0.87, 0.53, 0.12)
            obj.ViewObject.Transparency = 0
        if obj not in group.Group:
            group.addObject(obj)
        created.append(obj.Name)
        shapes.append(obj.Shape)

    fused_name = None
    if fuse_to_base and shapes:
        source = document.getObject(base_object) if base_object else None
        if source is None or getattr(source, "Shape", None) is None or source.Shape.isNull():
            raise MoldedFeatureError("fuse_to_base requires a valid base_object")
        combined = source.Shape
        for shape in shapes:
            combined = combined.fuse(shape)
        if not combined.isValid() or combined.isNull() or not combined.Solids:
            raise MoldedFeatureError("fused rib frame is not a valid solid")
        fused = document.getObject("ManufacturedRibFrameResult") or document.addObject(
            "Part::Feature", "ManufacturedRibFrameResult")
        fused.Label = "Manufactured Rib Frame (fused result)"
        fused.Shape = combined
        _property(fused, "App::PropertyString", "DesignStudioRole", "DesignStudio", "manufactured_frame_result")
        _property(fused, "App::PropertyString", "SourceGroup", "DesignStudio", group_name)
        if getattr(fused, "ViewObject", None) is not None:
            fused.ViewObject.ShapeColor = (0.78, 0.48, 0.10)
        fused_name = fused.Name
    document.recompute()
    return {"group": group.Name, "objects": created, "fused_result": fused_name,
            "rib_count": len(created)}


__all__ = ["MoldedFeatureError", "rib_shape", "build_rib_frame"]

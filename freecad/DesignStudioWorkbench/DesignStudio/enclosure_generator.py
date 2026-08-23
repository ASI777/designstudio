"""Deterministic, recomputable FreeCAD enclosure templates.

This module intentionally contains no GUI code and no mesh inference.  Every
returned manufacturing feature is derived from editable FreeCAD properties.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


TEMPLATES = ("rectangular", "injection_clamshell", "handheld")


def _app_part():
    import FreeCAD as App
    import Part
    return App, Part


def _box(Part, App, size, origin=(0.0, 0.0, 0.0)):
    return Part.makeBox(*map(float, size), App.Vector(*map(float, origin)))


def _rounded_box(Part, App, size, radius, origin=(0.0, 0.0, 0.0)):
    shape = _box(Part, App, size, origin)
    if radius <= 0.0:
        return shape
    # Horizontal edges provide a robust clamshell radius while leaving the
    # equatorial datum and mounting faces exact.
    edges = []
    for edge in shape.Edges:
        vertices = edge.Vertexes
        if len(vertices) == 2:
            delta = vertices[-1].Point - vertices[0].Point
            if abs(delta.z) < 1e-8:
                edges.append(edge)
    try:
        return shape.makeFillet(min(radius, min(size) * 0.2), edges)
    except Exception:
        return shape


def _rectangular_frame(Part, App, width, depth, z, frame_width, height):
    outer = _box(Part, App, (width, depth, height), (0, 0, z))
    inner = _box(Part, App,
                 (width - 2 * frame_width, depth - 2 * frame_width, height),
                 (frame_width, frame_width, z))
    return outer.cut(inner)


def _rectangle_wire(Part, App, x0, y0, z, width, depth):
    points = [App.Vector(x0, y0, z), App.Vector(x0 + width, y0, z),
              App.Vector(x0 + width, y0 + depth, z),
              App.Vector(x0, y0 + depth, z), App.Vector(x0, y0, z)]
    return Part.makePolygon(points)


def _handheld_solid(Part, App, width, depth, height, inset, z0=0.0):
    lower = _rectangle_wire(Part, App, inset, inset * 0.45, z0,
                            width - 2 * inset, depth - inset * 0.9)
    shoulder = _rectangle_wire(Part, App, 0, 0, z0 + height * 0.58, width, depth)
    upper = _rectangle_wire(Part, App, 0, 0, z0 + height, width, depth)
    return Part.makeLoft([lower, shoulder, upper], True, False)


def _controller_values(controller):
    return {
        "template": str(controller.Template),
        "width": float(controller.Width.Value),
        "depth": float(controller.Depth.Value),
        "height": float(controller.Height.Value),
        "wall": float(controller.WallThickness.Value),
        "split": float(controller.SplitHeight.Value),
        "corner": float(controller.CornerRadius.Value),
        "draft": float(controller.DraftAngle),
        "pcb_clearance": float(controller.PcbClearance.Value),
        "boss_diameter": float(controller.BossDiameter.Value),
        "boss_hole": float(controller.BossHoleDiameter.Value),
        "rib": float(controller.RibThickness.Value),
        "vent_count": int(controller.VentCount),
    }


def _validate(values):
    w, d, h, wall = values["width"], values["depth"], values["height"], values["wall"]
    if min(w, d, h) <= 0 or wall < 0.8:
        raise ValueError("enclosure dimensions must be positive and wall thickness >= 0.8 mm")
    if 2 * wall + 5 >= min(w, d, h):
        raise ValueError("wall thickness leaves no legal internal volume")
    if not wall < values["split"] < h - wall:
        raise ValueError("split height must remain between the enclosure floors")
    if values["boss_hole"] >= values["boss_diameter"]:
        raise ValueError("boss hole must be smaller than the boss diameter")


def _base_shell(Part, App, values):
    w, d, h, wall = values["width"], values["depth"], values["height"], values["wall"]
    if values["template"] == "handheld":
        inset = min(w * 0.1, d * 0.12)
        outer = _handheld_solid(Part, App, w, d, h, inset)
        inner = _handheld_solid(Part, App, w - 2 * wall, d - 2 * wall,
                                h - 2 * wall, max(0.0, inset - wall * 0.4), wall)
        inner.translate(App.Vector(wall, wall, 0))
        return outer.cut(inner)
    radius = values["corner"] if values["template"] == "injection_clamshell" else 0.0
    outer = _rounded_box(Part, App, (w, d, h), radius)
    inner = _rounded_box(Part, App, (w - 2 * wall, d - 2 * wall, h - 2 * wall),
                         max(0.0, radius - wall), (wall, wall, wall))
    return outer.cut(inner)


def _manufacturing_features(Part, App, values):
    w, d, wall = values["width"], values["depth"], values["wall"]
    split = values["split"]
    boss_r = values["boss_diameter"] / 2.0
    hole_r = values["boss_hole"] / 2.0
    boss_height = max(3.0, split - wall)
    inset = max(wall + boss_r + 1.0, 7.0)
    bosses = None
    for x, y in ((inset, inset), (w - inset, inset),
                 (w - inset, d - inset), (inset, d - inset)):
        boss = Part.makeCylinder(boss_r, boss_height, App.Vector(x, y, wall))
        hole = Part.makeCylinder(hole_r, boss_height, App.Vector(x, y, wall))
        boss = boss.cut(hole)
        bosses = boss if bosses is None else bosses.fuse(boss)

    rib = values["rib"]
    ribs = _box(Part, App, (w - 2 * wall, rib, wall * 1.5),
                (wall, d / 2 - rib / 2, wall))
    ribs = ribs.fuse(_box(Part, App, (rib, d - 2 * wall, wall * 1.5),
                          (w / 2 - rib / 2, wall, wall)))
    gasket = _rectangular_frame(Part, App, w - 2 * wall, d - 2 * wall,
                                split - 0.35, max(0.8, wall * 0.55), 0.7)
    gasket.translate(App.Vector(wall, wall, 0))
    return bosses, ribs, gasket


def _apply_openings_and_vents(Part, App, shell, values):
    w, d, h, wall = values["width"], values["depth"], values["height"], values["wall"]
    # Deterministic connector/display openings on protected datum faces.
    connector = _box(Part, App, (18.0, wall * 3, 9.0),
                     (w / 2 - 9.0, -wall, max(wall + 2.0, h * 0.35)))
    display = _box(Part, App, (min(42.0, w * 0.45), min(24.0, d * 0.4), wall * 3),
                   (w * 0.275, d * 0.3, h - 2 * wall))
    result = shell.cut(connector).cut(display)
    count = values["vent_count"]
    if count:
        spacing = min(5.0, (w - 2 * wall) / (count + 1))
        start = w / 2 - spacing * (count - 1) / 2
        for index in range(count):
            vent = _box(Part, App, (1.5, wall * 3, 8.0),
                        (start + index * spacing, d - 2 * wall, h * 0.55))
            result = result.cut(vent)
    return result


def build_shapes(controller):
    App, Part = _app_part()
    values = _controller_values(controller)
    _validate(values)
    shell = _apply_openings_and_vents(Part, App, _base_shell(Part, App, values), values)
    w, d, h, wall, split = (values[key] for key in
                             ("width", "depth", "height", "wall", "split"))
    lower_clip = _box(Part, App, (w + 2, d + 2, split + 1), (-1, -1, -1))
    upper_clip = _box(Part, App, (w + 2, d + 2, h - split + 1),
                      (-1, -1, split))
    lower = shell.common(lower_clip)
    upper = shell.common(upper_clip)
    bosses, ribs, gasket = _manufacturing_features(Part, App, values)
    lower = lower.fuse(bosses).fuse(ribs)

    clearance = values["pcb_clearance"]
    pcb_z = wall + 3.0
    legal = _box(Part, App,
                 (w - 2 * (wall + clearance),
                  d - 2 * (wall + clearance),
                  max(1.0, split - pcb_z - clearance)),
                 (wall + clearance, wall + clearance, pcb_z))
    if values["template"] == "handheld":
        # Narrow grip keep-outs leave a central deterministic PCB region.
        grip = max(4.0, w * 0.08)
        legal = legal.cut(_box(Part, App, (grip, d, split),
                              (wall + clearance, 0, 0)))
        legal = legal.cut(_box(Part, App, (grip, d, split),
                              (w - wall - clearance - grip, 0, 0)))
    return {"lower": lower, "upper": upper, "gasket": gasket, "legal_pcb": legal}


class EnclosurePartProxy:
    def __init__(self, obj, controller, role):
        obj.addProperty("App::PropertyLink", "Controller", "DesignStudio")
        obj.Controller = controller
        obj.addProperty("App::PropertyString", "PartRole", "DesignStudio")
        obj.PartRole = role
        self.Type = "DesignStudioEnclosurePart"
        obj.Proxy = self

    def execute(self, obj):
        try:
            obj.Shape = build_shapes(obj.Controller)[obj.PartRole]
            if "BuildStatus" in obj.Controller.PropertiesList:
                obj.Controller.BuildStatus = "valid"
        except Exception as exc:
            obj.Controller.BuildStatus = f"invalid: {exc}"


def _add_length(obj, name, value):
    obj.addProperty("App::PropertyLength", name, "Dimensions")
    setattr(obj, name, float(value))


def create_enclosure(document, template="rectangular", dimensions=None):
    """Create one editable enclosure and return its controller and part objects."""
    if template not in TEMPLATES:
        raise ValueError(f"unsupported enclosure template {template!r}")
    if document.getObject("DesignStudioEnclosure") is not None:
        raise ValueError("the document already contains a DesignStudio enclosure")
    values = {"width": 120.0, "depth": 80.0, "height": 38.0,
              "wall": 2.5, "split": 19.0, "corner": 5.0,
              "pcb_clearance": 2.0}
    values.update(dimensions or {})
    controller = document.addObject("App::FeaturePython", "DesignStudioEnclosure")
    controller.Label = "DesignStudio Enclosure Parameters"
    controller.addProperty("App::PropertyEnumeration", "Template", "DesignStudio")
    controller.Template = list(TEMPLATES)
    controller.Template = template
    for prop, key in (("Width", "width"), ("Depth", "depth"), ("Height", "height"),
                      ("WallThickness", "wall"), ("SplitHeight", "split"),
                      ("CornerRadius", "corner"), ("PcbClearance", "pcb_clearance")):
        _add_length(controller, prop, values[key])
    _add_length(controller, "BossDiameter", 7.0)
    _add_length(controller, "BossHoleDiameter", 2.8)
    _add_length(controller, "RibThickness", 1.8)
    controller.addProperty("App::PropertyAngle", "DraftAngle", "Manufacturing")
    controller.DraftAngle = 1.5 if template != "rectangular" else 0.0
    controller.addProperty("App::PropertyIntegerConstraint", "VentCount", "Manufacturing")
    controller.VentCount = (6, 0, 40, 1)
    controller.addProperty("App::PropertyString", "BuildStatus", "DesignStudio")
    controller.BuildStatus = "pending recompute"

    parts = {}
    for name, label, role in (("LowerShell", "Lower Shell", "lower"),
                              ("UpperShell", "Upper Shell", "upper"),
                              ("GasketChannel", "Gasket / Seal Channel", "gasket"),
                              ("LegalPCBVolume", "Legal PCB Volume", "legal_pcb")):
        obj = document.addObject("Part::FeaturePython", name)
        obj.Label = label
        EnclosurePartProxy(obj, controller, role)
        parts[role] = obj
    # Part -> controller is the recompute dependency. A reverse PropertyLinkList
    # would create a cycle in FreeCAD's document DAG, so discovery metadata is
    # stored as stable object names instead of reverse links.
    controller.addProperty("App::PropertyStringList", "GeneratedPartNames", "DesignStudio")
    controller.GeneratedPartNames = [part.Name for part in parts.values()]
    document.recompute()
    if controller.BuildStatus != "valid" or any(part.Shape.isNull() for part in parts.values()):
        raise ValueError(f"enclosure construction failed: {controller.BuildStatus}")
    return {"controller": controller, **parts}


def compare_catalog(required_inner_mm, catalog):
    """Return fitting catalog enclosures ordered by unused volume then price."""
    required = tuple(map(float, required_inner_mm))
    if len(required) != 3 or min(required) <= 0:
        raise ValueError("required inner dimensions must contain three positive values")
    result = []
    for item in catalog:
        inner = tuple(map(float, item["inner_mm"]))
        if all(inner[index] >= required[index] for index in range(3)):
            unused = math.prod(inner) - math.prod(required)
            result.append({**item, "unused_volume_mm3": round(unused, 6),
                           "fit_margin_mm": [inner[i] - required[i] for i in range(3)]})
    return sorted(result, key=lambda item: (item["unused_volume_mm3"],
                                            float(item.get("price", math.inf)),
                                            str(item.get("part_number", ""))))

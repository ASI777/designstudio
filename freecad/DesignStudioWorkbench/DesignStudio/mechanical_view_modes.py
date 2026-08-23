"""Semantic visibility/material policies shared by desktop and cloud views."""
from __future__ import annotations

from typing import Any


MODES = ("product", "manufacturing_internals", "clearance", "exploded", "presentation")


def classify_object(obj: Any) -> str:
    type_id = str(getattr(obj, "TypeId", "") or "")
    if type_id in {"App::Part", "App::DocumentObjectGroup", "App::LinkGroup"}:
        return "container"
    role = str(getattr(obj, "DesignStudioRole", "") or "").lower()
    name = str(getattr(obj, "Name", "") or "").lower()
    label = str(getattr(obj, "Label", "") or "").lower()
    text = " ".join((role, name, label))
    if "reservation" in text or "clearance" in text or "cavity" in text:
        return "reservation"
    if "rib" in text or "boss" in text or "support" in text or "midframe" in text:
        return "internal_structure"
    if "comp_" in text or "component" in text or "board_slab" in text:
        return "electronics"
    if "cover" in text or "housing" in text or "body" in text or "enclosure" in text:
        return "exterior"
    if "orthographic" in text or "profile" in text or "datum" in text:
        return "construction"
    # PartDesign result nodes are already filtered by the workbench's feature
    # policy.  Preserve their established visibility when no semantic label is
    # available instead of hiding a legitimate product solid.
    if type_id.startswith("PartDesign"):
        return "exterior"
    return "other"


def visibility_policy(mode: str, family: str) -> bool:
    if mode not in MODES:
        raise ValueError(f"unknown mechanical view mode: {mode}")
    if family in {"construction", "container"}:
        return family == "container"
    if family == "construction":
        return False
    if mode == "product":
        return family in {"exterior", "electronics"}
    if mode == "manufacturing_internals":
        return family in {"exterior", "internal_structure", "electronics"}
    if mode == "clearance":
        return family in {"exterior", "internal_structure", "electronics", "reservation"}
    if mode == "exploded":
        return family != "construction"
    return family in {"exterior", "electronics"}


def apply_view_mode(document: Any, mode: str, *, fit: bool = True) -> dict[str, Any]:
    """Apply a view mode without relying on object-name-specific callbacks."""
    if mode not in MODES:
        raise ValueError(f"unknown mechanical view mode: {mode}")
    counts = {family: 0 for family in
              ("reservation", "internal_structure", "electronics", "exterior",
               "construction", "container", "other")}
    visible = 0
    for obj in getattr(document, "Objects", []):
        family = classify_object(obj)
        counts[family] = counts.get(family, 0) + 1
        state = visibility_policy(mode, family)
        view = getattr(obj, "ViewObject", None)
        if view is not None:
            view.Visibility = state
        try:
            obj.Visibility = state
        except Exception:
            pass
        visible += int(state)
        if view is not None:
            if family == "reservation":
                view.ShapeColor = (0.90, 0.55, 0.15)
                view.Transparency = 72 if mode == "clearance" else 100
            elif family == "internal_structure":
                view.ShapeColor = (0.87, 0.53, 0.12)
                view.Transparency = 0
            elif family == "electronics":
                view.ShapeColor = (0.15, 0.55, 0.82)
                view.Transparency = 10
    gui_document = None
    try:
        import FreeCADGui as Gui
        gui_document = Gui.activeDocument()
        if gui_document is not None and fit:
            gui_document.activeView().viewAxonometric()
            gui_document.activeView().fitAll()
    except Exception:
        # Headless validation has no GUI; visibility changes are still useful.
        pass
    return {"mode": mode, "visible_objects": visible, "object_families": counts}


__all__ = ["MODES", "classify_object", "visibility_policy", "apply_view_mode"]

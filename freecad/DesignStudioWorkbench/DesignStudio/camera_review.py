"""B-Rep-derived camera review drawings and normalized silhouette comparison.

These SVG files are review evidence.  The FCStd/OpenCASCADE shapes remain the
geometry authority, and every overlay explicitly discards absolute scale.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


VIEWS = ("front", "rear", "left", "right", "top", "bottom")
COLORS = {"precision": "#2f80ed", "grip": "#eb5757", "serviceable": "#27ae60"}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_curves(curves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points = [point for curve in curves for point in curve.get("points", [])]
    if not points:
        return []
    xmin = min(point[0] for point in points)
    ymin = min(point[1] for point in points)
    width = max(max(point[0] for point in points) - xmin, 1.0e-12)
    height = max(max(point[1] for point in points) - ymin, 1.0e-12)
    return [{**curve, "points": [[(point[0] - xmin) / width, (point[1] - ymin) / height]
                                  for point in curve.get("points", [])]}
            for curve in curves]


def _overlay_svg(projections: dict[str, list[dict[str, Any]]]) -> str:
    paths = []
    for label, curves in projections.items():
        for curve in normalize_curves(curves):
            points = " ".join(f"{55 + point[0] * 700:.3f},{520 - point[1] * 440:.3f}"
                              for point in curve["points"])
            paths.append(f'<polyline points="{points}" fill="none" stroke="{COLORS[label]}" '
                         'stroke-width="1.2" opacity="0.72"/>')
    legend = " ".join(f'<text x="{60 + index * 220}" y="565" fill="{COLORS[label]}" '
                      f'font-size="18">{label}</text>' for index, label in enumerate(projections))
    return ("<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 820 600\">"
            "<rect width=\"820\" height=\"600\" fill=\"white\"/>"
            "<text x=\"40\" y=\"38\" font-size=\"22\">NORMALIZED FRONT SILHOUETTE REVIEW</text>"
            "<text x=\"40\" y=\"585\" font-size=\"13\">Scale removed; correspondence only. FCStd B-Rep remains authority.</text>"
            + "".join(paths) + legend + "</svg>\n")


def generate_review(candidate_files: dict[str, Path], output: Path) -> dict[str, Any]:
    import FreeCAD as App
    import Part
    from .authoritative_drawings import _hlr_projection, _projection_check, _svg
    from .physical_design import shape_digest

    output.mkdir(parents=True, exist_ok=True)
    front = {}
    artifacts = []
    correspondence = {}
    for label, source in candidate_files.items():
        doc = App.openDocument(str(source))
        exterior = doc.getObject("Exterior")
        if exterior is not None and exterior.Group:
            objects = [item for item in exterior.Group if getattr(item, "Shape", None) is not None
                       and not item.Shape.isNull()]
        else:
            # Program-authoritative camera FCStd files keep editable source
            # profiles and final semantic features under the program App::Part.
            objects = [item for item in doc.Objects
                       if getattr(item, "SemanticId", "")
                       and not item.SemanticId.startswith("camera.reservation.")
                       and getattr(item, "Shape", None) is not None
                       and not item.Shape.isNull()]
        if not objects:
            App.closeDocument(doc.Name)
            raise RuntimeError(f"{label} FCStd has no semantic exterior B-Rep features")
        shape = Part.makeCompound([item.Shape for item in objects])
        if shape.isNull() or not shape.isValid():
            App.closeDocument(doc.Name)
            raise RuntimeError(f"{label} exterior compound is invalid")
        source_digest = shape_digest(shape)
        correspondence[label] = sorted(getattr(item, "SemanticId", "") for item in objects)
        for view in VIEWS:
            projection = _hlr_projection(shape, view)
            check = _projection_check(shape, projection)
            curves = projection["visible"] + projection["hidden"]
            metadata = {"schema": "design-studio.camera-orthographic-review/1",
                        "candidate": label, "view": view,
                        "source_shape_sha256": source_digest,
                        "authority": "review-only OCCT HLR projection; FCStd B-Rep remains authority",
                        "absolute_scale_preserved": True, "release_authority": False}
            target = output / label / f"{view}.svg"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_svg(curves, f"{label.upper()} {view.upper()} REVIEW", metadata, {}),
                              encoding="utf-8")
            artifacts.append({"candidate": label, "view": view, "path": str(target),
                              "sha256": _digest(target), "projection_check": check})
            if view == "front":
                front[label] = projection["visible"]
        App.closeDocument(doc.Name)
    overlay = output / "normalized-front-silhouette-overlay.svg"
    overlay.write_text(_overlay_svg(front), encoding="utf-8")
    report = {"schema": "design-studio.camera-review-package/1",
              "status": "REVIEW_ONLY_NOT_RELEASE_AUTHORITY",
              "orthographic_artifacts": artifacts,
              "normalized_silhouette_overlay": {"path": str(overlay), "sha256": _digest(overlay),
                                                  "absolute_scale_removed": True},
              "semantic_feature_correspondence": correspondence,
              "canonical_candidate": None,
              "release_authority": False}
    manifest_path = output / "review-manifest.json"
    manifest_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
    from .camera_benchmark import bind_candidate_review_artifacts
    first_source = next(iter(candidate_files.values())).resolve()
    review_path = first_source.parents[2] / "candidate-review.json"
    if review_path.is_file():
        overlay_path = Path(report["normalized_silhouette_overlay"]["path"])
        bind_candidate_review_artifacts(review_path, {
            label: [Path(item["path"]) for item in artifacts if item["candidate"] == label]
                   + [overlay_path, manifest_path]
            for label in candidate_files
        })
    return report

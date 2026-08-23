#!/usr/bin/env python3
"""FreeCAD/OCCT runtime and reload fixture for package 3."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import sys

import FreeCAD as App
import Part

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "freecad" / "DesignStudioWorkbench"))
from DesignStudio.authoritative_drawings import (  # noqa: E402
    generate_authoritative_drawing_package,
    make_authoritative_request,
)


def digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def artifact_digests(directory):
    return {path.name: digest(path) for path in sorted(directory.iterdir())
            if path.suffix in {".svg", ".dxf"}}


def main():
    with tempfile.TemporaryDirectory(prefix="designstudio-authoritative-drawings-") as temp:
        root = Path(temp)
        (root / "mechanical").mkdir()
        doc = App.newDocument("AuthoritativeDrawingRuntime")
        source = doc.addObject("Part::Feature", "TargetBody")
        source.Shape = Part.makeBox(10.0, 20.0, 30.0)
        source.addProperty("App::PropertyString", "DesignStudioSemanticId", "DesignStudio Semantic")
        source.DesignStudioSemanticId = "target-body"
        source.addProperty("App::PropertyString", "DesignStudioRole", "DesignStudio Semantic")
        source.DesignStudioRole = "enclosure"
        mechanical = root / "mechanical" / "target.FCStd"
        doc.recompute(); doc.saveAs(str(mechanical))
        sections = [{"section_id": "mid-z", "plane_origin_mm": [5, 10, 15],
                     "plane_normal": [0, 0, 1], "label": "MID Z SECTION"}]
        request = make_authoritative_request(
            doc, root, source, "drawings/controller", sections=sections,
            selected_faces=["Face1"], selected_edges=["Edge1"], package_id="runtime-drawing")
        request["annotations"]["datums"] = [{"datum_id": "datum-a", "point_mm": [0, 0, 0], "label": "A"}]
        request["annotations"]["tolerances"] = [{"tolerance_id": "tol-a", "nominal_mm": 10.0, "plus_mm": 0.1, "minus_mm": 0.1}]
        request["annotations"]["gdt"] = [{"frame_id": "gdt-a", "symbol": "profile", "tolerance_mm": 0.2, "datum_refs": ["datum-a"]}]
        request["annotations"]["continuity"] = [{"continuity_id": "cont-a", "boundary_id": "Face1-boundary", "required": "G1", "max_normal_angle_deg": 1.0}]
        request_path = root / "contracts" / "authoritative-request.json"
        request_path.parent.mkdir(parents=True)
        request_path.write_text(json.dumps(request, indent=2) + "\n")
        first = generate_authoritative_drawing_package(doc, root, request_path)
        index = json.loads(Path(first["index_path"]).read_text())
        assert index["projection_engine"] == "occt-hlrbrep"
        assert index["artifact_count"] == 24, index["artifact_count"]
        assert all(not Path(item["path"]).is_absolute() for item in index["artifacts"])
        assert all(item["max_deviation_mm"] <= 0.05 + 1e-9 for item in index["view_checks"].values())
        directory = root / "drawings" / "controller"
        names = {path.name for path in directory.iterdir()}
        for view in ("front", "rear", "left", "right", "top", "bottom"):
            assert f"{view}.svg" in names and f"{view}.dxf" in names
        for stem in ("01-general-arrangement", "02-selected-region-details",
                     "03-sections-continuity", "04-material-manufacturing",
                     "05-interfaces-clearances"):
            assert f"{stem}.svg" in names and f"{stem}.dxf" in names
        svg = (directory / "front.svg").read_text()
        dxf = (directory / "front.dxf").read_text()
        assert 'id="visible"' in svg and 'id="hidden"' in svg
        assert "SOURCE B-REP SHA-256" in svg and "CONTINUITY" in svg and "GD&amp;T" in svg
        assert "SOURCE_BREP_SHA256" in dxf and "CONTINUITY" in dxf and "GD&T" in dxf
        first_digests = artifact_digests(directory)
        App.closeDocument(doc.Name)
        reloaded = App.openDocument(str(mechanical))
        source2 = reloaded.getObject("TargetBody")
        second = generate_authoritative_drawing_package(reloaded, root, request_path)
        assert json.loads(Path(second["index_path"]).read_text())["geometry_digest"] == index["geometry_digest"]
        assert artifact_digests(directory) == first_digests
        assert source2.Shape.isValid()
        from DesignStudio.live_tools import _generate_authoritative_drawings
        live = _generate_authoritative_drawings({
            "mechanical_path": str(mechanical), "workspace_root": str(root),
            "source_semantic_id": "target-body", "output_directory": "drawings/live",
            "sections": sections, "selected_faces": ["Face1"], "selected_edges": ["Edge1"],
            "package_id": "live-drawing"})
        assert live["ok"] and Path(live["data"]["index_path"]).is_file()
        App.closeDocument(reloaded.Name)
    print("AUTHORITATIVE_DRAWINGS_RUNTIME_OK")


if __name__ == "__main__":
    main()

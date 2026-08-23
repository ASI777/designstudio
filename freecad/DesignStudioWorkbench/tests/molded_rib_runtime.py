#!/usr/bin/env python3
"""FreeCAD/OpenCascade runtime check for manufactured rib B-Reps."""
from __future__ import annotations

import sys
from pathlib import Path

import FreeCAD as App

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "freecad" / "DesignStudioWorkbench"))

from DesignStudio.molded_features import build_rib_frame  # noqa: E402


def main() -> None:
    # The first member is intentionally shorter than its thickness. This is
    # the geometry that used to fail when the requested root radius was applied
    # without a member-length cap.
    network = {
        "ribs": [
            {
                "id": "rib:short:0",
                "path_mm": [[0.0, 0.0, 0.0], [0.5, 0.0, 0.0]],
                "thickness_mm": 1.313014,
                "height_mm": 4.4,
                "draft_deg": 1.0,
                "root_fillet_mm": 1.313014,
            },
            {
                "id": "rib:normal:0",
                "path_mm": [[0.0, 5.0, 0.0], [4.0, 5.0, 0.0]],
                "thickness_mm": 1.1,
                "height_mm": 4.4,
                "draft_deg": 1.0,
                "root_fillet_mm": 0.55,
            },
        ]
    }
    document = App.newDocument("MoldedRibRuntime")
    result = build_rib_frame(document, network)
    assert result["rib_count"] == 2
    for name in result["objects"]:
        obj = document.getObject(name)
        assert obj is not None and obj.Shape.isValid() and not obj.Shape.isNull()
        assert len(obj.Shape.Solids) == 1
    print("MOLDED_RIB_RUNTIME_OK", result)
    App.closeDocument(document.Name)


if __name__ == "__main__":
    main()


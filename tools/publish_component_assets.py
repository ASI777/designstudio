#!/usr/bin/env python3
"""Publish one approved component preview into DesignStudio's libraries."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PREFIX_OR_ROOT = Path(__file__).resolve().parents[1]
for candidate in (PREFIX_OR_ROOT, PREFIX_OR_ROOT / "share" / "DesignStudio" / "python"):
    if (candidate / "swarm").is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
        break

from swarm.memory.component_cad_program import publish_component_preview


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish an approved component CAD preview")
    parser.add_argument("manifest", help="preview-manifest.json produced by the extractor")
    parser.add_argument("--component-library", help="component/2 library root")
    parser.add_argument("--binding-library", help="bound-component library root")
    parser.add_argument("--reviewer", default="user", help="approval identity stored in evidence")
    args = parser.parse_args()
    result = publish_component_preview(
        args.manifest, component_root=args.component_library,
        binding_root=args.binding_library, reviewer=args.reviewer)
    print(json.dumps({"ok": True, "message": "Component assets published", **result},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

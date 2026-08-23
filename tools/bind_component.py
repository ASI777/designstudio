#!/usr/bin/env python3
"""Bind one component/2 record to a reviewed STEP model.

Example:
  python3 tools/bind_component.py component.json part.step --alignment verified

The default alignment is deliberately `unverified`; such a record is stored for
review but cannot be placed in a FreeCAD-locked board until a reviewer reruns the
command with the verified transform.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
INSTALLED_PYTHON = Path(__file__).resolve().parents[1] / "share" / "DesignStudio" / "python"
for candidate in (ROOT, INSTALLED_PYTHON):
    if (candidate / "swarm").is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from swarm.memory.component_binding import BoundComponentStore, bind_component


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", help="design-studio.component/2 JSON")
    parser.add_argument("step", help="STEP model aligned to the footprint origin")
    parser.add_argument("--alignment", choices=("verified", "unverified"),
                        default="unverified")
    parser.add_argument("--model-mpn", required=True,
                        help="MPN asserted by the reviewed STEP source; must exactly match")
    parser.add_argument("--transform", help="16 comma-separated row-major values")
    parser.add_argument("--library", help="bound-component library root")
    args = parser.parse_args(argv)

    component_path = Path(args.component).expanduser().resolve()
    component = json.loads(component_path.read_text(encoding="utf-8"))
    transform = None
    if args.transform:
        transform = [float(value.strip()) for value in args.transform.split(",")]
        if len(transform) != 16:
            parser.error("--transform requires exactly 16 comma-separated values")
    binding = bind_component(
        component, args.step, model_transform=transform,
        alignment_status=args.alignment, model_mpn=args.model_mpn,
        source_uri=str(component_path))
    store = BoundComponentStore(args.library)
    record = store.save(binding)
    print(json.dumps({
        "status": binding["status"], "binding_id": binding["binding_id"],
        "binding_digest": binding["binding_digest"], "record": str(record),
        "model_sha256": binding["model_3d"]["sha256"],
        "alignment": binding["model_3d"]["alignment_status"],
    }, indent=2))
    return 0 if binding["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())

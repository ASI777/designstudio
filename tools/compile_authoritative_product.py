#!/usr/bin/env python3
"""Compile or verify a DesignStudio cross-domain product bundle."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swarm.memory.authoritative_product import (  # noqa: E402
    ProductCompileError,
    compile_product,
    validate_bundle,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--validate", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.validate:
            manifest = validate_bundle(args.validate)
            path = args.validate.resolve()
        else:
            if args.requirements is None or args.output_root is None:
                raise ProductCompileError(
                    ["--requirements and --output-root are required for compilation"])
            path = compile_product(args.requirements, args.output_root)
            manifest = validate_bundle(path)
        print(json.dumps({
            "ok": True,
            "manifest_path": str(path),
            "product_id": manifest["product_id"],
            "revision": manifest["revision"],
            "bundle_digest": manifest["bundle_digest"],
            "artifact_count": len(manifest["artifacts"]),
        }, sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, ProductCompileError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

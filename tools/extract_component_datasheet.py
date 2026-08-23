#!/usr/bin/env python3
"""Run the controlled datasheet-to-component workflow for one part."""
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

from swarm.agents.datasheet_agent import process
from swarm.runtime_paths import data_root


def _safe(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_." else "_" for char in value)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect a datasheet and build an isolated symbol/footprint/STEP preview")
    parser.add_argument("source", nargs="?", default="",
                        help="Local PDF/file URL/HTTPS URL; omit to query configured vendors by MPN")
    parser.add_argument("--mpn", required=True, help="Exact manufacturer part number")
    parser.add_argument("--output", help="Output JSON path")
    parser.add_argument("--timeout", type=int, default=600, help="Codex Luna xhigh timeout in seconds")
    parser.add_argument("--component-cad", help="Path to the DesignStudio Open CASCADE command executor")
    parser.add_argument("--preview-root", help="Isolated component preview directory")
    args = parser.parse_args()

    source = args.source.strip()
    sources = [source] if source else []
    component = None
    if not source:
        try:
            from swarm.memory.component_library import ComponentLibrary
            with ComponentLibrary() as library:
                candidate = library.get_component(args.mpn, approved_only=True)
                footprint = library.asset_for(args.mpn, "footprint")
                model = library.asset_for(args.mpn, "model_3d")
                if candidate is not None and footprint is not None and model is not None:
                    component = candidate
        except Exception:
            component = None
    if not source:
        if component is None:
            from swarm.vendors.aggregator import Aggregator
            from swarm.vendors.base import exact_mpn_match
            aggregator = Aggregator()
            results = aggregator.search(args.mpn, limit=8)
            exact = [part for part in results
                     if part.mpn and exact_mpn_match(args.mpn, part.mpn)
                     and part.datasheet_url]
            sources.extend(str(part.datasheet_url) for part in exact
                           if part.datasheet_url and str(part.datasheet_url) not in sources)
            manufacturer = next((str(part.manufacturer) for part in exact
                                 if part.manufacturer), "")
            try:
                from swarm.vendors.web_fallback import search_datasheets
                sources.extend(item["url"] for item in search_datasheets(
                    args.mpn, limit=5, manufacturer=manufacturer)
                    if item["url"] not in sources)
            except Exception:
                pass
            if not sources:
                errors = "; ".join(f"{name}: {detail}"
                                   for name, detail in aggregator.last_errors.items())
                raise SystemExit(
                    "No exact-MPN datasheet was found through configured APIs or official-first internet research"
                    + (f" ({errors})" if errors else
                       "; attach a verified PDF or select another part"))

    if component is None:
        failures = []
        for candidate_source in sources:
            try:
                component = process(candidate_source, mpn=args.mpn, timeout=args.timeout,
                                    component_cad_executable=args.component_cad,
                                    preview_root=args.preview_root)
                if component:
                    break
            except Exception as exc:
                failures.append(f"{candidate_source}: {type(exc).__name__}")
        if component is None:
            raise SystemExit("Every API and internet datasheet candidate failed the PDF/MPN/CAD trust boundary; "
                             "attach a verified PDF or select another part"
                             + (f" ({'; '.join(failures)})" if failures else ""))
    destination = Path(args.output) if args.output else (
        data_root() / "component-previews" / "proposals" /
        f"{_safe(args.mpn)}.component.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(component, indent=2, sort_keys=True) + "\n")
    verification = (component.get("extraction") or {}).get("verification") or {}
    print(f"EXTRACTED_COMPONENT {destination}")
    print(f"PLACEMENT_STATE {verification.get('state', 'unknown')}")
    preview = component.get("preview") or {}
    print(f"PREVIEW_MANIFEST {preview.get('manifest_uri', '')}")
    if preview.get("state") == "published":
        print("REUSED_APPROVED_ASSETS true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

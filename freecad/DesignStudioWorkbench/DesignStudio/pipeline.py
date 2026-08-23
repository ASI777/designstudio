"""Deterministic product-build pipeline status for DesignStudio workspaces.

The PRODUCT_PIPELINE constant is the authoritative stage order every
industrial hardware build must follow.  inspect() reads only filesystem
facts inside a .dsworkspace, so the same process is enforced for every
product regardless of who or what drives construction.
"""
from __future__ import annotations

from pathlib import Path
import json

PIPELINE_VERSION = "product-pipeline/1"

# (stage_id, human label, detector key)
STAGES = [
    ("workspace",     "Workspace created (manifest + dirs)", "workspace"),
    ("references",    "Reference intake (datasheets/images, hashed)", "references"),
    ("components",    "Component models built (Phase 1b)", "components"),
    ("body",          "Mechanical body solids + reservations", "body"),
    ("electronics",   "Electronics populated (footprints placed)", "electronics"),
    ("instances",     "Real models replace reservation boxes", "instances"),
    ("datums",        "Interface datum contract extracted", "datums"),
    ("structure",     "Anchors/bosses/supporting ribs bonded to body", "structure"),
    ("verification",  "DFM + engineering verification passed", "verification"),
    ("review",        "Review package rendered for approval", "review"),
    ("release",       "Release gate signed / mfg package exported", "release"),
]


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _detect_workspace(root: Path):
    return (root / "manifest.json").is_file()


def _detect_references(root: Path):
    for d in ("evidence/references", "references"):
        p = root / d
        if p.is_dir() and any(p.iterdir()):
            return True
    return False


def _detect_components(root: Path):
    for d in ("mechanical/components", "components", "assets/components"):
        p = root / d
        if p.is_dir() and any(p.glob("*.step")):
            return True
    man = root / "contracts" / "component-manifest.json"
    return man.is_file()


def _detect_body(root: Path):
    fcstd = root / "mechanical" / "product.FCStd"
    if not fcstd.is_file():
        return False
    val = root / "mechanical" / "product-validation.json"
    if val.is_file():
        data = _read_json(val)
        if isinstance(data, dict):
            checks = data.get("checks", data)
            if isinstance(checks, dict):
                watertight = checks.get("watertight_solids")
                if watertight is True:
                    return True
                if watertight is False:
                    return False
    return fcstd.stat().st_size > 10_000


def _detect_electronics(root: Path):
    dsproj = root / "electronics" / "product.dsproj"
    if not dsproj.is_file():
        return False
    data = _read_json(dsproj)
    if not isinstance(data, dict):
        return False
    fps = data.get("footprints")
    return isinstance(fps, list) and len(fps) > 0


def _detect_instances(root: Path):
    return _detect_components(root)


def _detect_datums(root: Path):
    cdir = root / "contracts"
    if cdir.is_dir():
        for f in cdir.glob("*.json"):
            if "datum" in f.name.lower() or "interface" in f.name.lower():
                return True
    return False


def _detect_structure(root: Path):
    for pat in ("mechanical/structure-report.json",
                "generated/structure-report.json",
                "evidence/structure-report.json"):
        if (root / pat).is_file():
            return True
    return False


def _detect_verification(root: Path):
    rep = root / "verification" / "reports" / "engineering-verification.json"
    data = _read_json(rep) if rep.is_file() else None
    if isinstance(data, dict):
        return data.get("overall_status") == "pass"
    return False


def _detect_review(root: Path):
    for d in ("renders", "generated/reviews", "evidence/review"):
        p = root / d
        if p.is_dir() and any(p.glob("*.png")):
            return True
    return False


def _detect_release(root: Path):
    for pat in ("release/*.json", "release/**/release-manifest.json",
                "fab/*", "production-package/**"):
        hits = list(root.glob(pat))
        if hits:
            return True
    return False


DETECTORS = {
    "workspace": _detect_workspace,
    "references": _detect_references,
    "components": _detect_components,
    "body": _detect_body,
    "electronics": _detect_electronics,
    "instances": _detect_instances,
    "datums": _detect_datums,
    "structure": _detect_structure,
    "verification": _detect_verification,
    "review": _detect_review,
    "release": _detect_release,
}


def inspect(workspace_root: str | Path) -> dict:
    """Return {pipeline_version, root, stages:[(id,label,status)], current_stage}."""
    root = Path(workspace_root).expanduser()
    results = []
    done_count = 0
    current_stage = None
    for sid, label, key in STAGES:
        try:
            status = bool(DETECTORS[key](root))
        except Exception:
            status = False
        results.append({"id": sid, "label": label, "done": status})
        if status:
            done_count += 1
        elif current_stage is None:
            current_stage = sid
    if all(r["done"] for r in results):
        current_stage = "complete"
    return {"pipeline_version": PIPELINE_VERSION,
            "root": str(root),
            "stages": results,
            "completed": done_count,
            "total": len(STAGES),
            "current_stage": current_stage}


def format_report(report: dict) -> str:
    lines = [f"Product pipeline — {report['root']}",
             f"version: {report['pipeline_version']}  "
             f"progress: {report['completed']}/{report['total']}  "
             f"next: {report['current_stage']}"]
    marker = {"done": "[x]", "pending": "[ ]"}
    for s in report["stages"]:
        lines.append(f"  {marker['done' if s['done'] else 'pending']} "
                     f"{s['id']:<12} {s['label']}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("usage: pipeline.py <workspace-root>")
        raise SystemExit(2)
    print(format_report(inspect(sys.argv[1])))

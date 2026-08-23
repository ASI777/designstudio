"""Footprint-verification gate — the safety net that turns *silent* misalignment
into *reported* uncertainty.

For each component it cross-checks the footprint against the datasheet symbol and
classifies confidence:
  • verified  — exact land pattern from the datasheet dimension table (IPC),
                a traced vector pad grid, or a standard passive; pad numbers
                cover every symbol pin.
  • unverified — approximation/default footprint, or pad count / pin numbering
                that doesn't match the datasheet → surfaced as a warning.

Basic schema, numeric geometry, exact-MPN evidence, typed CAD execution, and an
explicit preview approval form the component publication boundary.
"""
from __future__ import annotations

import math

_VERIFIED_SOURCES = ("ipc7351", "vector-trace", "ipc-passive")


def verify_footprint(comp2: dict) -> dict:
    """Return {ok, confidence, source, issues[]} for one component/2."""
    fp = comp2.get("footprint", {}) or {}
    sym = (comp2.get("symbol", {}) or {}).get("pins", []) or []
    pads = fp.get("pads", []) or []
    gen = fp.get("generated", "")
    issues: list[str] = []

    sym_nums = {str(p.get("number")).upper() for p in sym if p.get("number") is not None}
    pad_nums = {str(p.get("number", p.get("name", ""))).upper() for p in pads}

    if not pads:
        issues.append("no footprint pads")
    # Pad count / numbering must cover every datasheet pin.
    if sym_nums:
        missing = sym_nums - pad_nums
        extra = pad_nums - sym_nums
        if missing:
            issues.append(f"{len(missing)} datasheet pin(s) have no pad "
                          f"(e.g. {', '.join(sorted(missing)[:4])})")
        if extra:
            issues.append(f"{len(extra)} pad(s) not in the datasheet pin list")
    elif pads:
        issues.append("no datasheet pins to check numbering against")

    # Source confidence — a footprint built from datasheet dimensions / a traced
    # grid / a standard passive is trustworthy; anything else is an approximation.
    has_dims = bool(fp.get("dimensions"))
    verified_src = gen in _VERIFIED_SOURCES or has_dims
    if not verified_src:
        issues.append("footprint is an approximation (no datasheet dimensions / IPC)")

    # numbering-complete + trusted source = high; partial = low; in between = medium
    numbering_ok = bool(sym_nums) and not (sym_nums - pad_nums)
    if verified_src and numbering_ok:
        conf = "verified"
    elif not pads or (sym_nums and (sym_nums - pad_nums)):
        conf = "unverified"
    else:
        conf = "approximate"
    return {"ok": conf == "verified", "confidence": conf,
            "source": gen or ("dimensions" if has_dims else "extracted/default"),
            "issues": issues}


def placement_allowed(comp2: dict) -> tuple[bool, str]:
    """Fail closed until a Sol/Luna component preview is explicitly approved."""
    extraction = (comp2 or {}).get("extraction") or {}
    if extraction.get("provider") != "codex-cli":
        return True, "legacy component record"
    mpn = str(((comp2 or {}).get("component") or {}).get("mpn") or "").strip()
    pads = ((comp2 or {}).get("footprint") or {}).get("pads") or []
    if not mpn:
        return False, "extracted component has no exact manufacturer part number"
    if not pads:
        return False, "extracted footprint has no pads"
    for index, pad in enumerate(pads):
        try:
            values = [float(pad[key]) for key in
                      ("x_mm", "y_mm", "width_mm", "height_mm")]
        except (KeyError, TypeError, ValueError):
            return False, f"pad {index + 1} has incomplete numeric geometry"
        if not all(math.isfinite(value) for value in values) \
                or values[2] <= 0 or values[3] <= 0:
            return False, f"pad {index + 1} has invalid numeric geometry"
    verification = extraction.get("verification") or {}
    state = verification.get("state") or "legacy"
    if state != "approved" or not verification.get("placement_allowed", False):
        return False, f"component preview requires approval ({state})"
    preview = (comp2 or {}).get("preview") or {}
    if preview.get("state") != "published":
        return False, "component assets are not published"
    return True, "approved datasheet-derived footprint"


# ── self-test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # 1) exact IPC footprint from datasheet dims, numbering complete → verified
    good = {"symbol": {"pins": [{"number": str(i + 1)} for i in range(8)]},
            "footprint": {"generated": "ipc7351", "dimensions": {"pitch": 0.5},
                          "pads": [{"number": str(i + 1)} for i in range(8)]}}
    r = verify_footprint(good)
    print("exact IPC:        ", r["confidence"], r["issues"])
    assert r["confidence"] == "verified"

    # 2) approximation, pad count short → unverified, flagged
    bad = {"symbol": {"pins": [{"number": str(i + 1)} for i in range(48)]},
           "footprint": {"pads": [{"number": str(i + 1)} for i in range(24)]}}
    r = verify_footprint(bad)
    print("approx, 24/48:    ", r["confidence"], r["issues"])
    assert r["confidence"] == "unverified" and r["issues"]

    print("\nfootprint_verify OK — verified vs unverified correctly classified")

"""Datasheet mechanical-dimensions parser → exact package descriptor.

The structure of a footprint (which sides have pins, pin-1 location, thermal pad)
is implied by the package type; the SIZE (pitch, body, pad, thermal) lives in the
JEDEC mechanical-dimensions table as TEXT. This module reads that table — the
authoritative numbers — so the IPC generator can place every pad on its true
grid, instead of trusting LLM-misread pad coordinates.

Returns a dims dict the IC footprint generator consumes:
  {family, pitch, body_w, body_l, pad_w, pad_l, thermal_w, thermal_l, jedec}
"""
from __future__ import annotations
import re

_NUM = re.compile(r"([0-9]+\.[0-9]+|[0-9]+)")

# JEDEC outline → package family (the standard registered drawings).
_JEDEC = {
    # QFN / DFN (no-lead)
    "MO-220": "qfn", "MO-247": "qfn", "MO-248": "qfn", "MO-241": "qfn",
    "MO-255": "qfn", "MO-243": "qfn", "MO-249": "qfn", "MO-252": "qfn",
    # QFP / TQFP / LQFP (gull-wing, 4 sides)
    "MS-026": "qfp", "MS-022": "qfp", "MO-136": "qfp", "MS-029": "qfp",
    "MO-108": "qfp", "MS-024": "qfp",
    # SOIC / SOP / SSOP / TSSOP / MSOP (gull-wing, 2 sides)
    "MS-012": "soic", "MS-013": "soic", "MO-187": "soic", "MS-187": "soic",
    "MO-153": "soic", "MO-194": "soic", "MO-203": "soic", "MS-012AC": "soic",
    "MO-137": "soic", "MO-150": "soic", "MS-001": "soic",
}


def _value_for(tokens: list[str], symbol: str, take: int = 3) -> float | None:
    """Find a dimension symbol and return its NOMINAL mm value: the BSC/REF value
    if a single controlled dim, else the middle of min/nom/max. Matching is
    CASE-SENSITIVE — JEDEC uses case to distinguish dims (e = pitch vs E = body) —
    and value tokens must start with a digit so a symbol like 'E2' isn't read as a
    number. Only the first (mm) numeric group is read; the inch columns are skipped."""
    for i, t in enumerate(tokens):
        if t != symbol:                       # exact case
            continue
        nums: list[float] = []
        j = i + 1
        while j < len(tokens) and len(nums) < take:
            tk = tokens[j]
            if tk == "/":                     # symbol separator → skip
                j += 1
                continue
            if tk and tk[0].isdigit():        # a value cell (e.g. "0.40BSC", "4.4")
                m = _NUM.search(tk)
                nums.append(float(m.group(1)))
                if "BSC" in tk.upper() or "REF" in tk.upper():
                    return nums[0]
                j += 1
            else:
                break                         # next symbol / header → row ended
        if nums:
            return nums[1] if len(nums) >= 3 else nums[0]
    return None


def _family(text: str) -> tuple[str, str]:
    """(family, jedec-code) from a JEDEC outline reference or package keywords."""
    u = text.upper()
    m = re.search(r"\b(M[OS]-\d{3})\b", u)
    if m and m.group(1) in _JEDEC:
        return _JEDEC[m.group(1)], m.group(1)
    for kw, fam in (("QFN", "qfn"), ("DFN", "qfn"), ("VQFN", "qfn"),
                    ("LQFP", "qfp"), ("TQFP", "qfp"), ("QFP", "qfp"),
                    ("TSSOP", "soic"), ("SSOP", "soic"), ("SOIC", "soic"),
                    ("MSOP", "soic"), ("SON", "soic")):
        if kw in u:
            return fam, (m.group(1) if m else "")
    return "", (m.group(1) if m else "")


def parse_package_dims(text: str) -> dict | None:
    """Parse the mechanical-dimensions table. None if no usable table found."""
    if not text or "BSC" not in text.upper() and "NOM" not in text.upper():
        return None
    # The table region is around the dimension symbols; tokenise on whitespace.
    tokens = text.replace("/", " / ").split()
    # Re-join combined symbols like "D / E", "D2 / E2" back so they match.
    joined, k = [], 0
    while k < len(tokens):
        if (k + 2 < len(tokens) and tokens[k + 1] == "/"
                and re.fullmatch(r"[A-Za-z][0-9A-Za-z]?", tokens[k])
                and re.fullmatch(r"[A-Za-z][0-9A-Za-z]?", tokens[k + 2])):
            joined.append(tokens[k]); joined.append(tokens[k + 2]); k += 3
        else:
            joined.append(tokens[k]); k += 1
    tk = joined

    pitch = _value_for(tk, "e")
    body_w = _value_for(tk, "D") or _value_for(tk, "E")
    body_l = _value_for(tk, "E") or body_w
    thermal_w = _value_for(tk, "D2") or _value_for(tk, "E2")
    thermal_l = _value_for(tk, "E2") or thermal_w
    pad_w = _value_for(tk, "b")          # lead/terminal width
    pad_l = _value_for(tk, "L")          # lead/terminal length
    fam, jedec = _family(text)

    if not (pitch and body_w):           # need at least pitch + body to be useful
        return None
    return {
        "family": fam, "jedec": jedec,
        "pitch": pitch, "body_w": body_w, "body_l": body_l or body_w,
        "pad_w": pad_w, "pad_l": pad_l,
        "thermal_w": thermal_w, "thermal_l": thermal_l,
    }


# ── self-test against the real RTL8153 mechanical table ───────────────────────
if __name__ == "__main__":
    import sys
    from pathlib import Path
    try:
        import fitz
    except ImportError:
        print("PyMuPDF required for the self-test"); sys.exit(0)
    sample = (Path(__file__).resolve().parents[2] / "testdata" / "datasheets" /
              "source" / "C2764167.pdf")
    doc = fitz.open(sample)
    text = ""
    for i in range(len(doc)):
        t = doc[i].get_text()
        if "BSC" in t and ("MO-220" in t or "D2" in t):
            text = t; break
    dims = parse_package_dims(text)
    print("RTL8153 parsed dimensions:")
    for k, v in (dims or {}).items():
        print(f"  {k:10s} {v}")
    assert dims and abs(dims["pitch"] - 0.40) < 1e-6, "pitch should be 0.40"
    assert abs(dims["body_w"] - 6.00) < 1e-6, "body should be 6.00"
    assert dims["family"] == "qfn", "MO-220 → QFN"
    assert abs(dims["thermal_w"] - 4.4) < 1e-6, "thermal pad 4.4"
    print("\nfootprint_extractor OK — exact dims parsed from the datasheet table")

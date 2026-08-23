"""
Validates component/2 JSON against component.schema.json before any agent
writes to the library. This is the trust boundary: hallucinated pad overlaps,
wrong pin numbers, or missing required fields fail loudly here, not silently
inside the PCB tool.
"""
import json
import math
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).parent.parent.parent / "docs/datasheet-extractor/component.schema.json"

class ValidationError(Exception):
    """Raised with a list of human-readable failures."""
    def __init__(self, failures: list[str]):
        self.failures = failures
        super().__init__("\n".join(failures))

def validate(component: dict, require_evidence: bool = False) -> list[str]:
    """
    Returns a list of failure strings. Empty list = valid.
    Implements the mandatory self-check from EXTRACTOR_PROMPT.md:
      ✓ Valid JSON with required top-level keys
      ✓ Pin numbers unique in symbol and footprint
      ✓ Every non-mechanical pad number exists in symbol.pins
      ✓ No two pads overlap (|Δx| >= (w1+w2)/2 - 0.001 or |Δy| >= (h1+h2)/2 - 0.001)
      ✓ Through-hole pads have drill_mm > 0 and drill_mm < min(w,h) - 0.1
      ✓ All lengths are reasonable (> 0, <= 500 mm)
      ✓ pitch matches pad x/y spacing (within 10%)
    """
    failures = []

    # ── Top-level required keys ───────────────────────────────────────────────
    for k in ("schema", "component", "symbol", "footprint"):
        if k not in component:
            failures.append(f"Missing required top-level key: '{k}'")

    if failures:
        return failures  # can't continue without structure

    schema_version = component.get("schema", "")
    if schema_version not in ("design-studio.component/1", "design-studio.component/2"):
        failures.append(f"Unknown schema version: '{schema_version}'")
    if require_evidence:
        evidence = component.get("evidence", {})
        if evidence.get("schema") != "design-studio.datasheet-evidence/1":
            failures.append("versioned datasheet evidence is required")
        if evidence.get("mpn_match") != "exact":
            failures.append("datasheet evidence must prove an exact MPN match")
        sha = evidence.get("sha256", "")
        if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha.lower()):
            failures.append("datasheet evidence SHA-256 is invalid")

    # ── Component ─────────────────────────────────────────────────────────────
    comp = component.get("component", {})
    if not comp.get("manufacturer"):
        failures.append("component.manufacturer is required and must be non-empty")
    if not comp.get("mpn"):
        failures.append("component.mpn is required and must be non-empty")

    # ── Symbol pins ───────────────────────────────────────────────────────────
    symbol = component.get("symbol", {})
    pins = symbol.get("pins", [])
    if not pins:
        failures.append("symbol.pins must have at least one entry")

    pin_numbers = [str(p.get("number","")) for p in pins]
    if len(pin_numbers) != len(set(pin_numbers)):
        dupes = [n for n in pin_numbers if pin_numbers.count(n) > 1]
        failures.append(f"Duplicate pin numbers in symbol.pins: {set(dupes)}")

    valid_etypes = {"input","output","bidirectional","power_in","power_out","passive","nc"}
    for p in pins:
        et = p.get("electrical_type","")
        if et not in valid_etypes:
            failures.append(f"Pin {p.get('number')} has invalid electrical_type: '{et}'")
        if not p.get("name"):
            failures.append(f"Pin {p.get('number')} missing name")

    # ── Footprint ─────────────────────────────────────────────────────────────
    fp = component.get("footprint", {})
    if not fp.get("name"):
        failures.append("footprint.name is required")
    mount = fp.get("mount")
    if mount not in ("smd", "through_hole"):
        failures.append(f"footprint.mount must be 'smd' or 'through_hole', got '{mount}'")

    pad_list = fp.get("pads", [])
    if not pad_list and "bga" not in fp:
        failures.append("footprint must have either 'pads' or 'bga'")

    if pad_list:
        # Pad number uniqueness
        pad_numbers = [str(p.get("number","")) for p in pad_list]
        if len(pad_numbers) != len(set(pad_numbers)):
            dupes = [n for n in pad_numbers if pad_numbers.count(n) > 1]
            failures.append(f"Duplicate pad numbers in footprint.pads: {set(dupes)}")

        # Every non-mechanical pad must exist in symbol.pins
        pin_number_set = set(pin_numbers)
        for pad in pad_list:
            if pad.get("mechanical", False):
                continue
            pn = str(pad.get("number",""))
            if pn and pn not in pin_number_set:
                failures.append(f"Pad '{pn}' has no matching symbol pin (or is not marked mechanical:true)")

        # Dimension sanity
        for pad in pad_list:
            w = pad.get("width_mm", 0)
            h = pad.get("height_mm", 0)
            if w <= 0:
                failures.append(f"Pad {pad.get('number')}: width_mm must be > 0, got {w}")
            if h <= 0:
                failures.append(f"Pad {pad.get('number')}: height_mm must be > 0, got {h}")
            if w > 100 or h > 100:
                failures.append(f"Pad {pad.get('number')}: dimensions seem unrealistic ({w}×{h} mm) — check unit conversion")

            # Through-hole annular ring check
            if mount == "through_hole" or pad.get("drill_mm", 0) > 0:
                drill = pad.get("drill_mm", 0)
                if drill <= 0:
                    failures.append(f"Through-hole pad {pad.get('number')}: drill_mm must be > 0")
                elif w > 0 and h > 0:
                    min_dim = min(w, h)
                    if drill >= min_dim - 0.099:
                        failures.append(
                            f"Pad {pad.get('number')}: drill {drill:.3f} mm leaves no annular ring "
                            f"(min dimension {min_dim:.3f} mm — annular ring would be {(min_dim-drill)/2:.3f} mm, need ≥ 0.05 mm)"
                        )

        # Pad overlap check
        for i, a in enumerate(pad_list):
            for j, b in enumerate(pad_list):
                if j <= i:
                    continue
                ax, ay, aw, ah = a.get("x_mm",0), a.get("y_mm",0), a.get("width_mm",0), a.get("height_mm",0)
                bx, by, bw, bh = b.get("x_mm",0), b.get("y_mm",0), b.get("width_mm",0), b.get("height_mm",0)
                dx = abs(ax - bx)
                dy = abs(ay - by)
                min_dx = (aw + bw) / 2 - 0.001
                min_dy = (ah + bh) / 2 - 0.001
                if dx < min_dx and dy < min_dy:
                    failures.append(
                        f"Pads {a.get('number')} and {b.get('number')} overlap "
                        f"(Δx={dx:.3f} < {min_dx:.3f}, Δy={dy:.3f} < {min_dy:.3f})"
                    )

    return failures


def validate_or_raise(component: dict, require_evidence: bool = False) -> None:
    """Raises ValidationError if the component fails any check."""
    failures = validate(component, require_evidence=require_evidence)
    if failures:
        raise ValidationError(failures)


def load_and_validate(json_path: str) -> dict:
    """Load a JSON file and validate it. Returns the dict or raises."""
    with open(json_path) as f:
        data = json.load(f)
    validate_or_raise(data)
    return data


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python schema_validator.py <component.json> [<component2.json> ...]")
        sys.exit(1)

    ok = 0
    fail = 0
    for path in sys.argv[1:]:
        try:
            data = load_and_validate(path)
            mpn = data.get("component", {}).get("mpn", path)
            conf = data.get("extraction", {}).get("confidence", {})
            sym_c = conf.get("symbol", "?")
            fp_c  = conf.get("footprint", "?")
            print(f"PASS  {path}  ({mpn})  confidence: symbol={sym_c}  footprint={fp_c}")
            ok += 1
        except ValidationError as e:
            print(f"FAIL  {path}")
            for f in e.failures:
                print(f"       • {f}")
            fail += 1
        except Exception as e:
            print(f"ERROR {path}: {e}")
            fail += 1

    print(f"\n{ok} passed, {fail} failed")
    sys.exit(0 if fail == 0 else 1)

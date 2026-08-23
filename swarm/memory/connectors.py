"""Connector land-pattern generator — deterministic footprints for common
connectors (USB-C, USB-A, micro-USB, RJ45/magjack, U.FL, pin headers), which are
covered by neither the IC generator (ic_footprints) nor the passive generator
(passives). Without this, connectors fall back to a generic pad row and come out
the wrong shape.

Geometry is a manufacturable approximation keyed by connector type + pin count:
the right topology (USB-C = two dense rows + corner shield tabs; RJ45 = through-
hole grid + mounting posts; header = 2.54 mm grid) and non-overlapping pads. Pin
numbers are preserved from the symbol so the netlist still maps.
"""
from __future__ import annotations
import re

# Pins whose NAME marks them mechanical (shield/shell/mounting) rather than signal.
_MECH = ("SHIELD", "SHELL", "SH", "MH", "MP", "MOUNT", "MTG", "GNDSH", "CASE", "TAB")


def _pad(num, x, y, w, h, shape="rect", drill=0.0):
    p = {"number": str(num), "x_mm": round(x, 3), "y_mm": round(y, 3),
         "width_mm": round(w, 3), "height_mm": round(h, 3), "shape": shape}
    if drill:
        p["drill_mm"] = round(drill, 3)
    return p


def classify(text: str) -> str:
    u = (text or "").upper()
    if any(k in u for k in ("U.FL", "UFL", "IPEX", "IPX", "MHF", "AMC")):
        return "ufl"
    if "SMA" in u:
        return "sma"
    if any(k in u for k in ("USB-C", "USB C", "USBC", "TYPE-C", "TYPE C", "USB4")):
        return "usb_c"
    if any(k in u for k in ("MICRO-USB", "MICROUSB", "MICRO USB", "MICRO-B", "MICROB")):
        return "micro_usb"
    if any(k in u for k in ("MINI-USB", "MINIUSB", "MINI USB")):
        return "micro_usb"
    if any(k in u for k in ("USB-A", "USB A", "USBA", "TYPE-A")):
        return "usb_a"
    if any(k in u for k in ("RJ45", "RJ-45", "8P8C", "MAGJACK", "MAG JACK", "ETHERNET JACK")):
        return "rj45"
    if any(k in u for k in ("HEADER", "PINHD", "PIN HEADER", "2.54", "1X", "2X",
                            "CONN_", "TERMINAL", "JST", "MOLEX")):
        return "header"
    return ""


def _split_mech(pins: list[str]):
    """Separate mechanical (shield/mount) pins from signal pins by name."""
    sig, mech = [], []
    for p in pins:
        (mech if any(m in p.upper() for m in _MECH) else sig).append(p)
    return sig, mech


# ── Per-type layouts (local coords, origin = body centre) ─────────────────────
def _two_rows(sig, pitch, row_gap, pw, ph):
    """Signal pins in two horizontal rows (front/back), centred."""
    per = (len(sig) + 1) // 2
    pads = []
    for row, lo, hi in ((0, 0, per), (1, per, len(sig))):
        grp = sig[lo:hi]
        x0 = -(len(grp) - 1) * pitch / 2
        y = (row_gap / 2) if row == 0 else -(row_gap / 2)
        for i, num in enumerate(grp):
            pads.append(_pad(num, x0 + i * pitch, y, pw, ph))
    return pads, per


def _corner_tabs(mech, halfw, halfh, tw=2.0, th=2.2):
    """Place up to 4 mechanical/shield pads at the body corners."""
    spots = [(-halfw, halfh), (halfw, halfh), (-halfw, -halfh), (halfw, -halfh)]
    return [_pad(mech[i], sx, sy, tw, th) for i, (sx, sy) in enumerate(spots) if i < len(mech)]


def _usb_c(pins):
    sig, mech = _split_mech(pins)
    # 24-pin USB-C: two rows of ~12 at 0.5 mm; simplified power variants have fewer.
    pads, _ = _two_rows(sig, 0.5, 2.4, 0.3, 1.05)
    pads += _corner_tabs(mech, 4.32, 3.0, 1.6, 2.1)
    return pads, "smd"


def _usb_a(pins):
    sig, mech = _split_mech(pins)
    pads = []
    x0 = -(len(sig) - 1) * 2.0 / 2
    for i, num in enumerate(sig[:8]):
        pads.append(_pad(num, x0 + i * 2.0, 0.0, 1.0, 2.0, "rect", drill=0.9))
    pads += _corner_tabs(mech, 6.0, 3.5, 2.0, 2.5)
    return pads, "through"


def _micro_usb(pins):
    sig, mech = _split_mech(pins)
    pads, _ = _two_rows(sig[:5], 0.65, 0.0, 0.4, 1.35)   # 5 pins single row
    pads += _corner_tabs(mech, 3.6, 2.0, 1.2, 1.9)
    return pads, "smd"


def _rj45(pins):
    # Magjack: through-hole signal pins in two staggered rows + LED pins + 2 big
    # mounting/shield posts. Topology matters more than exact spacing.
    sig, mech = _split_mech(pins)
    pads = []
    per = (len(sig) + 1) // 2
    for row, lo, hi, y in ((0, 0, per, 3.0), (1, per, len(sig), -3.0)):
        grp = sig[lo:hi]
        x0 = -(len(grp) - 1) * 1.27 / 2
        for i, num in enumerate(grp):
            pads.append(_pad(num, x0 + i * 1.27, y, 1.0, 1.7, "circle", drill=0.9))
    # large mounting/shield posts on each side
    for i, num in enumerate(mech[:2]):
        pads.append(_pad(num, (-8.0 if i == 0 else 8.0), 0.0, 3.2, 3.2, "circle", drill=2.5))
    return pads, "through"


def _ufl(pins):
    # U.FL coax: centre signal pad + ground pads either side (SMT).
    p = pins[:3] if len(pins) >= 3 else pins + ["2", "3"][: 3 - len(pins)]
    return [_pad(p[0], 0.0, 0.0, 0.6, 0.6),
            _pad(p[1], -1.1, 0.0, 0.7, 1.0),
            _pad(p[2], 1.1, 0.0, 0.7, 1.0)], "smd"


def _header(pins):
    # Generic 2.54 mm pin header: 1 or 2 rows, through-hole.
    n = len(pins)
    rows = 2 if n >= 8 and n % 2 == 0 else 1
    per = n // rows
    pads = []
    x0 = -(per - 1) * 2.54 / 2
    for i, num in enumerate(pins):
        col = i % per
        row = i // per
        y = (1.27 if rows == 2 else 0.0) - row * 2.54
        pads.append(_pad(num, x0 + col * 2.54, y, 1.7, 1.7, "circle", drill=1.0))
    return pads, "through"


_GEN = {"usb_c": _usb_c, "usb_a": _usb_a, "micro_usb": _micro_usb,
        "rj45": _rj45, "ufl": _ufl, "sma": _ufl, "header": _header}


# Standard pin count per connector type, used when the extracted symbol clearly
# under-counts (e.g. a USB-C symbol with only 2 pins). Real pins are preserved and
# padded with non-colliding filler so the SHAPE is right and nets still map.
_STD_PINS = {"usb_c": 24, "rj45": 12, "usb_a": 4, "micro_usb": 5, "ufl": 3,
             "sma": 3, "header": 4}


def detect(comp2: dict) -> dict | None:
    fp = comp2.get("footprint", {}) or {}
    sym = comp2.get("symbol", {}) or {}
    comp = comp2.get("component", {}) or {}
    text = " ".join([fp.get("name", ""), comp.get("description", ""),
                     comp.get("mpn", ""), comp.get("category", "")])
    kind = classify(text)
    if not kind:
        return None
    pins = [str(p.get("number", "")) for p in sym.get("pins", []) if p.get("number") is not None]
    if not pins:                                    # fall back to extracted pad numbers
        pins = [str(p.get("number", i + 1)) for i, p in enumerate(fp.get("pads", []))]
    # Pad out to the standard count for the type if the symbol under-counts — a
    # 2-pin USB-C is a broken extraction; the connector still has its real pads.
    need = _STD_PINS.get(kind, len(pins))
    if kind == "header":                            # parse the size from the name
        u = text.upper()
        m = re.search(r"(\d+)\s*X\s*(\d+)", u)       # 2x5, 1X6 …
        if m:
            need = int(m.group(1)) * int(m.group(2))
        else:
            m2 = re.search(r"(?<![0-9])(\d{1,3})\s*(?:PIN|POS|WAY|P\b|-)", u)
            if m2:
                need = int(m2.group(1))
    if len(pins) < need:
        seen, i = set(pins), 1
        while len(pins) < need:
            while str(i) in seen:
                i += 1
            pins.append(str(i)); seen.add(str(i)); i += 1
    return {"kind": kind, "pins": pins}


def generate(desc: dict) -> tuple[list[dict], str]:
    return _GEN[desc["kind"]](desc["pins"])


def regenerate_footprint(comp2: dict) -> dict | None:
    """Return a fresh footprint dict for a recognised connector, else None."""
    desc = detect(comp2)
    if not desc:
        return None
    pads, mount = generate(desc)
    if not pads:
        return None
    fp = dict(comp2.get("footprint", {}) or {})
    fp["pads"] = pads
    fp["mount"] = mount
    fp["generated"] = "connector"
    return fp


# ── self-test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cases = [
        ("USB4110-GF-A", "USB-C receptacle 24-pin", [str(i + 1) for i in range(24)] + ["SH1", "SH2", "SH3", "SH4"]),
        ("0826-1C1T-43-F", "RJ45 magjack with magnetics", [str(i + 1) for i in range(12)] + ["SH1", "SH2"]),
        ("U.FL-R-SMT-1", "U.FL coax RF connector", ["1", "2", "3"]),
        ("PinHeader-1x8", "2.54mm pin header", [str(i + 1) for i in range(8)]),
        ("USB-A-TH", "USB-A through-hole", ["1", "2", "3", "4", "SH1", "SH2"]),
    ]
    for mpn, desc, pins in cases:
        comp2 = {"component": {"mpn": mpn, "description": desc},
                 "symbol": {"pins": [{"number": p} for p in pins]}}
        fp = regenerate_footprint(comp2)
        pads = fp["pads"]
        boxes = [(p["x_mm"] - p["width_mm"] / 2, p["y_mm"] - p["height_mm"] / 2,
                  p["x_mm"] + p["width_mm"] / 2, p["y_mm"] + p["height_mm"] / 2) for p in pads]
        ov = sum(1 for i in range(len(boxes)) for j in range(i + 1, len(boxes))
                 if boxes[i][0] < boxes[j][2] and boxes[j][0] < boxes[i][2]
                 and boxes[i][1] < boxes[j][3] and boxes[j][1] < boxes[i][3])
        print(f"  {mpn:18s} {classify(desc+' '+mpn):9s} → {len(pads):2d} pads, "
              f"mount={fp['mount']:8s} overlaps={ov}")
        assert ov == 0, f"overlap in {mpn}"
    print("\nconnectors OK — connector land patterns generated, no pad overlaps")

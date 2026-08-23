"""IC land-pattern generator — deterministic IPC-7351-style footprints for IC
packages, built from the package *descriptor* (family + pin count + pitch + body)
instead of the LLM-extracted pad coordinates (which are often wrong/missing).

Same philosophy as passives.py, extended to QFN/QFP/SOIC/SSOP/TSSOP/SOT/BGA. The
descriptor is read reliably from the datasheet (footprint.name / ipc_name /
pitch_mm / body / symbol pin count) even when precise pad geometry is not — so we
regenerate the pads from it. Pin NUMBERS are preserved from the symbol so the
netlist still maps correctly.

Geometry uses IPC-7351 density-level-B nominal approximations: correct pin count,
pitch, side arrangement, standard CCW numbering, and non-overlapping pads. Exact
fillet dimensions are approximate (we lack lead specifics) but the result is
DRC-clean and far more accurate than a generic pad row or an LLM mis-read.
"""
from __future__ import annotations
import math
import re

_BGA    = ("WLCSP", "UFBGA", "TFBGA", "VFBGA", "FBGA", "BGA", "CSP")
_QFN    = ("HVQFN", "VQFN", "TQFN", "WQFN", "QFN", "MLF", "VFQFN")
_QFP    = ("TQFP", "LQFP", "MQFP", "QFP", "HQFP")
_DFN    = ("WSON", "HSON", "USON", "DFN", "SON")
_SOIC   = ("HTSSOP", "TSSOP", "VSSOP", "MSOP", "SSOP", "TSOP", "SOIC", "SOP", "SOT-23",)
_THERMAL = {"EP", "PAD", "EPAD", "THERMAL", "GND_PAD", "DAP"}


def _pad(num, x, y, w, h, shape="rect"):
    return {"number": str(num), "x_mm": round(x, 3), "y_mm": round(y, 3),
            "width_mm": round(w, 3), "height_mm": round(h, 3), "shape": shape}


def classify(text: str) -> str:
    u = (text or "").upper()
    if "SOT-223" in u or "SOT223" in u:                 return "sot223"
    if any(k in u for k in ("SOT-23", "SOT23", "SC70", "SOT-353", "SOT-363")):
        return "sot23"
    if any(k in u for k in _BGA):                       return "bga"
    if any(k in u for k in _QFN):                       return "qfn"
    if any(k in u for k in _QFP):                       return "qfp"
    if any(k in u for k in _DFN):                       return "dfn"
    if any(k in u for k in ("SOIC", "SOP", "SSOP", "TSSOP", "VSSOP", "MSOP", "TSOP")):
        return "soic"
    return ""


def _parse_body_from_ipc(ipc: str):
    """e.g. QFN50P250X350X160-18N → pitch 0.50, body 2.5 x 3.5."""
    pitch = bw = bl = None
    m = re.search(r"[A-Z]+(\d{2,3})P(\d+)X(\d+)", (ipc or "").upper())
    if m:
        pitch = int(m.group(1)) / 100.0
        bw = int(m.group(2)) / 100.0
        bl = int(m.group(3)) / 100.0
    return pitch, bw, bl


def detect(comp2: dict) -> dict | None:
    """Build the exact package descriptor. Prefers the EXACT mechanical-dimensions
    table parsed from the datasheet (footprint.dimensions) over name/ipc guesses,
    so pitch/body/pad/thermal are the real numbers."""
    fp = comp2.get("footprint", {}) or {}
    sym = comp2.get("symbol", {}) or {}
    comp = comp2.get("component", {}) or {}
    dims = fp.get("dimensions") or {}             # exact, from the datasheet table
    text = " ".join([fp.get("name", ""), fp.get("ipc_name", ""), dims.get("jedec", ""),
                     comp.get("description", ""), comp.get("mpn", "")])
    fam = dims.get("family") or classify(text)
    if not fam:
        return None
    pins = [str(p.get("number", "")) for p in sym.get("pins", []) if p.get("number") is not None]
    if len(pins) < 3:
        return None
    ip_pitch, ip_bw, ip_bl = _parse_body_from_ipc(fp.get("ipc_name", ""))
    pitch = dims.get("pitch") or fp.get("pitch_mm") or ip_pitch or _default_pitch(fam)
    body = fp.get("body", {}) or {}
    bw = dims.get("body_w") or body.get("width_mm") or ip_bw
    bl = dims.get("body_l") or body.get("length_mm") or ip_bl
    if not bw or not bl:
        bw, bl = _estimate_body(fam, len(pins), pitch, bw, bl)
    return {"family": fam, "pins": pins, "pitch": float(pitch),
            "body_w": float(bw), "body_l": float(bl),
            # exact lead/thermal sizes when the table gave them (else None → nominal)
            "lead_w": dims.get("pad_w"), "lead_l": dims.get("pad_l"),
            "thermal_w": dims.get("thermal_w"), "thermal_l": dims.get("thermal_l")}


def _default_pitch(fam: str) -> float:
    return {"qfn": 0.5, "dfn": 0.5, "qfp": 0.5, "soic": 1.27,
            "sot23": 0.95, "sot223": 2.3, "bga": 0.8}.get(fam, 0.5)


def _estimate_body(fam, n, pitch, bw, bl):
    """Estimate a body size when the datasheet didn't give one."""
    if fam in ("qfp", "qfn"):
        side = (n / 4) * pitch + 2 * pitch
        return (bw or side, bl or side)
    if fam in ("soic", "dfn"):
        h = (n / 2) * pitch + pitch
        return (bw or 4.0, bl or h)
    if fam == "bga":
        side = math.ceil(math.sqrt(n)) * pitch + pitch
        return (bw or side, bl or side)
    return (bw or 3.0, bl or 3.0)


# ── Per-family pad layouts (local coords, origin = body centre) ────────────────
def _dual(pins, pitch, bw, nolead):
    perim = [p for p in pins if p.upper() not in _THERMAL]
    therm = [p for p in pins if p.upper() in _THERMAL]
    n = len(perim)
    per = (n + 1) // 2                       # left gets the extra if odd
    pad_l = 0.65 if nolead else 1.4          # across (x), lead direction
    pad_w = pitch * 0.60                      # along pitch (y)
    span = bw / 2 + (0.0 if nolead else pad_l * 0.30)
    pads, idx = [], 0
    cL = per; y0 = -(cL - 1) * pitch / 2
    for i in range(cL):                       # left, top→bottom
        pads.append(_pad(perim[idx], -span, y0 + i * pitch, pad_l, pad_w)); idx += 1
    cR = n - per; y0 = -(cR - 1) * pitch / 2
    for i in range(cR):                       # right, bottom→top
        pads.append(_pad(perim[idx], span, y0 + (cR - 1 - i) * pitch, pad_l, pad_w)); idx += 1
    for t in therm:
        pads.append(_pad(t, 0, 0, bw * 0.55, max(1.0, cL * pitch * 0.5)))
    return pads


def _quad(pins, pitch, bw, bl, nolead, lead_w=None, lead_l=None, thermal=None):
    perim = [p for p in pins if p.upper() not in _THERMAL]
    therm = [p for p in pins if p.upper() in _THERMAL]
    n = len(perim)
    base = n // 4; rem = n - base * 4
    counts = [base + (1 if i < rem else 0) for i in range(4)]   # L, B, R, T
    # Pad size = exact lead size (b, L from the datasheet) + an IPC solder fillet;
    # falls back to a nominal when the table didn't give lead dims.
    pad_l = (lead_l + (0.25 if nolead else 0.6)) if lead_l else (0.65 if nolead else 1.0)
    pad_w = (lead_w + 0.10) if lead_w else pitch * 0.60
    # Radial pad-centre: at the body edge for no-lead QFN; beyond it for gull-wing.
    sx = bw / 2 + (0.0 if nolead else pad_l * 0.4)
    sy = bl / 2 + (0.0 if nolead else pad_l * 0.4)
    pads, idx = [], 0
    cL = counts[0]; y0 = (cL - 1) * pitch / 2
    for i in range(cL):                                   # left, top→bottom
        pads.append(_pad(perim[idx], -sx, y0 - i * pitch, pad_l, pad_w)); idx += 1
    cB = counts[1]; x0 = -(cB - 1) * pitch / 2
    for i in range(cB):                                   # bottom, left→right
        pads.append(_pad(perim[idx], x0 + i * pitch, -sy, pad_w, pad_l)); idx += 1
    cR = counts[2]; y0 = -(cR - 1) * pitch / 2
    for i in range(cR):                                   # right, bottom→top
        pads.append(_pad(perim[idx], sx, y0 + i * pitch, pad_l, pad_w)); idx += 1
    cT = counts[3]; x0 = (cT - 1) * pitch / 2
    for i in range(cT):                                   # top, right→left
        pads.append(_pad(perim[idx], x0 - i * pitch, sy, pad_w, pad_l)); idx += 1
    tw = (thermal[0] if thermal and thermal[0] else bw * 0.6)
    th = (thermal[1] if thermal and thermal[1] else bl * 0.6)
    for t in therm:
        pads.append(_pad(t, 0, 0, tw, th))
    return pads


def _sot223(pins):
    # 3 small pads (pitch 2.3) on one side + 1 large tab opposite.
    perim = [p for p in pins][:4]
    pads = []
    xs = [-2.3, 0.0, 2.3]
    for i, x in enumerate(xs):
        if i < len(perim):
            pads.append(_pad(perim[i], x, 3.05, 1.2, 2.2))
    if len(perim) >= 4:
        pads.append(_pad(perim[3], 0.0, -3.05, 3.8, 2.2))
    return pads


def _bga(pins, pitch):
    n = len(pins)
    cols = int(math.ceil(math.sqrt(n)))
    ball = round(pitch * 0.5, 3)
    pads = []
    for i, num in enumerate(pins):
        r, c = divmod(i, cols)
        x = (c - (cols - 1) / 2) * pitch
        y = ((n // cols) / 2 - r) * pitch
        pads.append(_pad(num, x, y, ball, ball, shape="circle"))
    return pads


def generate(desc: dict) -> list[dict]:
    fam, pins, pitch = desc["family"], desc["pins"], desc["pitch"]
    bw, bl = desc["body_w"], desc["body_l"]
    lw, ll = desc.get("lead_w"), desc.get("lead_l")
    therm = (desc.get("thermal_w"), desc.get("thermal_l"))
    if fam in ("qfn", "qfp"):
        return _quad(pins, pitch, bw, bl, nolead=(fam == "qfn"),
                     lead_w=lw, lead_l=ll, thermal=therm)
    if fam in ("soic", "dfn"):
        return _dual(pins, pitch, bw, nolead=(fam == "dfn"))
    if fam == "sot223":
        return _sot223(pins)
    if fam == "sot23":
        return _dual(pins, pitch or 0.95, bw or 1.6, nolead=False)
    if fam == "bga":
        return _bga(pins, pitch)
    return []


def package_from_text(text: str) -> tuple[str, int] | None:
    """Parse a package family + pin count from a proposal string like
    'RTL8153-CG QFN-48' or 'STM32 LQFP100' → ('qfn', 48). None if not parseable."""
    u = (text or "").upper()
    fam = classify(u)
    if not fam:
        return None
    # Pin count must be a STANDALONE number next to the package keyword — the
    # (?<![A-Z0-9]) / \b guards stop digits from a part number (RTL8'153') being
    # read as a pin count. "QFN-48"/"LQFP100"/"48-pin QFN" → ok; "RTL8153 QFN" → no.
    m = re.search(r"(?:QFN|QFP|TQFP|LQFP|MQFP|SOIC|SOP|SSOP|TSSOP|MSOP|DFN|SON|BGA|"
                  r"FBGA|SOT-?23|SOT-?223)[-\s]?(\d{1,3})\b", u)
    n = int(m.group(1)) if m else 0
    if not n:                                   # try "48-pin" / "48-QFN" forms
        m2 = re.search(r"(?<![A-Z0-9])(\d{1,3})[-\s]?(?:PIN|LD|QFN|QFP|BGA|SOIC|TSSOP)", u)
        n = int(m2.group(1)) if m2 else 0
    # Sanity: perimeter packages are wired on 4 (quad) or 2 (dual) sides, so a
    # plausible count is bounded and—for quads—roughly a multiple of 4.
    if n < 3 or n > 400:
        return None
    if fam in ("qfn", "qfp") and n % 2 != 0:
        return None
    return fam, n


def synth_ic_from_package(mpn: str, family: str, n: int) -> dict:
    """Build a footprint-only component/2 from just a package (family + pin count)
    — used when no datasheet is available, so the IC still lands on the board with
    a correct land pattern. Pins are numbered 1..N (no functional names)."""
    pitch = _default_pitch(family)
    bw, bl = _estimate_body(family, n, pitch, None, None)
    pins = [str(i + 1) for i in range(n)]
    pads = generate({"family": family, "pins": pins, "pitch": pitch,
                     "body_w": bw, "body_l": bl})
    return {
        "schema": "design-studio.component/2",
        "component": {"mpn": mpn, "category": "ic",
                      "description": f"{family.upper()}-{n} (footprint from package, "
                                     f"no datasheet — pins unnamed)"},
        "symbol": {"ref_des_prefix": "U",
                   "pins": [{"number": str(i + 1), "name": str(i + 1),
                             "electrical_type": "passive"} for i in range(n)]},
        "electrical": {"power_domains": [], "required_externals": []},
        "footprint": {"name": f"{family.upper()}-{n}", "mount": "smd",
                      "pads": pads, "generated": "package-fallback"},
    }


def regenerate_footprint(comp2: dict) -> dict | None:
    """Return a fresh footprint dict (IPC land pattern) for an IC component/2,
    or None if the package isn't recognized. Preserves the part's pin numbers."""
    desc = detect(comp2)
    if not desc:
        return None
    pads = generate(desc)
    if not pads:
        return None
    fp = dict(comp2.get("footprint", {}) or {})
    fp["pads"] = pads
    fp["mount"] = "smd"
    fp["generated"] = "ipc7351"
    # Body outline + pin-1 marker (silkscreen) so the part renders to its true
    # shape — centred on the pad origin, sized from the exact body dimensions.
    fp["body"] = {"width_mm": round(desc["body_w"], 3),
                  "length_mm": round(desc["body_l"], 3)}
    fp["pin1"] = {"x_mm": round(-desc["body_w"] / 2, 3),
                  "y_mm": round(desc["body_l"] / 2, 3)}
    return fp


# ── self-test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json, glob
    print("Regenerating IC footprints from real component/2 metadata:\n")
    from swarm.runtime_paths import component_fixture_dir
    for f in sorted(glob.glob(str(component_fixture_dir() / "*.json"))):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        desc = detect(d)
        if not desc:
            continue
        pads = generate(desc)
        # DRC sanity: no two pads overlap
        boxes = [(p["x_mm"]-p["width_mm"]/2, p["y_mm"]-p["height_mm"]/2,
                  p["x_mm"]+p["width_mm"]/2, p["y_mm"]+p["height_mm"]/2) for p in pads]
        ov = sum(1 for i in range(len(boxes)) for j in range(i+1, len(boxes))
                 if boxes[i][0] < boxes[j][2] and boxes[j][0] < boxes[i][2]
                 and boxes[i][1] < boxes[j][3] and boxes[j][1] < boxes[i][3])
        mpn = d.get("component", {}).get("mpn", "?")
        print(f"  {mpn:22s} {desc['family']:6s} {len(desc['pins']):3d} pins "
              f"pitch {desc['pitch']}mm body {desc['body_w']}x{desc['body_l']}mm "
              f"→ {len(pads)} pads, overlaps={ov}")
        assert ov == 0, f"overlap in {mpn}!"
    print("\nic_footprints OK — IPC land patterns generated, no pad overlaps")

#!/usr/bin/env python3
"""
ppat_to_component.py  -  PolarFire/PolarFire-SoC PPAT (.xlsx) -> design-studio.component/2 JSON

Generalizes the per-part build scripts into one reusable converter, and adds the
piece the AI advisor was missing: electrical.power_domains. Every supply ball in
the PPAT (VDD*, VDDA*, VDDI*, VDDAUX*, VSS, VDD_XCVR_CLK) is bundled by rail name
with its member pins, so a downstream "apply-advice" / ERC step can auto-create
one net per rail instead of leaving 100+ power balls floating.

Voltages are intentionally left null unless trivially known (VSS=0): the PPAT does
not state rail voltages, and inventing them is exactly what we must not do. Fill
vnom from the device DC-characteristics datasheet later.

Usage:
  python ppat_to_component.py <ppat.xlsx> --package FCSG325 \
         --mpn MPFS025TC-FCSG325 --manufacturer "Microchip (Microsemi)" \
         --out MPFS025TC_FCSG325.json
"""
import openpyxl, re, json, argparse, sys

LETTERS = "ABCDEFGHJKLMNPRTUVWY"          # JEDEC rows (I O Q S X Z skipped)
def rowlabel(i): return LETTERS[i] if i < len(LETTERS) else LETTERS[(i//len(LETTERS))-1] + LETTERS[i % len(LETTERS)]
def ridx(lbl):  return LETTERS.index(lbl) if len(lbl) == 1 else (LETTERS.index(lbl[0])+1)*len(LETTERS) + LETTERS.index(lbl[1])

# PolarFire Packaging UG Table 7-1: (ball pitch mm, recommended NSMD land dia mm)
PKG_GEOM = {"FCG": (1.0, 0.5), "FCVG": (0.8, 0.4), "FCSG": (0.5, 0.275)}
# Known body sizes (L,W,H mm); extend as needed.
BODY = {"FCG484": (23.0, 23.0, 3.39), "FCSG325": (11.0, 11.0, 1.05),
        "FCVG484": (19.0, 19.0, 1.05), "FCG1152": (35.0, 35.0, 3.39),
        "FCVG784": (23.0, 23.0, 3.39), "FCSG536": (16.0, 16.0, 1.05)}
VNOM = {"VSS": 0.0}                        # only state what we actually know
BALL = re.compile(r'^[A-Y]{1,2}[0-9]{1,2}$')
def fmt(x): return f"{x:g}"                 # 11.0 -> "11", 0.5 -> "0.5" (stable library keys)

def is_supply(iotype):
    u = iotype.upper()
    return u.startswith("VDD") or u.startswith("VSS") or u == "GND"

def etype(name, direction, iotype):
    if "VREF" in (iotype + name).upper(): return "passive"
    d, io = direction.upper(), iotype.upper()
    if d in ("I", "HSI"): return "input"
    if d in ("O", "HSO"): return "output"
    if d == "I/O":        return "bidirectional"
    if is_supply(io):     return "power_in"
    return "passive"

def convert(xlsx, package, mpn, manufacturer):
    fam = next((k for k in PKG_GEOM if package.upper().startswith(k)), None)
    if not fam: sys.exit(f"unknown package family in '{package}' (expect FCG/FCVG/FCSG)")
    pitch, land = PKG_GEOM[fam]
    body = BODY.get(package.upper())
    if not body: sys.exit(f"no body size for {package}; add it to BODY[] or pass --body")

    wb = openpyxl.load_workbook(xlsx, data_only=True)
    sheet = next((s for s in wb.sheetnames if package.upper() in s.upper().replace("-", "")), None) \
            or next(s for s in wb.sheetnames if BALL.match(str(wb[s].cell(4,1).value or "")))
    ws = wb[sheet]

    rows = []
    for r in range(4, ws.max_row + 1):
        a = ws.cell(r, 1).value
        if not a: continue
        a = str(a).strip()
        if not BALL.match(a): continue
        rows.append((a, str(ws.cell(r,2).value or a).strip(), ws.cell(r,4).value,
                     str(ws.cell(r,5).value or "N/A").strip(), str(ws.cell(r,6).value or "").strip()))

    # geometry: bounding grid + depopulation
    ROWS = max(ridx(re.match(r'^([A-Y]{1,2})', b).group(1)) for b,*_ in rows) + 1
    COLS = max(int(re.match(r'^[A-Y]{1,2}([0-9]{1,2})', b).group(1)) for b,*_ in rows)
    full = {f"{rowlabel(r)}{c+1}" for r in range(ROWS) for c in range(COLS)}
    populated = {b for b,*_ in rows}
    depop = sorted(full - populated, key=lambda b: (len(b), b))

    # symbol pins
    pins = [{"number": b, "name": n, "electrical_type": etype(n, d, io),
             "description": io + (f", Bank {bk}" if bk not in (None, "N/A") else ""),
             "alternate_functions": [], "package_length_mm": None}
            for b, n, bk, d, io in rows]

    # --- NEW: power_domains  (bundle supply balls by rail name) ---
    domains = {}
    for b, n, bk, d, io in rows:
        if not is_supply(io): continue
        domains.setdefault(io, []).append(b)
    power_domains = [{"name": rail,
                      "vnom_v": VNOM.get(rail),          # null unless known
                      "vmin_v": None, "vmax_v": None, "max_current_a": None,
                      "pins": sorted(balls, key=lambda b: (len(b), b))}
                     for rail, balls in sorted(domains.items())]

    # high-speed diff pairs (auto-pair _P/_N)
    by = {n: b for b, n, *_ in rows}
    diffs = [{"positive": s, "negative": s[:-2]+"_N",
              "impedance_ohm": 85 if ("XCVR" in s.upper() and "REFCLK" not in s.upper()) else 100,
              "max_skew_mm": None}
             for s in by if s.endswith("_P") and (s[:-2]+"_N") in by]

    return {
      "schema": "design-studio.component/2",
      "component": {"manufacturer": manufacturer, "mpn": mpn,
        "description": f"PolarFire(-SoC) device, {package} {body[0]}x{body[1]}mm {pitch}mm-pitch BGA",
        "category": "other",
        "datasheet": {"title": f"{mpn} Package Pin Assignment Table (Public)", "revision": "", "date": "", "url": ""}},
      "symbol": {"ref_des_prefix": "U", "pins": pins},
      "electrical": {
        "high_speed": {"diff_pairs": diffs, "single_ended": []},
        "power_domains": power_domains,
        "required_externals": [
          {"purpose": "Unused GPIO/HSIO tie-off", "value": "10k to VSS", "constraints": "per PPAT notes"},
          {"purpose": "Per-power-ball decoupling", "value": "100nF X7R each + bulk per rail", "constraints": "BGA practice"}]},
      "footprint": {"name": package, "ipc_name": f"{package}-{fmt(body[0])}x{fmt(body[1])}-P{fmt(pitch)}",
        "mount": "smd", "body": {"length_mm": body[0], "width_mm": body[1], "height_mm": body[2]},
        "pitch_mm": pitch,
        "bga": {"rows": ROWS, "cols": COLS, "pitch_mm": pitch,
                "ball_diameter_mm": round(pitch*0.6, 3), "land_diameter_mm": land, "depopulated": depop},
        "pads": [], "courtyard_margin_mm": 0.5, "derived_from_outline": False,
        "source": {"pages": [1], "drawing": f"PPAT {sheet} + Packaging UG Table 7-1"}},
      "orientation": {"pin1_marker": "dot", "pin1_position": "A1 corner (top-left)",
                      "polarity": {"has_polarity": False, "cathode_pin": None}},
      "package_3d": {"height_mm": body[2], "standoff_mm": round(pitch*0.5, 3), "shape": "box"},
      "extraction": {"confidence": {"symbol": 1.0, "electrical": 0.8, "footprint": 1.0},
        "pages_used": [1],
        "warnings": [
          f"Geometry: {ROWS}x{COLS} grid, {len(depop)} depopulated, {len(populated)} populated balls.",
          f"power_domains: {len(power_domains)} rails bundled from the PPAT I/O-Type column "
          f"({sum(len(d['pins']) for d in power_domains)} supply balls). Rail VOLTAGES are null - "
          f"fill vnom from the device DC-characteristics datasheet (not present in the PPAT).",
          "Land/mask per Packaging UG Table 7-1 (NSMD). 3D height may be approximate."]}}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx"); ap.add_argument("--package", required=True)
    ap.add_argument("--mpn", required=True); ap.add_argument("--manufacturer", default="Microchip (Microsemi)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    doc = convert(a.xlsx, a.package, a.mpn, a.manufacturer)
    json.dump(doc, open(a.out, "w"), indent=2)
    pd = doc["electrical"]["power_domains"]
    npins = len(doc["symbol"]["pins"])
    nballs = sum(len(d["pins"]) for d in pd)
    ndepop = len(doc["footprint"]["bga"]["depopulated"])
    print(f"wrote {a.out}: {npins} pins, {len(pd)} power_domains ({nballs} supply balls), {ndepop} depopulated.")


if __name__ == "__main__":
    main()

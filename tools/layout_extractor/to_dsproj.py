#!/usr/bin/env python3
"""Wrap the extracted USB4910 footprint in a .dsproj the Qt Design Studio app
opens directly. Uses IC_pcb.dsproj as a structural template so every required
top-level key (version, stackup, net_classes, ...) is present, then replaces the
footprints with the extracted one placed at board centre."""
import json, sys, os
from runtime_paths import layout_output_dir

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNTIME_OUT = str(layout_output_dir())
ART  = os.path.join(RUNTIME_OUT, "artifacts.json")
TMPL = os.path.join(ROOT, "IC_pcb.dsproj")
OUT  = os.path.join(RUNTIME_OUT, "USB4910.dsproj")

art = json.load(open(ART))
proj = json.load(open(TMPL))                       # template => all keys present

# ---- consume the rules-driven CLASSIFIER output (primitives), not raw bboxes ----
# Each primitive already carries {tool, shape, snapped params}. Design Studio y is
# +down, so every y is negated here.
pads, regions = [], []
for p in art["primitives"]:
    if p["tool"] == "region":
        regions.append({"net": -1,
                        "pts": [[round(x, 4), round(-y, 4)] for x, y in p["points"]],
                        "hole_pts": [[round(x, 4), round(-y, 4)] for x, y in p.get("hole", [])]})
    elif p["tool"] in ("pad", "hole"):
        th = p["tool"] == "hole"
        pads.append({
            "name": p["ref"],
            "x_mm": round(p["x_mm"], 4),
            "y_mm": round(-p["y_mm"], 4),
            "w_mm": round(p["w_mm"], 4),
            "h_mm": round(p["h_mm"], 4),
            "net": -1, "th": th, "drill_mm": round(p.get("drill_mm", 0.0), 4),
            "pkg_delay_mm": 0.0,
            "shape": p.get("shape", "rect"),
            "corner_r_mm": round(p.get("corner_r_mm", 0.0), 4),
        })

bw, bh = proj.get("board_width_mm", 100.0), proj.get("board_height_mm", 80.0)
footprint = {
    "ref": "J1", "lib": "USB4910",
    "x_mm": round(bw / 2, 3), "y_mm": round(bh / 2, 3),   # place at board centre
    "rot_deg": 0, "side": 0, "h3d_mm": 3.16,
    "pads": pads, "regions": regions,
}

# replace board contents, keep stackup / net_classes / board params from template
proj["footprints"] = [footprint]
for k in ("traces", "vias", "planes", "match_groups", "channel_blocks"):
    if k in proj:
        proj[k] = []
proj["nets"] = []
if "net_table" in proj:
    proj["net_table"] = {}

json.dump(proj, open(OUT, "w"), indent=2)
print(f"wrote {OUT}")
print(f"  footprint J1 / USB4910 : {len(pads)} pads + {len(regions)} regions "
      f"at board centre ({footprint['x_mm']},{footprint['y_mm']})")

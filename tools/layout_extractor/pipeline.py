#!/usr/bin/env python3
"""General extractor orchestration -- the LLM<->CV bridge end to end.

  locate -> render -> register -> verify -> GROUND the LLM footprint against the
  drawing -> emit a grounded component/2 + a discrepancy report.

Input  : a datasheet PDF + the vision-LLM's component/2 JSON (structure discovery,
         per the repo EXTRACTOR_PROMPT; works on any part incl. scans).
Output : <mpn>.grounded.component.json  (LLM footprint annotated with CV evidence
         + warnings) and <mpn>.verify-report.json (per-pad + summary).

CV never DISCOVERS the layout (that doesn't generalize); it VERIFIES/REFINES at the
LLM anchors after auto-registration, which is robust. No part-specific constants.
"""
import os
import json
import numpy as np
import fitz

import locate
import detect
import register
import verify
from runtime_paths import layout_output_dir, sample_datasheet

# discrepancy tolerances (mm)
POS_TOL = 0.15        # anchor->copper offset above this = possible misplacement
SIZE_TOL = 0.20      # |cv-llm| size delta above this = note (size-refine approximate)
MIN_PRESENT_RATE = 0.90


def _pads_of(component):
    return component.get("footprint", {}).get("pads", [])


def run(pdf_path, component, ocr_backend=None):
    """Returns a result dict; does not write files. `component` is a parsed
    component/2 dict (or a path)."""
    if isinstance(component, str):
        component = json.load(open(component))
    pads = _pads_of(component)
    result = {"pdf": pdf_path, "ok": False, "stage": None, "warnings": []}

    fig = locate.locate_layout(pdf_path, ocr_backend=ocr_backend)
    if not fig:
        result["stage"] = "locate"
        result["warnings"].append("land-pattern figure not found (no caption; needs OCR/vision)")
        return result
    result["figure"] = dict(page=fig.page, caption=fig.caption, via_ocr=fig.via_ocr,
                            clip=[round(v, 1) for v in fig.clip])

    page = fitz.open(pdf_path)[fig.page]
    gray, dpi = detect.render_clip(page, fig.clip)
    if not pads:
        result["stage"] = "register"
        result["warnings"].append("LLM component has no footprint pads to ground")
        return result

    reg = register.register(gray, pads)
    result["registration"] = dict(scale_px_per_mm=reg["scale"], rot_deg=reg["rot_k"]*90,
                                   flip=reg["flip"], present_rate=reg["present_rate"])
    if reg["present_rate"] < MIN_PRESENT_RATE:
        result["warnings"].append(
            f"low registration confidence ({reg['present_rate']}): the LLM footprint "
            f"may not match this drawing, or the figure/orientation is ambiguous")

    rep, summ = verify.verify_and_refine(gray, pads, scale=reg["scale"], to_px_fn=reg["to_px"],
                                         swap_wh=(reg["rot_k"] % 2 == 1))

    # ---- discrepancies (only ACTIONABLE per-pad signals: missing copper, offset) ----
    disc = []
    for r in rep:
        ref = r["ref"]
        if not r["present"]:
            disc.append({"ref": ref, "level": "error", "msg": "no copper at LLM pad location"})
            continue
        ox, oy = r["center_off_mm"]
        if ox is not None and (abs(ox) > POS_TOL or abs(oy) > POS_TOL):
            disc.append({"ref": ref, "level": "warn",
                         "msg": f"pad offset {abs(ox):.2f},{abs(oy):.2f} mm from copper centroid"})
    # size-refine is approximate (dense pads bleed across windows) -> one summary note,
    # not per-pad noise.
    if summ.get("size_mae_w_mm") and (summ["size_mae_w_mm"] > SIZE_TOL or
                                      (summ.get("size_mae_h_mm") or 0) > SIZE_TOL):
        disc.append({"ref": "*", "level": "info",
                     "msg": f"CV pad-size refine is approximate (MAE w={summ['size_mae_w_mm']} "
                            f"h={summ['size_mae_h_mm']} mm); trust LLM nominal sizes"})

    result.update(ok=True, stage="done", verification=summ,
                  discrepancies=disc, n_pads=len(pads), dpi=dpi)

    # ---- grounded component/2 (LLM annotated with CV evidence) ----
    grounded = json.loads(json.dumps(component))            # deep copy
    by_ref = {r["ref"]: r for r in rep}
    for p in _pads_of(grounded):
        ref = p.get("number") or p.get("ref") or p.get("name")
        r = by_ref.get(ref)
        if r:
            p["cv"] = {"present": r["present"], "offset_mm": r["center_off_mm"],
                       "cv_w_mm": r["cv_w"], "cv_h_mm": r["cv_h"]}
    grounded.setdefault("extraction", {}).setdefault("warnings", [])
    grounded["extraction"]["warnings"] += (
        [f"CV grounding: {summ['copper_present']}/{summ['n']} pads on copper "
         f"(rate {summ['presence_rate']}); registration scale {reg['scale']}px/mm "
         f"rot {reg['rot_k']*90}deg"] +
        [f"{d['level'].upper()} {d['ref']}: {d['msg']}" for d in disc])
    result["grounded_component"] = grounded
    result["report"] = rep
    return result


def write(result, out_dir, mpn="component"):
    os.makedirs(out_dir, exist_ok=True)
    paths = {}
    if result.get("grounded_component"):
        p = os.path.join(out_dir, f"{mpn}.grounded.component.json")
        json.dump(result["grounded_component"], open(p, "w"), indent=2); paths["grounded"] = p
    rpt = {k: result[k] for k in ("pdf", "ok", "stage", "figure", "registration",
                                  "verification", "discrepancies", "warnings") if k in result}
    p = os.path.join(out_dir, f"{mpn}.verify-report.json")
    json.dump(rpt, open(p, "w"), indent=2); paths["report"] = p
    return paths


if __name__ == "__main__":
    import sys
    output_dir = layout_output_dir()
    pdf = sys.argv[1] if len(sys.argv) > 1 else str(sample_datasheet())
    comp = sys.argv[2] if len(sys.argv) > 2 else str(output_dir / "USB4910.component.json")
    res = run(pdf, comp)
    if not res["ok"]:
        print(f"FAILED at {res['stage']}: {res['warnings']}"); sys.exit(1)
    f, reg, v = res["figure"], res["registration"], res["verification"]
    print(f"locate  : {f['caption']!r} p{f['page']} via_ocr={f['via_ocr']}")
    print(f"register: {reg['scale_px_per_mm']}px/mm rot={reg['rot_deg']} flip={reg['flip']} "
          f"present_rate={reg['present_rate']}")
    print(f"verify  : {v['copper_present']}/{v['n']} on copper (rate {v['presence_rate']}); "
          f"size MAE w={v['size_mae_w_mm']} h={v['size_mae_h_mm']} mm")
    print(f"discrepancies ({len(res['discrepancies'])}):")
    for d in res["discrepancies"][:10]:
        print(f"   [{d['level']}] {d['ref']}: {d['msg']}")
    paths = write(res, str(output_dir), mpn="USB4910")
    print("wrote:", ", ".join(paths.values()))

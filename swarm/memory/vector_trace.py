"""Tier-2 footprint extraction: trace a connector's land pattern from the PDF's
VECTOR primitives — for the (uncommon) datasheets that draw the recommended PCB
pads as a regular cluster of filled rectangles.

This is intentionally STRICT: it only returns pads when it finds a genuine pad
cluster (many same-size filled rects in a regular arrangement) AND a pitch value
in the page text to set the mm scale. Real connector datasheets are usually
mechanical drawings with dimension callouts rather than pad grids — for those
this returns None and the caller falls back (dimension parsing / leave-out),
rather than emitting garbage.
"""
from __future__ import annotations
import re
from collections import Counter, defaultdict


def _filled_rects(page):
    """(cx, cy, w, h) for every filled rectangle on the page (PDF points)."""
    out = []
    for d in page.get_drawings():
        if not d.get("fill"):
            continue
        for it in d["items"]:
            if it[0] == "re":
                r = it[1]
                if r.width > 0 and r.height > 0:
                    out.append((r.x0 + r.width / 2, r.y0 + r.height / 2,
                                round(r.width, 2), round(r.height, 2)))
    return out


def _pitch_mm(text: str) -> float | None:
    """Find a pad pitch in mm from the page text near a 'pitch' label."""
    u = text.upper()
    m = re.search(r"(\d\.\d{1,2})\s*(?:MM)?\s*PITCH", u) or \
        re.search(r"PITCH[:\s]*?(\d\.\d{1,2})", u)
    return float(m.group(1)) if m else None


def trace_pads(pdf_path: str, expect: int = 0) -> list[dict] | None:
    """Return [{number,x_mm,y_mm,width_mm,height_mm}] traced from a vector land
    pattern, or None if no clean, scalable pad cluster is found."""
    try:
        import fitz
    except ImportError:
        return None
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return None

    for page in doc:
        txt = page.get_text()
        u = txt.upper()
        if not any(k in u for k in ("RECOMMENDED", "PCB LAYOUT", "SOLDER PAD",
                                    "LAND PATTERN", "PAD LAYOUT")):
            continue
        rects = _filled_rects(page)
        if len(rects) < 4:
            continue
        # The pads are the largest group of identically-sized filled rectangles.
        by_size = Counter((w, h) for _, _, w, h in rects)
        (pw, ph), count = by_size.most_common(1)[0]
        if count < 4:
            continue
        pads_pt = [(cx, cy) for cx, cy, w, h in rects if (w, h) == (pw, ph)]
        if expect and not (expect * 0.6 <= count <= expect * 1.6):
            continue                              # cluster size unlike the pin count

        # Scale: the smallest centre-to-centre spacing is the pitch (in points);
        # match it to the pitch stated in the text to get points→mm.
        pitch_mm = _pitch_mm(txt)
        if not pitch_mm:
            continue
        spacings = []
        for i in range(len(pads_pt)):
            for j in range(i + 1, len(pads_pt)):
                dx = pads_pt[i][0] - pads_pt[j][0]; dy = pads_pt[i][1] - pads_pt[j][1]
                spacings.append((dx * dx + dy * dy) ** 0.5)
        pitch_pt = min(s for s in spacings if s > 1.0)
        if pitch_pt <= 0:
            continue
        scale = pitch_mm / pitch_pt               # mm per point

        # Origin at the cluster centroid; flip y (PDF y grows downward).
        ox = sum(c[0] for c in pads_pt) / len(pads_pt)
        oy = sum(c[1] for c in pads_pt) / len(pads_pt)
        pads = []
        order = sorted(pads_pt, key=lambda c: (round((c[1] - oy) * scale, 1),
                                               round((c[0] - ox) * scale, 1)))
        for n, (cx, cy) in enumerate(order, 1):
            pads.append({"number": str(n),
                         "x_mm": round((cx - ox) * scale, 3),
                         "y_mm": round(-(cy - oy) * scale, 3),
                         "width_mm": round(pw * scale, 3),
                         "height_mm": round(ph * scale, 3), "shape": "rect"})
        return pads
    return None


# ── self-test: synthesise a clean pad grid in a PDF and trace it back ─────────
if __name__ == "__main__":
    import sys, tempfile, os
    try:
        import fitz
    except ImportError:
        print("PyMuPDF required"); sys.exit(0)
    # Build a 2x6 grid of 1mm pads at 2mm pitch (≈ at 72pt/in scale) + a pitch label.
    doc = fitz.open()
    page = doc.new_page(width=400, height=400)
    PT = 20.0                                       # arbitrary points-per-mm in drawing
    pad = 1.0 * PT
    for r in range(2):
        for c in range(6):
            x = 100 + c * 2.0 * PT; y = 150 + r * 6.0 * PT
            page.draw_rect(fitz.Rect(x, y, x + pad, y + pad), fill=(0, 0, 0))
    page.insert_text((100, 320), "Recommended PCB Layout — 2.00 mm pitch")
    f = tempfile.mktemp(suffix=".pdf"); doc.save(f)
    pads = trace_pads(f, expect=12)
    print(f"traced {len(pads) if pads else 0} pads (expected 12)")
    if pads:
        xs = sorted(set(p["x_mm"] for p in pads))
        dx = round(xs[1] - xs[0], 2) if len(xs) > 1 else 0
        print(f"  recovered column pitch: {dx} mm (drawn 2.00)")
        print(f"  pad size: {pads[0]['width_mm']}x{pads[0]['height_mm']} mm (drawn 1.0)")
    os.remove(f)
    assert pads and len(pads) == 12 and abs(dx - 2.0) < 0.1
    print("vector_trace OK — clean pad grids are traced + scaled correctly")

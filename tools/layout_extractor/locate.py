#!/usr/bin/env python3
"""Auto-locate the 'Recommended PCB Layout' (land-pattern) figure in a component
datasheet -- the first step toward a GENERAL extractor that is not hard-coded to
one part.

Strategy (text + vector, no OCR needed when the PDF has a real text layer):
  1. Scan every page's text layer for a land-pattern CAPTION
     ('recommended pcb layout', 'land pattern', 'solder pattern', 'footprint'),
     preferring layout/land captions over solder-paste-stencil ones.
  2. Spatially CLUSTER the page's vector drawings (rasterise segments into a coarse
     occupancy grid in PDF-point space, dilate, connected components) so each
     distinct figure on the sheet becomes one blob.
  3. Pick the drawing cluster ADJACENT to the caption and return its bbox as the
     render clip -- replacing the hard-coded CLIP_PT / PAGE.

Returns a LayoutFigure(page, clip, caption, rotation).
"""
import re
import numpy as np
import cv2
import fitz  # PyMuPDF

# caption patterns, best-first. (pattern, score) -- higher score wins ties.
_CAPTION_PATTERNS = [
    (re.compile(r"recommended\s+pcb\s+layout"), 100),
    (re.compile(r"recommended\s+land\s+pattern"), 100),
    (re.compile(r"\bland\s+pattern\b"), 90),
    (re.compile(r"recommended\s+(?:pcb\s+)?footprint"), 85),
    (re.compile(r"\bpcb\s+layout\b"), 80),
    (re.compile(r"recommended\s+solder(?!\s+paste)(?!\s+stencil)"), 60),
    (re.compile(r"\bsolder\s+pattern\b"), 55),
]
# captions that mark the WRONG figure (paste stencil / keepout) -- demote.
_NEGATIVE = re.compile(r"paste|stencil|keep[\s-]*out|mask")


class LayoutFigure:
    def __init__(self, page, clip, caption, rotation, via_ocr=False):
        self.page = page          # 0-based page index
        self.clip = clip          # fitz.Rect in unrotated PDF points
        self.caption = caption    # matched caption text
        self.rotation = rotation  # page rotation (deg)
        self.via_ocr = via_ocr    # True if the caption came from the OCR fallback

    def __repr__(self):
        c = self.clip
        src = " via=OCR" if self.via_ocr else ""
        return (f"LayoutFigure(page={self.page}, rot={self.rotation}, "
                f"caption={self.caption!r}, clip=Rect({c.x0:.0f},{c.y0:.0f},"
                f"{c.x1:.0f},{c.y1:.0f}){src})")


def _caption_hits(words):
    """Caption hits as (score, bbox_rect, text) from a words list shaped like
    page.get_text('words'); OCR words (ocr_reader) share the first 5 fields, so the
    text-layer and OCR-fallback paths use this identical matcher. Joined in reading
    order so rotated text still matches."""
    if not words:
        return []
    hits = []
    n = len(words)
    # slide a small window (captions are <=4 words) and test the joined lowercase text
    for i in range(n):
        for w in range(1, 5):
            if i + w > n:
                break
            span = words[i:i + w]
            text = " ".join(s[4] for s in span).lower()
            for pat, score in _CAPTION_PATTERNS:
                if pat.search(text):
                    if _NEGATIVE.search(text):
                        score -= 50
                    x0 = min(s[0] for s in span); y0 = min(s[1] for s in span)
                    x1 = max(s[2] for s in span); y1 = max(s[3] for s in span)
                    hits.append((score, fitz.Rect(x0, y0, x1, y1), text))
    # de-dup overlapping hits, keep the highest score per location
    hits.sort(key=lambda h: -h[0])
    kept = []
    for h in hits:
        if not any(abs(h[1].x0 - k[1].x0) < 4 and abs(h[1].y0 - k[1].y0) < 4 for k in kept):
            kept.append(h)
    return kept


def _drawing_clusters(page, cell=2.0, dilate_pt=6.0, min_extent_pt=20.0):
    """Cluster the page's vector strokes into figures. Returns list of bbox Rects
    (unrotated PDF points), largest first."""
    pr = page.rect
    # the sheet BORDER / title-block are single very long strokes that would bridge
    # every figure into one blob -- drop any segment longer than this before clustering.
    maxseg = 0.33 * max(pr.width, pr.height)
    W = max(1, int(pr.width / cell)); H = max(1, int(pr.height / cell))
    occ = np.zeros((H, W), np.uint8)
    def gx(x): return int(np.clip((x - pr.x0) / cell, 0, W - 1))
    def gy(y): return int(np.clip((y - pr.y0) / cell, 0, H - 1))
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                if abs(it[1].x - it[2].x) > maxseg or abs(it[1].y - it[2].y) > maxseg:
                    continue                            # frame / long divider line
                cv2.line(occ, (gx(it[1].x), gy(it[1].y)), (gx(it[2].x), gy(it[2].y)), 255, 1)
            elif it[0] == "re":
                r = it[1]
                if r.width > maxseg or r.height > maxseg:
                    continue
                cv2.rectangle(occ, (gx(r.x0), gy(r.y0)), (gx(r.x1), gy(r.y1)), 255, 1)
            elif it[0] in ("c", "qu"):                  # bezier/quad -> mark control pts
                for p in it[1:]:
                    if isinstance(p, fitz.Point):
                        occ[gy(p.y), gx(p.x)] = 255
    k = max(1, int(dilate_pt / cell)) | 1
    occ = cv2.dilate(occ, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(occ)
    boxes = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        bx0 = pr.x0 + x * cell; by0 = pr.y0 + y * cell
        bx1 = bx0 + w * cell;   by1 = by0 + h * cell
        if max(w, h) * cell < min_extent_pt:
            continue
        boxes.append((w * h, fitz.Rect(bx0, by0, bx1, by1)))
    boxes.sort(key=lambda b: -b[0])
    return [b[1] for b in boxes]


def _raster_clusters(page, dpi=150, dilate_pt=6.0, min_extent_pt=20.0):
    """Cluster figures from the rendered RASTER -- the fallback for pure scans /
    outlined-text pages where get_drawings() is empty. Renders with rotation removed
    so the returned bboxes are in the SAME unrotated PDF-point space as the vector
    clusters (interchangeable). The sheet border / long dividers are removed with
    wide 1-D openings (the raster analogue of dropping long vector segments)."""
    rot = page.rotation
    try:
        page.set_rotation(0)
        pr = page.rect
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72))
        a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
        gray = a[:, :, 0] if pix.n == 1 else cv2.cvtColor(
            np.ascontiguousarray(a[:, :, :3]), cv2.COLOR_RGB2GRAY)
    finally:
        page.set_rotation(rot)
    px2pt = 72.0 / dpi
    Hpx, Wpx = gray.shape
    ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    # strip frame / long rules: pixels that survive a wide horizontal OR vertical open
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(1, int(0.33*Wpx)), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(1, int(0.33*Hpx))))
    longs = cv2.bitwise_or(cv2.morphologyEx(ink, cv2.MORPH_OPEN, hk),
                           cv2.morphologyEx(ink, cv2.MORPH_OPEN, vk))
    ink = cv2.subtract(ink, longs)
    k = max(1, int(dilate_pt / px2pt)) | 1
    ink = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(ink)
    boxes = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if max(w, h) * px2pt < min_extent_pt or area < (min_extent_pt/px2pt) * 4:
            continue
        boxes.append((w * h, fitz.Rect(pr.x0 + x*px2pt, pr.y0 + y*px2pt,
                                       pr.x0 + (x+w)*px2pt, pr.y0 + (y+h)*px2pt)))
    boxes.sort(key=lambda b: -b[0])
    return [b[1] for b in boxes]


def _clusters(page, vector_min=40):
    """Figure clusters: prefer precise VECTOR clusters when the page has real vector
    content, else fall back to RASTER clustering (scans / outlined text)."""
    if len(page.get_drawings()) >= vector_min:
        cl = _drawing_clusters(page)
        if cl:
            return cl
    return _raster_clusters(page)


def _gap(a, b):
    """Edge-to-edge gap between two rects (0 if they overlap)."""
    dx = max(a.x0 - b.x1, b.x0 - a.x1, 0)
    dy = max(a.y0 - b.y1, b.y0 - a.y1, 0)
    return (dx * dx + dy * dy) ** 0.5


def locate_layout(pdf_path, pad_pt=6.0, ocr_backend=None, force_ocr=False):
    """Find the land-pattern figure. Returns a LayoutFigure or None.

    Deterministic text-layer path first; if a page yields NO caption and an
    `ocr_backend` (ocr_reader.OcrBackend) is given, retry that page from OCR'd
    words -- the fallback for scanned / outlined-text datasheets. `force_ocr`
    ignores the text layer entirely (used to verify the OCR path against ground
    truth)."""
    doc = fitz.open(pdf_path)
    best = None        # (caption_score, -gap, LayoutFigure)
    for pno in range(len(doc)):
        page = doc[pno]
        via_ocr = False
        if force_ocr:
            words = ocr_backend.read_page(page) if ocr_backend else []
            via_ocr = True
        else:
            words = page.get_text("words")
        caps = _caption_hits(words)
        if not caps and ocr_backend and not force_ocr:
            words = ocr_backend.read_page(page)        # text-layer missed -> OCR
            caps = _caption_hits(words)
            via_ocr = True
        if not caps:
            continue
        clusters = _clusters(page)          # vector if present, else raster (scans)
        if not clusters:
            continue
        for score, cap_box, text in caps:
            # the figure is the drawing cluster nearest the caption that the caption
            # does not itself dominate (caption sits just outside the figure).
            ranked = sorted(clusters, key=lambda c: (_gap(c, cap_box), -c.get_area()))
            fig = ranked[0]
            clip = fig + (-pad_pt, -pad_pt, pad_pt, pad_pt)
            clip &= page.rect
            key = (score, -_gap(fig, cap_box))
            cand = (key, LayoutFigure(pno, clip, text, page.rotation, via_ocr))
            if best is None or cand[0] > best[0]:
                best = cand
    return best[1] if best else None


if __name__ == "__main__":
    import sys
    from runtime_paths import sample_datasheet
    pdf = sys.argv[1] if len(sys.argv) > 1 else str(sample_datasheet())
    fig = locate_layout(pdf)
    print(fig if fig else "no land-pattern figure found")
    if fig:
        print("  hard-coded reference (USB4910): Rect(180,204,360,351)")

#!/usr/bin/env python3
"""BATCH ELEMENT DETECTOR — LocateAnything-3B over a FOLDER of datasheet PDFs.

Drop a bunch of PDFs in one folder. This walks them ONE AT A TIME, renders each
page, and asks LocateAnything-3B to localize every drawing PRIMITIVE — rectangles,
squares, decimal numbers, arrow / dimension lines, shaded (hatched) boxes, and
boxes filled with a pattern. It then finds the REGION where those elements are
DENSE (that's the land-pattern drawing), snapshots it, and saves:

  out/<pdf>/page<N>_annotated.png   full page, every detection drawn + labelled
  out/<pdf>/page<N>_dense.png       cropped snapshot of the densest region
  out/<pdf>/page<N>.json            all detections {label,box,score-less} + dense box
  out/<pdf>/SUMMARY.json            per-page element counts + which page is densest

The densest page's `dense.png` is exactly the crop you feed to stage 2 (Qwen2.5-VL).

LocateAnything-3B == Qwen2.5-3B + MoonViT. Output coords are NORMALIZED [0,1000]:
pixel = coord/1000 * dim. fp16 (T4 is Turing; bf16 is emulated).

RUN — on a Colab T4:
  !pip install -U transformers accelerate pillow pymupdf
  # put your PDFs in /content/datasheets (or pass a folder), then:
  !python batch_detect_colab.py                       # default /content/datasheets -> /content/out
  !python batch_detect_colab.py /content/sheets /content/out
  !python batch_detect_colab.py /content/sheets /content/out --classes rect,square,number,arrow,shaded,pattern
  # zip the results to download:  !zip -r out.zip out  &&  echo done
"""
import sys, os, io, json, re, glob
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
try:
    import fitz                               # PyMuPDF (classic import name)
except ModuleNotFoundError:
    import pymupdf as fitz                    # newer PyMuPDF prefers this name
from PIL import Image, ImageDraw, ImageFont
import torch

LOC_ID = "nvidia/LocateAnything-3B"
RENDER_DPI = 200
DETECT_TPL = "Locate all the instances that matches the following description: {desc}."

# Each element class: a localization phrase + an annotation colour. Trim with --classes.
ELEMENTS = {
    "rect":    ("rectangular copper pads (long rectangle lands)",            (220, 40, 40)),
    "square":  ("square pads or square mounting pads",                       (240, 140, 0)),
    "number":  ("decimal dimension numbers like 0.50, 1.60, 4.55",          (30, 90, 220)),
    "arrow":   ("dimension lines with arrowheads at both ends",              (20, 160, 60)),
    "shaded":  ("diagonally shaded or hatched copper regions",               (150, 40, 200)),
    "pattern": ("boxes or areas filled with a repeating pattern or texture", (140, 80, 30)),
}
PAD_FRAC = 0.05          # pad the dense crop by 5% of page size
MODEL_MAX_SIDE = 1280    # cap the long side fed to MoonViT (attention is O(patches²);
                         # a full 200-DPI page OOMs a T4). Lower to 1024 if still OOM.
GRID = 24               # density heatmap is GRID x GRID cells
DENSE_FRAC = 0.30        # (legacy) kept for reference; dense_region now flood-fills
MIN_STROKES = 40         # skip pages with fewer vector strokes AND no images (pure-text
                         # spec pages). Lower for densely-drawn sheets; 0 disables.


def _install_dtype_fix():
    """T4 is Turing — we load fp16, but the MoonViT vision tower can emit fp32 features
    while the Qwen2.5 LM embeds are fp16 -> `masked_scatter_: Half vs Float`. Globally
    coerce the scatter source to the target dtype (same fix as stage 1's DeepSeek-OCR)."""
    _orig = torch.Tensor.masked_scatter_
    def _patched(self, mask, source):
        if hasattr(source, "dtype") and source.dtype != self.dtype:
            source = source.to(self.dtype)
        return _orig(self, mask, source)
    torch.Tensor.masked_scatter_ = _patched


def load_locator():
    from transformers import AutoModel, AutoProcessor
    _install_dtype_fix()
    try:
        proc = AutoProcessor.from_pretrained(LOC_ID, trust_remote_code=True)
    except Exception as e:
        # The processor's remote code imports decord + lmdb; without them AutoProcessor
        # fails. A tokenizer-only fallback has no py_apply_chat_template (detect() needs
        # it), so fail loudly with the fix instead of returning a broken processor.
        raise RuntimeError(
            f"AutoProcessor failed ({type(e).__name__}: {e}).\n"
            "LocateAnything's processor needs decord + lmdb and transformers==4.57.1.\n"
            "  pip install transformers==4.57.1 decord==0.6.0 lmdb==1.7.5\n"
            "Then restart the runtime and re-run.") from e
    model = AutoModel.from_pretrained(
        LOC_ID, trust_remote_code=True, torch_dtype=torch.float16,
        device_map="cuda").eval()
    return model, proc


def _iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    ua = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _dedupe(boxes, iou_thr=0.6):
    """The model often repeats the same box dozens of times (a generation loop) and
    emits near-duplicates. Greedy NMS by area collapses both."""
    kept = []
    for b in sorted(boxes, key=lambda x: (x[2]-x[0])*(x[3]-x[1]), reverse=True):
        if all(_iou(b, k) < iou_thr for k in kept):
            kept.append(b)
    return kept


def _downscale(image, max_side):
    """Cap the long side fed to MoonViT. Its attention is O(patches²); a full 200-DPI
    page (2339×1654) OOMs a 16 GB T4 (~24 GB alloc). Coords are normalized [0,1000],
    so we lose NO output precision — we still denormalize against the ORIGINAL page."""
    W, H = image.size
    s = max_side / max(W, H)
    if s >= 1.0:
        return image
    return image.resize((max(1, int(W * s)), max(1, int(H * s))), Image.LANCZOS)


def detect(model, proc, image, desc):
    """Return pixel boxes [(x1,y1,x2,y2), …] matching one element description, in the
    ORIGINAL image's pixel space."""
    q = DETECT_TPL.format(desc=desc)
    W, H = image.size                                   # original dims -> output coords
    model_img = _downscale(image, MODEL_MAX_SIDE)       # smaller image -> fits the T4
    messages = [{"role": "user", "content": [
        {"type": "image", "image": model_img}, {"type": "text", "text": q}]}]
    text = proc.py_apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = proc(text=[text], images=[model_img], return_tensors="pt")
    pv = inputs["pixel_values"].to(device="cuda", dtype=model.dtype)
    ids = inputs["input_ids"].to("cuda")
    # image_grid_hws is a NUMPY array (no .to()) — pass it through as-is; generate()
    # does torch.from_numpy() internally. attention_mask is a tensor.
    grid = inputs.get("image_grid_hws", None)
    attn = inputs.get("attention_mask", None)
    if hasattr(attn, "to"):
        attn = attn.to("cuda")
    tok = getattr(proc, "tokenizer", None)              # generate reads model_max_length
    with torch.no_grad():
        out = model.generate(pixel_values=pv, input_ids=ids, attention_mask=attn,
                             image_grid_hws=grid, max_new_tokens=2048,
                             generation_mode="hybrid", use_cache=True, tokenizer=tok)
    # generate() ALREADY returns the decoded string (response[0]); don't batch_decode it.
    ans = out[0] if isinstance(out, (list, tuple)) else out
    ans = ans if isinstance(ans, str) else str(ans)
    if os.environ.get("LA_DEBUG"):                       # see the raw token format
        print(f"[raw:{desc[:24]}] {ans[:400]!r}", flush=True)
    page_area = W * H
    boxes = []
    for m in re.finditer(r"<box><(\d+)><(\d+)><(\d+)><(\d+)></box>", ans):
        xa, ya, xb, yb = (int(g) for g in m.groups())
        x1, x2 = sorted((xa, xb)); y1, y2 = sorted((ya, yb))   # model swaps corners
        b = (x1 / 1000 * W, y1 / 1000 * H, x2 / 1000 * W, y2 / 1000 * H)
        w, h = b[2] - b[0], b[3] - b[1]
        if w <= 1 or h <= 1:                         # zero-area parse junk
            continue
        if w * h > 0.70 * page_area:                 # page-sized box: not one element
            continue
        boxes.append(b)
    return _dedupe(boxes)                            # collapse the repetition loop


def dense_region(dets, W, H):
    """Heatmap element centres, find the density PEAK, then FLOOD-FILL outward through
    every adjacent non-empty cell. This captures the whole CONTIGUOUS drawing — the
    tightly-packed pads AND the sparser dimension callouts around them — and stops at
    the empty gutter that separates it from other figures. (Thresholding on density
    alone kept only the pad core and cut off the dimensions Stage 2 needs to read.)"""
    if not dets:
        return None
    grid = [[0] * GRID for _ in range(GRID)]
    for d in dets:
        cx = (d["box"][0] + d["box"][2]) / 2
        cy = (d["box"][1] + d["box"][3]) / 2
        gx = min(GRID - 1, int(cx / W * GRID))
        gy = min(GRID - 1, int(cy / H * GRID))
        grid[gy][gx] += 1
    peak = max(c for row in grid for c in row)
    if peak == 0:
        return None
    pgy, pgx = max(((gy, gx) for gy in range(GRID) for gx in range(GRID)),
                   key=lambda p: grid[p[0]][p[1]])          # seed at the peak cell
    seen = {(pgy, pgx)}; stack = [(pgy, pgx)]; cells = [(pgy, pgx)]
    while stack:                                            # 8-connected flood
        gy, gx = stack.pop()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                ny, nx = gy + dy, gx + dx
                if (0 <= ny < GRID and 0 <= nx < GRID and (ny, nx) not in seen
                        and grid[ny][nx] >= 1):
                    seen.add((ny, nx)); stack.append((ny, nx)); cells.append((ny, nx))
    ys = [c[0] for c in cells]; xs = [c[1] for c in cells]
    cw, ch = W / GRID, H / GRID
    x1 = min(xs) * cw - PAD_FRAC * W; y1 = min(ys) * ch - PAD_FRAC * H
    x2 = (max(xs) + 1) * cw + PAD_FRAC * W; y2 = (max(ys) + 1) * ch + PAD_FRAC * H
    return (max(0, int(x1)), max(0, int(y1)), min(W, int(x2)), min(H, int(y2)))


def annotate(image, dets, dense):
    im = image.convert("RGB").copy()
    dr = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
    for d in dets:
        c = d["color"]; b = d["box"]
        dr.rectangle(b, outline=c, width=2)
        dr.text((b[0] + 2, max(0, b[1] - 14)), d["label"], fill=c, font=font)
    if dense:
        dr.rectangle(dense, outline=(0, 0, 0), width=4)
        dr.text((dense[0] + 4, dense[1] + 2), "DENSE REGION", fill=(0, 0, 0), font=font)
    return im


def process_pdf(model, proc, pdf, outroot, classes):
    name = os.path.splitext(os.path.basename(pdf))[0]
    outdir = os.path.join(outroot, name)
    os.makedirs(outdir, exist_ok=True)
    doc = fitz.open(pdf)
    summary = {"pdf": os.path.basename(pdf), "pages": []}
    best = {"page": None, "count": -1}
    for pno in range(len(doc)):
        page = doc[pno]
        # Pre-filter: skip pages with almost no vector drawing AND no images (pure-text
        # spec pages) — avoids 6 model calls per page on a 72-page datasheet. Keep any
        # page with images so raster/scanned land patterns are never skipped.
        try:
            n_strokes = len(page.get_drawings())
        except Exception:
            n_strokes = MIN_STROKES                      # if unsure, don't skip
        if n_strokes < MIN_STROKES and not page.get_images():
            print(f"  {name} p{pno+1}/{len(doc)}: skipped (text page, {n_strokes} strokes)",
                  flush=True)
            continue
        pix = page.get_pixmap(matrix=fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72))
        mode = "RGB" if pix.n < 4 else "RGBA"
        img = Image.frombytes(mode, (pix.width, pix.height), pix.samples).convert("RGB")
        W, H = img.size
        dets, counts = [], {}
        for cls in classes:
            desc, color = ELEMENTS[cls]
            for b in detect(model, proc, img, desc):
                dets.append({"label": cls, "box": [round(v, 1) for v in b], "color": color})
            counts[cls] = sum(1 for d in dets if d["label"] == cls)
        dense = dense_region(dets, W, H)
        annotate(img, dets, dense).save(os.path.join(outdir, f"page{pno+1}_annotated.png"))
        if dense:
            img.crop(dense).save(os.path.join(outdir, f"page{pno+1}_dense.png"))
        json.dump({"page": pno + 1, "size": [W, H], "counts": counts,
                   "dense_box": dense, "detections": dets},
                  open(os.path.join(outdir, f"page{pno+1}.json"), "w"), indent=2)
        total = sum(counts.values())
        summary["pages"].append({"page": pno + 1, "counts": counts, "total": total,
                                 "dense_box": dense})
        if total > best["count"]:
            best = {"page": pno + 1, "count": total}
        print(f"  {name} p{pno+1}/{len(doc)}: {counts}  total={total}"
              + (f"  dense={dense}" if dense else "  (no dense region)"), flush=True)
    summary["densest_page"] = best["page"]
    json.dump(summary, open(os.path.join(outdir, "SUMMARY.json"), "w"), indent=2)
    print(f"[done] {name}: densest page = {best['page']} -> "
          f"{outdir}/page{best['page']}_dense.png", flush=True)


def selftest(model, proc, pdfs, classes):
    """One-command end-to-end check on the FIRST page of the FIRST PDF: render ->
    detect each class -> print raw box counts. Proves load+processor+generate+parse
    work on REAL data before committing to the full batch."""
    doc = fitz.open(pdfs[0])
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72))
    mode = "RGB" if pix.n < 4 else "RGBA"
    img = Image.frombytes(mode, (pix.width, pix.height), pix.samples).convert("RGB")
    print(f"[selftest] {os.path.basename(pdfs[0])} page 1 @ {img.size}", flush=True)
    for cls in classes:
        desc, _ = ELEMENTS[cls]
        boxes = detect(model, proc, img, desc)
        print(f"  {cls:8s}: {len(boxes)} box(es)"
              + (f"  e.g. {tuple(round(v) for v in boxes[0])}" if boxes else ""), flush=True)
    print("[selftest] OK — generate + box parsing work. Re-run WITHOUT --selftest "
          "for the full batch.", flush=True)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    indir = args[0] if len(args) > 0 else "/content/datasheets"
    outroot = args[1] if len(args) > 1 else "/content/out"
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    is_selftest = "--selftest" in flags
    classes = list(ELEMENTS.keys())
    for a in flags:
        if a.startswith("--classes"):
            sel = a.split("=", 1)[1] if "=" in a else ""
            classes = [c for c in sel.split(",") if c in ELEMENTS] or classes
    pdfs = sorted(glob.glob(os.path.join(indir, "*.pdf")) + glob.glob(os.path.join(indir, "*.PDF")))
    if not pdfs:
        print(f"no PDFs in {indir} — upload some (Files panel) and re-run"); return
    os.makedirs(outroot, exist_ok=True)
    print(f"loading LocateAnything-3B (fp16)… detecting classes: {classes}", flush=True)
    model, proc = load_locator()
    if is_selftest:
        selftest(model, proc, pdfs, classes); return
    print(f"{len(pdfs)} PDF(s) -> {outroot}", flush=True)
    for i, pdf in enumerate(pdfs):                      # ONE pdf at a time
        print(f"\n=== [{i+1}/{len(pdfs)}] {os.path.basename(pdf)} ===", flush=True)
        try:
            process_pdf(model, proc, pdf, outroot, classes)
        except Exception as e:
            print(f"[skip] {os.path.basename(pdf)}: {type(e).__name__}: {e}", flush=True)
    print(f"\nALL DONE -> {outroot}  (zip it: !zip -r out.zip {outroot})")


if __name__ == "__main__":
    main()

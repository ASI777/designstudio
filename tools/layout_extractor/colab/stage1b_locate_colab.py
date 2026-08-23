#!/usr/bin/env python3
"""STAGE 1b — LocateAnything-3B crops the land-pattern figure → cropped bundle.

Sits BETWEEN stage 1 (DeepSeek-OCR) and stage 2 (Qwen2.5-VL). It picks the
land-pattern page, asks LocateAnything-3B to localize the recommended-land-pattern
DRAWING on that page, crops a tight (padded) box around it, and writes a new bundle
that stage 2 consumes UNCHANGED.

Why: a tight crop of just the drawing (instead of the whole downscaled page) gives
Qwen far more pixels-per-dimension-number at the same visual-token budget — it reads
the arrows better and stops OOM-clipping. It also replaces the brittle deterministic
locate.py (6/21 hit rate) with an open-vocabulary grounding model.

LocateAnything-3B == Qwen2.5-3B + MoonViT, visual-grounding. Output coords are
NORMALIZED [0,1000]: pixel = coord/1000 * image_dim. Runs in the SAME runtime as
stage 2 (both Qwen2.5-era transformers) — NOT stage 1's pinned 4.46.3.

RUN — on a Colab T4 (same session as stage 2):
  !pip install -U transformers accelerate pillow
  # upload ocr_bundle.json from stage 1, then:
  !python stage1b_locate_colab.py ocr_bundle.json              # auto-pick the page
  !python stage1b_locate_colab.py ocr_bundle.json 2            # OR force page 2 (1-indexed)
  # -> writes ocr_bundle_cropped.json  AND  cropped_figure.png (eyeball the crop!)
  # then:
  !python stage2_vlm_colab.py ocr_bundle_cropped.json
"""
import sys, os, io, json, re, base64
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
from PIL import Image
import torch

LOC_ID = "nvidia/LocateAnything-3B"
# Localize the DRAWING, not text. Phrasing follows the model's detection template.
LOCATE_QUERY = ("Locate all the instances that matches the following description: "
                "the recommended PCB land pattern / solder pad layout drawing showing "
                "copper pads with dimension arrows.")
PAD_FRAC = 0.06          # pad the crop by 6% of page size on every side (keep callouts)

# Reuse stage 2's page picker so both stages agree on WHICH page is the drawing.
_LANDPAT = re.compile(r"land\s*pattern|recommended|solder\s*pad|pad\s*layout|footprint|outline", re.I)
_DIMPAT  = re.compile(r"\d+\s*[x×]\s*\d|R\s?\d\.\d|Ø", re.I)


def pick_page(pages):
    best_i, best = 0, -1
    for i, p in enumerate(pages):
        t = p.get("ocr_markdown", "")
        score = (len(_LANDPAT.findall(t)) * 40 + len(_DIMPAT.findall(t)) * 8
                 + len(re.findall(r"\b\d\.\d{1,2}\b", t)))
        if score > best:
            best, best_i = score, i
    return best_i


def _install_dtype_fix():
    """fp16 load on Turing: coerce masked_scatter_ source to target dtype if MoonViT
    emits fp32 while the LM is fp16 (same fix as stage 1)."""
    _orig = torch.Tensor.masked_scatter_
    def _patched(self, mask, source):
        if hasattr(source, "dtype") and source.dtype != self.dtype:
            source = source.to(self.dtype)
        return _orig(self, mask, source)
    torch.Tensor.masked_scatter_ = _patched


def load_locator():
    from transformers import AutoModel, AutoProcessor
    _install_dtype_fix()
    proc = AutoProcessor.from_pretrained(LOC_ID, trust_remote_code=True)
    # fp16 (T4 is Turing — bf16 is emulated). 3B in fp16 ~6 GB, fits alongside nothing
    # else loaded yet; run this BEFORE loading Qwen-7B, or free it after.
    model = AutoModel.from_pretrained(
        LOC_ID, trust_remote_code=True, torch_dtype=torch.float16,
        device_map="cuda").eval()
    return model, proc


def _downscale(image, max_side=1280):
    """Cap the long side fed to MoonViT (attention is O(patches²); a full 200-DPI page
    OOMs a T4). Coords are normalized [0,1000] so we denormalize against the ORIGINAL."""
    W, H = image.size
    s = max_side / max(W, H)
    return image if s >= 1.0 else image.resize(
        (max(1, int(W * s)), max(1, int(H * s))), Image.LANCZOS)


def locate_boxes(model, proc, image, query):
    """Return list of pixel boxes [(x1,y1,x2,y2), …] for `query`, in ORIGINAL pixels."""
    W, H = image.size                                   # original dims -> output coords
    model_img = _downscale(image)
    messages = [{"role": "user", "content": [
        {"type": "image", "image": model_img}, {"type": "text", "text": query}]}]
    text = proc.py_apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = proc(text=[text], images=[model_img], return_tensors="pt")
    pv = inputs["pixel_values"].to(device="cuda", dtype=model.dtype)
    ids = inputs["input_ids"].to("cuda")
    # image_grid_hws is a NUMPY array (no .to()) — pass through as-is; generate() does
    # torch.from_numpy() internally. attention_mask is a tensor.
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
    boxes = []
    for m in re.finditer(r"<box><(\d+)><(\d+)><(\d+)><(\d+)></box>", ans):
        x1, y1, x2, y2 = (int(g) for g in m.groups())
        boxes.append((x1 / 1000 * W, y1 / 1000 * H, x2 / 1000 * W, y2 / 1000 * H))
    return boxes, ans


def crop_figure(image, boxes):
    """Pick the largest grounded box (the drawing dominates the figure region), pad it,
    and return the crop. Falls back to the full page if nothing was localized."""
    W, H = image.size
    if not boxes:
        print("[locate] no box returned — keeping FULL page", flush=True)
        return image, None
    x1, y1, x2, y2 = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    px, py = PAD_FRAC * W, PAD_FRAC * H
    box = (max(0, int(x1 - px)), max(0, int(y1 - py)),
           min(W, int(x2 + px)), min(H, int(y2 + py)))
    print(f"[locate] {len(boxes)} box(es); cropping {box} of {(W, H)}", flush=True)
    return image.crop(box), box


def main():
    bundle_path = sys.argv[1] if len(sys.argv) > 1 else "ocr_bundle.json"
    bundle = json.load(open(bundle_path))
    pages = bundle["pages"]
    if len(sys.argv) > 2:
        i = max(0, min(len(pages) - 1, int(sys.argv[2]) - 1))
        print(f"[page] forced to page {i+1} of {len(pages)}", flush=True)
    else:
        i = pick_page(pages)
        print(f"[page] auto-picked page {pages[i]['page']} of {len(pages)}", flush=True)

    img = Image.open(io.BytesIO(base64.b64decode(pages[i]["image_b64"]))).convert("RGB")
    print("loading LocateAnything-3B (fp16)…", flush=True)
    model, proc = load_locator()
    boxes, ans = locate_boxes(model, proc, img, LOCATE_QUERY)
    print(f"[locate] raw model output: {ans[:200]!r}", flush=True)
    crop, box = crop_figure(img, boxes)
    crop.save("cropped_figure.png")
    print("wrote cropped_figure.png — open it and confirm it's the land pattern", flush=True)

    # Re-encode the crop into a ONE-PAGE bundle stage 2 reads with no changes.
    buf = io.BytesIO(); crop.save(buf, format="PNG")
    out_page = dict(pages[i])
    out_page["image_b64"] = base64.b64encode(buf.getvalue()).decode()
    out_page["crop_box"] = box
    out = {"source_pdf": bundle.get("source_pdf", "?"),
           "render_dpi": bundle.get("render_dpi"),
           "located_by": LOC_ID, "pages": [out_page]}
    json.dump(out, open("ocr_bundle_cropped.json", "w"))
    print("wrote ocr_bundle_cropped.json — now run: "
          "!python stage2_vlm_colab.py ocr_bundle_cropped.json")


if __name__ == "__main__":
    main()

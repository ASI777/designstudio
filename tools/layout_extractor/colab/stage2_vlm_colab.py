#!/usr/bin/env python3
"""STAGE 2 — Qwen2.5-VL-3B reads STAGE 1's ocr_bundle.json → component/2 footprint.

Runs on its OWN Colab runtime with a NEW transformers (Qwen2.5-VL needs >=4.49),
separate from STAGE 1's pinned 4.46.3. Picks the land-pattern page, gives Qwen BOTH
the page image (so it SEES the drawing) and the DeepSeek-OCR text, and emits a
design-studio.component/2 JSON the desktop app validates.

RUN — on a Colab T4:
  !pip install -q "transformers>=4.49" accelerate qwen-vl-utils pillow bitsandbytes
  # upload ocr_bundle.json (Files panel), then:
  !python stage2_vlm_colab.py ocr_bundle.json          # auto-pick the land-pattern page
  !python stage2_vlm_colab.py ocr_bundle.json 2        # OR force page 2 (1-indexed)
  # -> writes component.json  (download → import into the app's user library, or POST
  #    it back via the Components-panel extract)
"""
import sys, os, io, json, re, base64
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
from PIL import Image
import torch

VLM_ID = "Qwen/Qwen2.5-VL-7B-Instruct"   # 7B in 4-bit (~6 GB) fits T4; far better at
                                         # reading dense dimension drawings than 3B

EXTRACT_PROMPT = """You are an expert at reading MECHANICAL ENGINEERING DRAWINGS of PCB
land patterns. You are given the recommended-land-pattern drawing image plus its OCR text.
Reconstruct the footprint by READING THE DIMENSION ANNOTATIONS (arrows + numbers), not by
guessing. Output ONE design-studio.component/2 JSON object and NOTHING else.

HOW TO READ THE DRAWING (all values are millimetres):
- A NUMBER attached to a line with ARROWHEADS at both ends (a dimension line, with thin
  extension lines reaching to the feature edges) is the DISTANCE between those two edges.
- "N x VALUE" (e.g. "2x 1.60", "24x0.85", "12x1.10") = N identical features/spaces, each
  VALUE. Use it for repeated pads, pad sizes, and pitch.
- "R0.45" = corner radius. "Ø0.55" / "2x Ø0.55" = hole diameter (N holes).
- Dimensions STACK from a DATUM (usually a dash-dot centreline). A chain like
  0.30 / 0.85 / 1.10 / 4.55 / 6.20 are cumulative positions measured from that datum —
  use them to place pad centres.
- Pad WIDTH/HEIGHT come from a dimension across ONE pad; PITCH from a centre-to-centre or
  "Nx pitch" dimension; POSITION from a datum-to-pad dimension. Drawings are usually
  mirror-symmetric, and "2x" marks a mirrored pair (one each side of the datum).

PROCEDURE:
1. List every distinct copper feature: signal pads (often a row/grid), mounting pads,
   and drill holes (Ø).
2. For each, read the arrows that give its width, its height, and its X and Y position
   relative to the PART CENTRE (datum). Cross-check every number against the OCR text.
3. Emit pads as centre coordinates: {number,x_mm,y_mm,width_mm,height_mm,shape}. Holes:
   shape "circle", width=height=Ø, "drill_mm":Ø, "mechanical":true. Mounting pads:
   "mechanical":true. roundrect/oval pads: add "corner_r_mm".

RULES: never invent a number — if unreadable, omit it and note in extraction.warnings.
If the page is ONLY a body outline with no pad dimensions, say so and give an IPC-7351
estimate from the body. Reconstruct ALL pads you can see, not just one.

Schema:
{
  "schema":"design-studio.component/2",
  "component":{"manufacturer":"","mpn":"","category":"","description":""},
  "symbol":{"ref_des_prefix":"U","pins":[{"number":"1","name":"","electrical_type":"passive"}]},
  "footprint":{"name":"","mount":"smd","pitch_mm":0.0,
     "body":{"length_mm":0,"width_mm":0,"height_mm":0},
     "pads":[{"number":"1","x_mm":0,"y_mm":0,"width_mm":0,"height_mm":0,"shape":"rect"}]},
  "orientation":{"pin1_marker":"none","pin1_position":""},
  "extraction":{"warnings":[]}
}
Return only the JSON object."""

_LANDPAT = re.compile(r"land\s*pattern|recommended|solder\s*pad|pad\s*layout|footprint|outline",
                      re.I)
# dimension CALLOUTS that mark a real drawing page: "2x 1.60", "24x0.85", "R0.45", "Ø0.55"
_DIMPAT = re.compile(r"\d+\s*[x×]\s*\d|R\s?\d\.\d|Ø", re.I)


def pick_page(pages):
    """Pick the DRAWING page (dense dimension callouts), not the spec page."""
    best_i, best = 0, -1
    for i, p in enumerate(pages):
        t = p.get("ocr_markdown", "")
        cap = len(_LANDPAT.findall(t))
        callouts = len(_DIMPAT.findall(t))            # Nx / R / Ø => the drawing
        dims = len(re.findall(r"\b\d\.\d{1,2}\b", t))
        score = cap * 40 + callouts * 8 + dims
        if score > best:
            best, best_i = score, i
    return best_i


def load_vlm():
    from transformers import (Qwen2_5_VLForConditionalGeneration, AutoProcessor,
                              BitsAndBytesConfig)
    # 4-bit LM (fits 7B on a T4); torch_dtype=fp16 keeps the NON-quantized vision tower
    # in fp16 so its features match the LM (avoids the fp32/fp16 scatter mismatch). fp16
    # is T4-native (bf16 isn't).
    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_compute_dtype=torch.float16)
    m = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        VLM_ID, quantization_config=bnb, torch_dtype=torch.float16,
        device_map="cuda", attn_implementation="sdpa").eval()
    # Visual-token budget: enough resolution to READ the dimension numbers, but well
    # under the ~26k-token explosion that OOM'd (attention ∝ tokens²; 2560 patches ≈
    # ~0.2 GB attention, trivial). Raise/lower max_pixels to trade legibility vs VRAM.
    proc = AutoProcessor.from_pretrained(
        VLM_ID, min_pixels=512*28*28, max_pixels=2560*28*28)
    return m, proc


def extract(model, proc, img, ocr_text):
    from qwen_vl_utils import process_vision_info
    # min/max_pixels MUST be set here (not just on the processor) — process_vision_info
    # uses its own large default otherwise and the image explodes to ~13k tokens -> OOM.
    # 1280*28*28 patches ≈ legible for dimension numbers and fits a T4 alongside the model.
    messages = [{"role": "user", "content": [
        {"type": "image", "image": img,
         "min_pixels": 256*28*28, "max_pixels": 1280*28*28},
        {"type": "text", "text": EXTRACT_PROMPT + "\n\nOCR (layout) text:\n" + ocr_text[:6000]},
    ]}]
    text = proc.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    imgs, vids = process_vision_info(messages)
    inputs = proc(text=[text], images=imgs, videos=vids, padding=True,
                  return_tensors="pt").to("cuda")
    # USB4910's full footprint (24 pads + mounts + holes) is a large JSON — 2048 tokens
    # truncates it mid-object. Give it room.
    gen = model.generate(**inputs, max_new_tokens=6144, do_sample=False)
    gen = gen[:, inputs.input_ids.shape[1]:]
    raw = proc.batch_decode(gen, skip_special_tokens=True)[0]
    try:
        open("component_raw.txt", "w").write(raw)      # never lose the model output
    except OSError:
        pass
    return _parse_json(raw)


def _parse_json(raw):
    """Lenient parse: strip code fences, take the outermost object, and repair a
    truncated tail / trailing commas (the model sometimes overruns or drops a comma)."""
    s = re.sub(r"\s*```$", "", re.sub(r"^\s*```(?:json)?\s*", "", raw.strip()))
    a = s.find("{")
    if a < 0:
        return {"schema": "design-studio.component/2",
                "error": "no JSON in model output", "raw": raw[:800]}
    s = s[a:]
    def _balance(t):
        t = t.rstrip().rstrip(",")
        t += "]" * max(0, t.count("[") - t.count("]"))
        t += "}" * max(0, t.count("{") - t.count("}"))
        return t
    for fix in (lambda x: x,
                lambda x: re.sub(r",\s*([}\]])", r"\1", x),         # trailing commas
                _balance,                                          # truncated tail
                lambda x: re.sub(r",\s*([}\]])", r"\1", _balance(x))):
        try:
            return json.loads(fix(s))
        except Exception:
            continue
    return {"schema": "design-studio.component/2",
            "error": "unparseable JSON (saved to component_raw.txt)", "raw": raw[:800]}


def main():
    bundle_path = sys.argv[1] if len(sys.argv) > 1 else "ocr_bundle.json"
    bundle = json.load(open(bundle_path))
    pages = bundle["pages"]
    if len(sys.argv) > 2:                          # explicit 1-indexed page override
        i = max(0, min(len(pages) - 1, int(sys.argv[2]) - 1))
        print(f"[page] forced to page {i+1} of {len(pages)}", flush=True)
    else:
        i = pick_page(pages)
        print(f"[page] auto-picked page {pages[i]['page']} of {len(pages)} "
              f"(append a page number to override, e.g. … ocr_bundle.json 2)", flush=True)
    ocr = pages[i].get("ocr_markdown", "")
    print(f"[ocr] page {i+1} preview: {ocr[:300]!r}", flush=True)
    img = Image.open(io.BytesIO(base64.b64decode(pages[i]["image_b64"]))).convert("RGB")
    print("loading Qwen2.5-VL-7B (4-bit)…", flush=True)
    model, proc = load_vlm()
    comp = extract(model, proc, img, ocr)
    comp.setdefault("extraction", {}).setdefault("warnings", []).append(
        f"DeepSeek-OCR (stage 1) + Qwen2.5-VL-7B (stage 2), page {pages[i]['page']} "
        f"of {bundle.get('source_pdf','?')}; verify before fabrication")
    json.dump(comp, open("component.json", "w"), indent=2)
    pads = (comp.get("footprint", {}) or {}).get("pads", [])
    print(f"wrote component.json — {len(pads)} pads"
          + (f"  ERROR: {comp['error']}" if comp.get("error") else ""))


if __name__ == "__main__":
    main()

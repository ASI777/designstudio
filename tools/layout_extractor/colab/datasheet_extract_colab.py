#!/usr/bin/env python3
"""Datasheet -> footprint extractor for Google Colab (T4 16 GB).

Two-stage pipeline the user chose:
  Stage 1  UnlimitedOcrBackend  = DeepSeek-OCR (4-bit)  -> OCR + layout text/boxes
  Stage 2  Vision-LLM           = Qwen2.5-VL-3B-Instruct -> reads the page image +
                                   OCR text and emits a design-studio.component/2 JSON
Serves a /extract endpoint over a public ngrok URL so DesignStudio's Components-panel
"Add Datasheet -> Extract Footprint" can POST a PDF and get the footprint back.

HOW TO RUN (Colab, Runtime = T4 GPU):
  ### SUPERSEDED: a SINGLE runtime CANNOT load both models — DeepSeek-OCR needs
  ### transformers<=4.47 (LlamaFlashAttention2) and Qwen2.5-VL needs >=4.49. Use the
  ### two-stage scripts instead: stage1_ocr_colab.py (DeepSeek-OCR) -> ocr_bundle.json
  ### -> stage2_vlm_colab.py (Qwen2.5-VL-3B). This file is kept only as a reference
  ### for the combined flow / Flask endpoint shape.
  # set your ngrok token (free): https://dashboard.ngrok.com
  import os; os.environ["NGROK_AUTHTOKEN"] = "<your token>"
  !python datasheet_extract_colab.py
  # -> prints "ENDPOINT: https://xxxx.ngrok-free.app" ; paste that into DesignStudio.

VRAM budget on T4 16 GB: DeepSeek-OCR 4-bit ~4-6 GB + Qwen2.5-VL-3B bf16 ~6-7 GB.
Fits with headroom; if you hit OOM set SEQUENTIAL=True to load one model at a time.

SEAMS TO VERIFY on first Colab run (version-sensitive, marked ### VERIFY):
  - DeepSeek-OCR's inference call signature (its repo ships custom code).
  - Qwen2.5-VL processor / chat-template names.
"""
import os, io, json, re, tempfile
import fitz                       # PyMuPDF
from PIL import Image
import torch

DEVICE = "cuda"
SEQUENTIAL = False                # True = load OCR, run, free, then load VLM (lowest VRAM)
OCR_ID = "deepseek-ai/DeepSeek-OCR"
VLM_ID = "Qwen/Qwen2.5-VL-3B-Instruct"   # see stage2_vlm_colab.py (runs separately)
RENDER_DPI = 200

# the JSON contract the desktop app already validates (DatasheetImport / component/2)
EXTRACT_PROMPT = """You are a meticulous PCB-datasheet footprint extractor. You are given
ONE datasheet page image (the recommended land pattern / outline drawing) plus OCR text
of that page. Output ONE JSON object and NOTHING else (no prose, no markdown fence),
schema "design-studio.component/2":

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

Rules:
- Read pad sizes, pitch, and positions from the drawing's dimension numbers IN MILLIMETRES
  (1 mil = 0.0254 mm). NEVER invent a value: if a dimension is unreadable, omit it and add a
  note to extraction.warnings. Put pad centres in mm relative to the footprint centre.
- If the datasheet gives NO recommended land pattern (only a body outline), say so in
  extraction.warnings and set footprint.pads to your best IPC-7351 estimate from the body.
- Mechanical/mounting pads: mark them "mechanical": true.
Return only the JSON object."""

_bnb = None
ocr_model = ocr_tok = vlm = vlm_proc = None


def _load_ocr():
    global ocr_model, ocr_tok, _bnb
    from transformers import AutoModel, AutoTokenizer, BitsAndBytesConfig
    _bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                              bnb_4bit_compute_dtype=torch.float16)
    ocr_tok = AutoTokenizer.from_pretrained(OCR_ID, trust_remote_code=True)
    ocr_model = AutoModel.from_pretrained(
        OCR_ID, trust_remote_code=True, quantization_config=_bnb,
        device_map=DEVICE, _attn_implementation="eager").eval()


def _load_vlm():
    global vlm, vlm_proc
    from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
    vlm = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        VLM_ID, torch_dtype=torch.bfloat16, device_map=DEVICE).eval()
    vlm_proc = AutoProcessor.from_pretrained(VLM_ID)


def _free(model):
    try:
        del model
    except Exception:
        pass
    torch.cuda.empty_cache()


def deepseek_ocr(image_path: str) -> str:
    """Stage 1 — OCR + layout markdown for one page image. ### VERIFY call signature."""
    prompt = "<image>\n<|grounding|>Convert the document to markdown."
    out = ocr_model.infer(ocr_tok, prompt=prompt, image_file=image_path,
                          base_size=1024, image_size=640, crop_mode=True,
                          save_results=False, test_compress=True)
    return out if isinstance(out, str) else str(out)


def qwen_extract(image: Image.Image, ocr_text: str) -> dict:
    """Stage 2 — page image + OCR text -> component/2 dict."""
    from qwen_vl_utils import process_vision_info
    messages = [{"role": "user", "content": [
        {"type": "image", "image": image},
        {"type": "text", "text": EXTRACT_PROMPT + "\n\nOCR (layout) text:\n" + ocr_text[:6000]},
    ]}]
    text = vlm_proc.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    imgs, vids = process_vision_info(messages)
    inputs = vlm_proc(text=[text], images=imgs, videos=vids, padding=True,
                      return_tensors="pt").to(DEVICE)
    gen = vlm.generate(**inputs, max_new_tokens=2048, do_sample=False)
    gen = gen[:, inputs.input_ids.shape[1]:]
    raw = vlm_proc.batch_decode(gen, skip_special_tokens=True)[0]
    return _parse_json(raw)


def _parse_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {"schema": "design-studio.component/2", "error": "no JSON in model output",
                "raw": raw[:500]}
    try:
        return json.loads(m.group(0))
    except Exception as e:
        return {"schema": "design-studio.component/2", "error": f"bad JSON: {e}",
                "raw": m.group(0)[:500]}


_LANDPAT = re.compile(r"land\s*pattern|recommended|solder\s*pad|pad\s*layout|footprint|outline",
                      re.I)


def _pick_page(pages_ocr):
    """Choose the land-pattern page: caption keywords, else the most dimension numbers."""
    best_i, best_score = 0, -1
    for i, txt in enumerate(pages_ocr):
        kw = len(_LANDPAT.findall(txt))
        dims = len(re.findall(r"\b\d\.\d{1,2}\b", txt))
        score = kw * 100 + dims
        if score > best_score:
            best_i, best_score = i, score
    return best_i


def run_pipeline(pdf_path: str) -> dict:
    doc = fitz.open(pdf_path)
    imgs, paths = [], []
    for pno in range(len(doc)):
        pix = doc[pno].get_pixmap(matrix=fitz.Matrix(RENDER_DPI/72, RENDER_DPI/72))
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples) if pix.n < 4 \
            else Image.frombytes("RGBA", (pix.width, pix.height), pix.samples).convert("RGB")
        p = os.path.join(tempfile.gettempdir(), f"pg_{pno}.png"); img.save(p)
        imgs.append(img); paths.append(p)

    # Stage 1: OCR every page
    if SEQUENTIAL and ocr_model is None:
        _load_ocr()
    pages_ocr = [deepseek_ocr(p) for p in paths]
    page = _pick_page(pages_ocr)
    if SEQUENTIAL:
        _free(ocr_model); _load_vlm()

    # Stage 2: VLM extracts the footprint from the chosen page
    comp = qwen_extract(imgs[page], pages_ocr[page])
    comp.setdefault("extraction", {}).setdefault("warnings", []).append(
        f"extracted by DeepSeek-OCR + Qwen2.5-VL-3B on page {page+1}; verify before fab")
    return comp


def serve(port=5000):
    from flask import Flask, request, jsonify
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify(ok=True, ocr=OCR_ID, vlm=VLM_ID)

    @app.post("/extract")
    def extract():
        if "pdf" not in request.files:
            return jsonify(error="POST a 'pdf' file"), 400
        f = request.files["pdf"]
        path = os.path.join(tempfile.gettempdir(), f.filename or "in.pdf")
        f.save(path)
        try:
            return jsonify(run_pipeline(path))
        except Exception as e:
            return jsonify(schema="design-studio.component/2", error=str(e)), 500

    if os.environ.get("NGROK_AUTHTOKEN"):
        from pyngrok import ngrok
        ngrok.set_auth_token(os.environ["NGROK_AUTHTOKEN"])
        print("ENDPOINT:", ngrok.connect(port).public_url, flush=True)
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    if not SEQUENTIAL:
        _load_ocr(); _load_vlm()
    print("models loaded; starting server…", flush=True)
    serve()

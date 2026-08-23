#!/usr/bin/env python3
"""STAGE 1 — DeepSeek-OCR (the UnlimitedOcrBackend) → ocr_bundle.json.

Runs the OCR model in ITS OWN environment (transformers 4.46.3) and writes a single
self-contained bundle: per-page OCR markdown + the page image (base64 PNG). Upload
that bundle to STAGE 2 (Qwen2.5-VL) — which runs on a different transformers version,
so the two never share an environment and the version conflict disappears.

Why separate: DeepSeek-OCR's code imports LlamaFlashAttention2 (removed in
transformers >=4.48); Qwen2.5-VL needs >=4.49. They cannot coexist in one runtime.

RUN — on a Colab T4 (or your local CUDA GPU; 4-bit ~4-6 GB fits an RTX 4050):
  !pip install -q "transformers==4.46.3" accelerate bitsandbytes pymupdf pillow \
                  addict easydict einops
  #  >>> Runtime -> Restart session  (so the pinned transformers loads) <<<
  !python stage1_ocr_colab.py your_datasheet.pdf
  # -> writes ocr_bundle.json  (download it from the Files panel for STAGE 2)
"""
import sys, os, io, json, base64, tempfile, contextlib
import fitz                          # PyMuPDF
from PIL import Image
import torch

OCR_ID = "deepseek-ai/DeepSeek-OCR"
RENDER_DPI = 200


def _install_dtype_fix():
    """DeepSeek-OCR's vision encoder emits fp32 image features while the LM embeds are
    fp16 -> `masked_scatter_: Half vs Float` on T4 (bf16 GPUs don't hit it). Globally
    coerce masked_scatter_'s source to the target dtype. Works regardless of when the
    remote modeling file is (re-)downloaded — unlike file patching, which races the
    fresh-session re-download."""
    _orig = torch.Tensor.masked_scatter_
    def _patched(self, mask, source):
        if hasattr(source, "dtype") and source.dtype != self.dtype:
            source = source.to(self.dtype)
        return _orig(self, mask, source)
    torch.Tensor.masked_scatter_ = _patched
    print("[fix] masked_scatter_ dtype coercion installed", flush=True)


def load_ocr():
    # Load UNIFORMLY in fp16 — NOT 4-bit. The model is ~6.7 GB and fits a T4 16 GB
    # in fp16; fp16 is T4-native (bf16 is unsupported on Turing). 4-bit left the
    # vision tower in fp32 while the LM ran fp16 -> masked_scatter "Half vs Float".
    from transformers import AutoModel, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(OCR_ID, trust_remote_code=True)
    model = AutoModel.from_pretrained(
        OCR_ID, trust_remote_code=True, torch_dtype=torch.float16,
        device_map="cuda", _attn_implementation="eager").eval()
    return model, tok


def ocr_page(model, tok, image_path):
    # DeepSeek-OCR's infer() RETURNS None and writes the result to output_path (and
    # prints it) — so the old `str(out)` stored the literal "None". Capture the text
    # from the saved file / return value / stdout, in that order.
    outdir = os.path.join(tempfile.gettempdir(), "ds_ocr_out")
    os.makedirs(outdir, exist_ok=True)
    for fn in os.listdir(outdir):                       # clear the previous page's result
        try:
            os.remove(os.path.join(outdir, fn))
        except OSError:
            pass
    prompt = "<image>\n<|grounding|>Convert the document to markdown."
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ret = model.infer(tok, prompt=prompt, image_file=image_path, output_path=outdir,
                          base_size=1024, image_size=640, crop_mode=True,
                          save_results=True, test_compress=False)
    text = ""
    for fn in sorted(os.listdir(outdir)):               # 1) saved markdown/text file
        if fn.lower().endswith((".mmd", ".md", ".txt")):
            text = open(os.path.join(outdir, fn), encoding="utf-8", errors="ignore").read()
            if text.strip():
                break
    if not text.strip() and isinstance(ret, str) and ret.strip():
        text = ret                                      # 2) return value
    if not text.strip():
        text = buf.getvalue()                           # 3) captured stdout
    return text.strip()


def main():
    pdf = sys.argv[1] if len(sys.argv) > 1 else "input.pdf"
    _install_dtype_fix()              # fix the fp32/fp16 vision-feature mismatch (T4)
    model, tok = load_ocr()
    doc = fitz.open(pdf)
    pages = []
    for pno in range(len(doc)):
        pix = doc[pno].get_pixmap(matrix=fitz.Matrix(RENDER_DPI/72, RENDER_DPI/72))
        mode = "RGB" if pix.n < 4 else "RGBA"
        img = Image.frombytes(mode, (pix.width, pix.height), pix.samples).convert("RGB")
        p = os.path.join(tempfile.gettempdir(), f"pg{pno}.png"); img.save(p)
        text = ocr_page(model, tok, p)
        buf = io.BytesIO(); img.save(buf, format="PNG")
        pages.append({"page": pno + 1, "ocr_markdown": text,
                      "image_b64": base64.b64encode(buf.getvalue()).decode()})
        print(f"  page {pno+1}/{len(doc)} OCR'd ({len(text)} chars)", flush=True)
    bundle = {"source_pdf": os.path.basename(pdf), "render_dpi": RENDER_DPI, "pages": pages}
    json.dump(bundle, open("ocr_bundle.json", "w"))
    mb = os.path.getsize("ocr_bundle.json") / 1e6
    print(f"wrote ocr_bundle.json ({len(pages)} pages, {mb:.1f} MB) — download for STAGE 2")


if __name__ == "__main__":
    main()

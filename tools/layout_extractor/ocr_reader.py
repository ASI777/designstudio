#!/usr/bin/env python3
"""Fallback READER for datasheets whose text layer is missing/unsearchable (scans,
or text drawn as outlined curves) -- the case behind ~70% of locator misses.

A backend returns words as `(x0, y0, x1, y1, text)` tuples in PDF-point space (the
same shape as `page.get_text("words")[:5]`), so OCR output drops straight into the
existing text pipeline (locate/calibrate/legend) with no other changes.
"""
import numpy as np
import fitz


def render_page_rgb(page, dpi=300):
    """Render a page to (H,W,3) uint8 + the px->pt factor (72/dpi)."""
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72))
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    a = np.repeat(a, 3, 2) if pix.n == 1 else a[:, :, :3]
    return np.ascontiguousarray(a), 72.0 / dpi


class OcrBackend:
    """read_page(page) -> [(x0,y0,x1,y1,text)] in PDF points."""
    def read_page(self, page):
        raise NotImplementedError


class MockOcrBackend(OcrBackend):
    """Canned words per page index -- verifies the fallback WIRING without a model."""
    def __init__(self, words_by_page):
        self.words_by_page = words_by_page

    def read_page(self, page):
        return list(self.words_by_page.get(page.number, []))


class UnlimitedOcrBackend(OcrBackend):
    """Baidu Unlimited-OCR (VLM: OCR + layout). Lazy-imports torch/transformers so
    this module loads without them. Run it in a grounded mode that returns text WITH
    boxes; `parse_grounded` is the single seam to confirm against the real model's
    output schema. Needs torch+transformers+~6 GB weights (not runnable in this
    sandbox: PEP-668 lock, py3.14, 6 GB GPU)."""
    def __init__(self, model_id="baidu/Unlimited-OCR", dpi=300, device="cuda"):
        self.model_id, self.dpi, self.device = model_id, dpi, device
        self._model = self._proc = None

    def _ensure(self):
        if self._model is None:
            import torch
            from transformers import AutoModel, AutoProcessor
            self._proc = AutoProcessor.from_pretrained(self.model_id, trust_remote_code=True)
            self._model = AutoModel.from_pretrained(
                self.model_id, trust_remote_code=True,
                torch_dtype=torch.float16).to(self.device).eval()

    @staticmethod
    def parse_grounded(raw, w_px, h_px):
        """<box>x0,y0,x1,y1</box> spans, coords normalised 0..1000 -> pixel boxes.
        ADAPT to the real model output here."""
        import re
        sx, sy = w_px/1000.0, h_px/1000.0
        out = []
        for m in re.finditer(r"<box>\s*([\d.]+),([\d.]+),([\d.]+),([\d.]+)\s*</box>\s*([^<]*)", raw):
            v = [float(m.group(i)) for i in range(1, 5)]; t = m.group(5).strip()
            if t:
                out.append((v[0]*sx, v[1]*sy, v[2]*sx, v[3]*sy, t))
        return out

    def read_page(self, page):
        self._ensure()
        import torch
        from PIL import Image
        img, px2pt = render_page_rgb(page, self.dpi)
        pil = Image.fromarray(img)
        inp = self._proc(images=pil,
                         text="OCR with layout; emit <box>x0,y0,x1,y1</box> per span.",
                         return_tensors="pt").to(self.device)
        with torch.no_grad():
            ids = self._model.generate(**inp, max_new_tokens=8192)
        raw = self._proc.batch_decode(ids, skip_special_tokens=True)[0]
        return [(x0*px2pt, y0*px2pt, x1*px2pt, y1*px2pt, t)
                for x0, y0, x1, y1, t in self.parse_grounded(raw, pil.width, pil.height)]

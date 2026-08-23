"""
Datasheet Extractor Agent
=========================
Wraps the EXTRACTOR_PROMPT.md instructions as a live agent that accepts a PDF
path and returns a validated component/2 JSON, ready for library import.

Information wired in from docs/:
  • docs/datasheet-extractor/EXTRACTOR_PROMPT.md  → system prompt (verbatim)
  • docs/datasheet-extractor/component.schema.json → runtime schema validation
  • docs/datasheet-extractor/example-ams1117-3v3.json → few-shot example

The agent uses a vision-capable Claude model (claude-opus-4-8 preferred,
claude-sonnet-4-6 for cost-constrained runs) because the PDF pages are images.

MCP tool: extract_component(pdf_path, package_variant?) → component/2 JSON
Anti-Gravity task type: [datasheet_extract]
"""
import json
import base64
import os
import sys
from pathlib import Path
from typing import Optional
import llm_backend

from schema_validator import validate_or_raise, ValidationError

# ── Paths to doc assets ───────────────────────────────────────────────────────
_DOCS = Path(__file__).parent.parent.parent / "docs" / "datasheet-extractor"
_PROMPT_PATH   = _DOCS / "EXTRACTOR_PROMPT.md"
_EXAMPLE_PATH  = _DOCS / "example-ams1117-3v3.json"

def _load_extractor_prompt() -> str:
    """Extract the PROMPT section from EXTRACTOR_PROMPT.md (between the rules)."""
    text = _PROMPT_PATH.read_text()
    # The prompt is between "## PROMPT" and "## END PROMPT"
    start = text.find("## PROMPT")
    end   = text.find("## END PROMPT")
    if start == -1 or end == -1:
        return text  # fallback: use whole file
    return text[start:end].strip()

def _load_example() -> str:
    """Load the AMS1117 worked example as pretty JSON."""
    with open(_EXAMPLE_PATH) as f:
        ex = json.load(f)
    return json.dumps(ex, indent=2)

# ── System prompt ─────────────────────────────────────────────────────────────
# The EXTRACTOR_PROMPT.md text is the system prompt, verbatim.
# We append the worked example so the model has a concrete target shape.
_SYSTEM_PROMPT = f"""{_load_extractor_prompt()}

---

## Worked example (correct output shape for AMS1117-3.3 SOT-223)

```json
{_load_example()}
```

The JSON above shows the expected level of detail. Match it exactly for the
component you are about to extract.
"""

# ── PDF → base64 image pages ──────────────────────────────────────────────────
def _pdf_to_images(pdf_path: str) -> list[dict]:
    """
    Convert PDF pages to base64 PNG images for Claude's vision input.
    Requires pdf2image + poppler. Falls back to text extraction if not available.
    """
    try:
        from pdf2image import convert_from_path
        pages = convert_from_path(pdf_path, dpi=200)
        images = []
        for page in pages:
            import io
            buf = io.BytesIO()
            page.save(buf, format="PNG")
            b64 = base64.standard_b64encode(buf.getvalue()).decode()
            images.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64}
            })
        return images
    except ImportError:
        # pdf2image not installed — return empty, agent will use text extraction
        return []

def _pdf_to_image_files(pdf_path: str) -> list[str]:
    """Convert PDF pages to PNG files on disk so the Claude CLI can Read them.
    Returns [] if pdf2image/poppler is unavailable (agent falls back to text)."""
    try:
        from pdf2image import convert_from_path
        import tempfile
        pages = convert_from_path(pdf_path, dpi=200)
        outdir = tempfile.mkdtemp(prefix="ds_pdf_")
        paths = []
        for i, page in enumerate(pages):
            p = os.path.join(outdir, f"page_{i+1:03d}.png")
            page.save(p, "PNG")
            paths.append(p)
        return paths
    except ImportError:
        return []

def _pdf_to_text(pdf_path: str) -> str:
    """Fallback: extract text from PDF using pdfminer or pymupdf."""
    try:
        import fitz  # pymupdf
        doc = fitz.open(pdf_path)
        pages = []
        for i, page in enumerate(doc):
            pages.append(f"=== PAGE {i+1} ===\n{page.get_text()}")
        return "\n\n".join(pages)
    except ImportError:
        pass
    try:
        from pdfminer.high_level import extract_text
        return extract_text(pdf_path)
    except ImportError:
        return f"[PDF text extraction unavailable — install pymupdf or pdfminer.six]\nFile: {pdf_path}"


# ── Core extraction function ──────────────────────────────────────────────────
def extract_component(
    pdf_path: str,
    package_variant: Optional[str] = None,
    model: str = "claude-opus-4-8",
    max_retries: int = 2,
) -> dict:
    """
    Extract a component/2 JSON from a datasheet PDF.

    Args:
        pdf_path: Path to the datasheet PDF.
        package_variant: Optional package to extract (e.g. "SOT-223").
                         If None, the extractor picks the first SMD package.
        model: Claude model to use.
        max_retries: Number of retry attempts if validation fails.

    Returns:
        Validated component/2 dict.

    Raises:
        ValidationError: if after retries the output still fails validation.
        RuntimeError: if the API returns no usable JSON.
    """
    # Vision input: base64 blocks for the API path, on-disk PNGs for the CLI path.
    images_b64  = _pdf_to_images(pdf_path)
    image_files = _pdf_to_image_files(pdf_path) if llm_backend.backend_name() == "claude-cli" else []

    if images_b64 or image_files:
        base_instr = (
            "Extract the component shown in the datasheet page images, following the "
            "system instructions exactly.\n"
            + (f"Package variant requested: {package_variant}\n" if package_variant else "")
            + "Output ONLY the JSON object, no prose, no markdown fences."
        )
    else:
        # Text fallback
        text = _pdf_to_text(pdf_path)
        base_instr = (
            "Extract the following datasheet text following the instructions exactly.\n"
            + (f"Package variant requested: {package_variant}\n" if package_variant else "")
            + "Output ONLY the JSON object, no prose, no markdown fences.\n\n"
            + text[:100_000]  # Claude context limit guard
        )

    last_failures: list[str] = []
    for attempt in range(max_retries + 1):
        # On retries, add the validation failures as feedback
        retry_context = ""
        if attempt > 0 and last_failures:
            retry_context = (
                "\n\nYour previous extraction failed these validation checks:\n"
                + "\n".join(f"• {f}" for f in last_failures)
                + "\n\nFix ALL of the above issues and re-emit the JSON."
            )

        raw = llm_backend.complete_vision(
            system=_SYSTEM_PROMPT,
            user=base_instr + retry_context,
            image_paths=image_files,
            b64_images=images_b64,
            model=model,
            max_tokens=8192,
        ).strip()

        # Strip markdown fences if the model emitted them despite instructions
        if raw.startswith("```"):
            raw = raw[raw.find("\n")+1:]
        if raw.endswith("```"):
            raw = raw[:raw.rfind("```")]
        raw = raw.strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            last_failures = [f"JSON parse error: {e}"]
            continue

        failures = []
        try:
            validate_or_raise(data)
        except ValidationError as ve:
            failures = ve.failures

        # Surface extraction warnings regardless
        warnings = data.get("extraction", {}).get("warnings", [])
        if warnings:
            print(f"Extraction warnings for {pdf_path}:")
            for w in warnings:
                print(f"  ⚠  {w}")

        # Surface low-confidence sections
        conf = data.get("extraction", {}).get("confidence", {})
        for section, score in conf.items():
            if isinstance(score, (int, float)) and score < 0.75:
                print(f"  Low confidence {section}: {score:.0%}")

        if not failures:
            return data

        last_failures = failures
        print(f"Attempt {attempt+1}: {len(failures)} validation failure(s) — retrying")

    raise ValidationError(last_failures)


# ── Anti-Gravity agent loop ───────────────────────────────────────────────────
def run_as_agent(antigravity_url: str):
    """
    Run as a persistent agent, claiming [datasheet_extract] tasks from the queue.
    Task context must have: {"pdf_path": "...", "package_variant": null | "SOT-223"}
    Result stored as validated component/2 JSON in task result.
    """
    import requests
    agent_id = "datasheet-extractor-1"
    print(f"DatasheetExtractorAgent starting — connecting to {antigravity_url}")

    while True:
        # Claim a task
        resp = requests.post(f"{antigravity_url}/task/claim", json={
            "agent_id": agent_id,
            "capability_filter": ["datasheet_extract"]
        })
        if resp.status_code == 204:
            import time; time.sleep(5)
            continue

        task = resp.json()
        task_id = task["task_id"]
        ctx = task.get("context_json", {})
        pdf_path = ctx.get("pdf_path", "")
        package  = ctx.get("package_variant")

        print(f"Claiming task {task_id}: extract {Path(pdf_path).name}")
        try:
            result = extract_component(pdf_path, package)
            requests.post(f"{antigravity_url}/task/complete", json={
                "task_id": task_id,
                "result_json": result,
            })
            print(f"Task {task_id} complete: {result['component']['mpn']}")
        except Exception as e:
            requests.post(f"{antigravity_url}/task/fail", json={
                "task_id": task_id, "reason": str(e)
            })
            print(f"Task {task_id} failed: {e}")


# ── MCP tool entry points (used by library_mcp.py) ───────────────────────────
def mcp_extract_component(pdf_path: str, package_variant: str = "") -> dict:
    """MCP-callable tool. Returns component/2 dict or raises."""
    return extract_component(pdf_path, package_variant or None)


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python datasheet_extractor_agent.py <datasheet.pdf> [package_variant]")
        sys.exit(1)

    pdf   = sys.argv[1]
    pkg   = sys.argv[2] if len(sys.argv) > 2 else None
    model = os.environ.get("EXTRACTOR_MODEL", "claude-opus-4-8")

    print(f"Extracting {pdf}" + (f" ({pkg})" if pkg else "") + f" with {model}…")
    try:
        result = extract_component(pdf, pkg, model=model)
        mpn = result["component"]["mpn"]
        out = f"{mpn.replace(' ','_')}.json"
        with open(out, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Saved → {out}")
    except ValidationError as e:
        print("Extraction failed validation:")
        for f in e.failures:
            print(f"  • {f}")
        sys.exit(1)

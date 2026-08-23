"""Datasheet agent: supplier API/HTTPS/local PDF → reviewed component/2 JSON.

Pipeline:
  1. Download PDF (urllib)
  2. Extract text with pdftotext (fast path) or PyMuPDF (fallback)
  3. Visually inspect page contact sheets with GPT-5.6 Sol medium
  4. Render only Sol-selected regions at original detail
  5. Extract component/2 and author typed CAD with GPT-5.6 Luna xhigh
  6. Execute deterministic footprint/B-Rep/STEP validation into an isolated preview
  7. Validate the trust boundary and return or raise DatasheetError
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

_site = Path.home() / ".local/lib/python3.14/site-packages"
if str(_site) not in sys.path:
    sys.path.insert(0, str(_site))

AGY  = str(Path.home() / ".local/bin/agy")
REPO = str(Path(__file__).parents[2])

_PROMPT_PATH = Path(__file__).parents[2] / "docs" / "datasheet-extractor" / "EXTRACTOR_PROMPT.md"
_SCHEMA_PATH = Path(__file__).parents[2] / "docs" / "datasheet-extractor" / "component.schema.json"

_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]|\r')
_SPINNER  = re.compile(r'^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏\[K]')


class DatasheetError(Exception):
    pass


def _strip_ansi(text: str) -> str:
    lines = _ANSI_RE.sub('', text).splitlines()
    return '\n'.join(l for l in lines if not _SPINNER.match(l)).strip()


def _agy_env() -> dict:
    env = os.environ.copy()
    local_bin = str(Path.home() / ".local/bin")
    if local_bin not in env.get("PATH", ""):
        env["PATH"] = local_bin + ":" + env.get("PATH", "/usr/bin:/bin")
    return env


def _call_agy(model_id: str, prompt: str, timeout: int = 300) -> str:
    agy_args = [AGY, "--model", model_id, "-p", prompt, "--print-timeout", f"{timeout}s"]
    inner = " ".join("'" + a.replace("'", "'\\''") + "'" for a in agy_args)
    result = subprocess.run(
        ["script", "-q", "-c", inner, "/dev/null"],
        capture_output=True, text=True, timeout=timeout + 15, env=_agy_env(),
    )
    return _strip_ansi(result.stdout)


def _download_pdf(url: str, dest: Path, timeout: int = 30) -> None:
    from swarm.vendors.evidence import download_pdf
    download_pdf(url, dest, timeout=timeout)


def _extract_text_pdftotext(pdf_path: Path) -> str:
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout.strip()


def _extract_text_pymupdf(pdf_path: Path, max_pages: int = 30) -> str:
    try:
        import fitz
    except ImportError:
        return ""
    doc = fitz.open(str(pdf_path))
    pages = min(len(doc), max_pages)
    return "\n\n".join(doc[i].get_text() for i in range(pages))


def _extract_text(pdf_path: Path) -> str:
    text = _extract_text_pdftotext(pdf_path)
    if len(text) > 500:
        return text
    return _extract_text_pymupdf(pdf_path)


# Pages that matter for component/2 extraction (pinout + footprint), so big
# datasheets aren't truncated before the pin table (the ESP32 0-pins problem).
_PIN_KW = ("pin configuration", "pinout", "pin description", "pin function",
           "pin assignment", "terminal function", "ball assignment", "ball map",
           "signal description", "pin name", "pin#")
_FP_KW  = ("land pattern", "recommended footprint", "recommended pcb",
           "package dimension", "mechanical data", "package outline",
           "soldering", "footprint", "thermal pad")


def _pages_pymupdf(pdf_path: Path, max_pages: int = 80) -> list[str]:
    try:
        import fitz
    except ImportError:
        return []
    doc = fitz.open(str(pdf_path))
    return [doc[i].get_text() for i in range(min(len(doc), max_pages))]


def _score_page(t: str) -> int:
    u = t.lower()
    return sum(u.count(k) for k in _PIN_KW) * 3 + sum(u.count(k) for k in _FP_KW) * 2


def _extract_relevant_text(pdf_path: Path, budget: int = 80_000) -> str:
    """Assemble the pinout/footprint-relevant pages within a token budget, instead
    of truncating the first N chars — so the pin table (often mid-datasheet) is
    never cut off. Always includes the first two pages (overview/pin description)."""
    pages = _pages_pymupdf(pdf_path)
    if not pages:
        return _extract_text(pdf_path)[:budget]          # fallback: flat truncate
    chosen = set(range(min(2, len(pages))))              # overview pages
    for i in sorted(range(len(pages)), key=lambda j: -_score_page(pages[j])):
        if sum(len(pages[j]) for j in chosen) >= budget:
            break
        chosen.add(i)
    return "\n\n".join(pages[i] for i in sorted(chosen))


def _select_relevant_page_indices(pdf_path: Path, limit: int = 6) -> list[int]:
    """Pick overview, pinout and footprint pages for multimodal extraction."""
    pages = _pages_pymupdf(pdf_path)
    if not pages:
        return []
    chosen: list[int] = list(range(min(2, len(pages))))
    ranked = sorted(range(len(pages)), key=lambda index: (-_score_page(pages[index]), index))
    for index in ranked:
        if len(chosen) >= limit:
            break
        if index not in chosen and _score_page(pages[index]) > 0:
            chosen.append(index)
    return sorted(chosen)


def _selected_region_text(pdf_path: Path, inspection: dict, budget: int = 50_000) -> str:
    """Send Luna only text from Sol-selected pages, retaining printed tables."""
    pages = _pages_pymupdf(pdf_path, max_pages=160)
    selected = sorted({int(region.get("page", 0)) - 1
                       for region in inspection.get("regions", [])})
    chunks = []
    used = 0
    for index in selected:
        if index < 0 or index >= len(pages):
            continue
        chunk = f"PDF PAGE {index + 1}\n{pages[index]}"
        remaining = budget - used
        if remaining <= 0:
            break
        chunks.append(chunk[:remaining]); used += min(len(chunk), remaining)
    return "\n\n".join(chunks)


def _load_extractor_prompt() -> str:
    if _PROMPT_PATH.exists():
        raw = _PROMPT_PATH.read_text()
        # Trim to the PROMPT section only (between the --- markers)
        m = re.search(r'---\s*\n(.+?)---', raw, re.DOTALL)
        if m:
            return m.group(1).strip()
    return (
        "You are an electronics datasheet extraction engine. "
        "Output ONE JSON object conforming to design-studio.component/2 schema, "
        "nothing else. Never invent values not in the datasheet text."
    )


def _extract_json(text: str) -> dict:
    """Pull first JSON object from model response."""
    m = re.search(r'\{[\s\S]+\}', text)
    if not m:
        raise DatasheetError(f"No JSON in model response: {text[:300]}")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise DatasheetError(f"JSON parse error: {e}") from e


def process(datasheet_url: str, mpn: str = "", timeout: int = 600,
            *, build_preview: bool = True, component_cad_executable: str | None = None,
            preview_root: str | Path | None = None) -> dict:
    """Acquire a datasheet PDF and run Codex CLI Luna/xhigh extraction.

    Returns a component/2 dict. Raises DatasheetError on failure.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "datasheet.pdf"
        from swarm.vendors.evidence import (cached_download_pdf, local_pdf_evidence,
                                            finalize_mpn_evidence)

        # A local file (user-provided datasheet) is used directly; otherwise
        # download. Accepts a plain path or a file:// URL.
        local = datasheet_url[7:] if datasheet_url.startswith("file://") else datasheet_url
        if local and os.path.exists(local):
            import shutil
            shutil.copy(local, pdf_path)
            evidence = local_pdf_evidence(pdf_path, expected_mpn=mpn)
            evidence["source"] = str(Path(local).resolve())
        else:
            try:
                evidence = cached_download_pdf(datasheet_url, pdf_path, expected_mpn=mpn)
            except Exception as e:
                raise DatasheetError(f"Download failed for {datasheet_url}: {e}") from e

        # Reject non-PDF downloads (HTML error / anti-bot pages) BEFORE wasting an
        # LLM call — vendors often serve an "Access Denied" page to scrapers.
        head = pdf_path.read_bytes()[:1024] if pdf_path.exists() else b""
        if not head.startswith(b"%PDF"):
            raise DatasheetError(
                "Downloaded content is not a PDF (likely an HTML error/anti-bot page)")

        # Extract the pinout/footprint-relevant pages (not just the first 60k
        # chars), so large datasheets don't lose the pin table before extraction.
        text = _extract_relevant_text(pdf_path)
        if len(text) < 100:
            raise DatasheetError("Could not extract meaningful text from PDF")
        evidence = finalize_mpn_evidence(evidence, text)
        if mpn and evidence["mpn_match"] != "exact":
            raise DatasheetError(
                f"Datasheet evidence does not mention exact requested MPN '{mpn}'")
        # Guard against access-denied / captcha / bot-block bodies served as PDF.
        low = text[:3000].lower()
        if any(m in low for m in (
                "access denied", "access to this page has been denied",
                "automation tools", "security restrictions", "request blocked",
                "enable javascript", "verify you are human", "captcha",
                "are you a robot", "unusual traffic")):
            raise DatasheetError("Datasheet URL returned an access-denied/anti-bot page")
        if len(text) > 90_000:                            # final safety cap
            text = text[:90_000] + "\n\n[TRUNCATED]"

        try:
            from swarm.agents.codex_datasheet_inspector import (
                inspect_datasheet, render_selected_regions)
            inspection, inspection_provenance, inspection_cache_hit = inspect_datasheet(
                pdf_path, mpn=mpn, datasheet_sha256=evidence["sha256"],
                working_directory=REPO, timeout=min(timeout, 300))
            vision_pages = render_selected_regions(pdf_path, inspection)
            luna_text = _selected_region_text(pdf_path, inspection)
        except Exception as exc:
            raise DatasheetError(f"GPT-5.6 Sol medium datasheet inspection failed: {exc}") from exc
        if not vision_pages:
            raise DatasheetError("Sol selected no datasheet regions for Luna CAD generation")
        # Retain the immutable bytes for post-model CV grounding after the
        # acquisition temporary directory closes.
        pdf_bytes = pdf_path.read_bytes()

        # Tier-2: while the PDF is still on disk, try tracing a vector land pattern
        # (for connectors that draw pads as a clean filled-rect grid). None if the
        # datasheet is a mechanical drawing instead — handled gracefully below.
        traced_pads = None
        try:
            from swarm.memory.vector_trace import trace_pads
            traced_pads = trace_pads(str(pdf_path))
        except Exception:
            traced_pads = None

    extractor_prompt = _load_extractor_prompt() + """

Use the page images to associate drawing features with their printed dimension
callouts. Never calculate millimetres from pixel distance. Prefer the
manufacturer's recommended PCB land pattern. Extract the complete symbol pin
numbering and electrical properties from the text. If a footprint dimension,
pin identity, or electrical value is ambiguous, omit it and add a warning rather
than guessing. Curved features must be represented using the closest supported
component/2 primitive and explicitly warned for later semantic-vector review.
"""
    try:
        from swarm.agents.codex_datasheet_extractor import build_prompt, call_codex
        prompt = build_prompt(
            instructions=extractor_prompt,
            datasheet_text=luna_text,
            pages=vision_pages,
            mpn=mpn,
            inspection=inspection,
        )
        component, model_provenance = call_codex(
            prompt=prompt, pages=vision_pages, schema_path=_SCHEMA_PATH,
            working_directory=REPO, timeout=timeout)
    except Exception as exc:
        raise DatasheetError(f"Codex CLI GPT-5.6 Luna xhigh extraction failed: {exc}") from exc

    # Stamp vendor info if not already present
    if mpn and not component.get("component", {}).get("mpn"):
        component.setdefault("component", {})["mpn"] = mpn
    if mpn:
        from swarm.vendors.base import exact_mpn_match
        extracted_mpn = component.get("component", {}).get("mpn", "")
        if not exact_mpn_match(mpn, extracted_mpn):
            raise DatasheetError(
                f"Extractor returned MPN '{extracted_mpn}', expected exact '{mpn}'")
    else:
        extracted_mpn = component.get("component", {}).get("mpn", "")
        evidence["expected_mpn"] = extracted_mpn
        evidence = finalize_mpn_evidence(evidence, text)
        if evidence["mpn_match"] != "exact":
            raise DatasheetError(
                f"Datasheet text does not substantiate extracted MPN '{extracted_mpn}'")
    component["evidence"] = evidence
    component["inspection"] = inspection

    extraction = component.setdefault("extraction", {})
    extraction["provider"] = model_provenance["provider"]
    extraction["model"] = model_provenance["model"]
    extraction["reasoning_effort"] = model_provenance["reasoning_effort"]
    extraction["response_id"] = model_provenance["response_id"]
    extraction["codex_version"] = model_provenance.get("codex_version", "")
    extraction["pages_used"] = [page["page"] for page in vision_pages]
    extraction["inspection"] = {
        "provider": inspection_provenance["provider"],
        "model": inspection_provenance["model"],
        "reasoning_effort": inspection_provenance["reasoning_effort"],
        "cache_hit": inspection_cache_hit,
    }

    # EXACT footprint: parse the mechanical-dimensions table (pitch/body/pad/
    # thermal) from the datasheet text and regenerate the land pattern from those
    # real numbers — the LLM-read pad coordinates are unreliable (the RTL8153
    # 24-vs-48-pad failure). Structure from the package, scale from the table.
    try:
        from swarm.memory.footprint_extractor import parse_package_dims
        from swarm.memory.ic_footprints import regenerate_footprint
        dims = parse_package_dims(text)
        applied = False
        if dims:
            component.setdefault("footprint", {})["dimensions"] = dims
            fp = regenerate_footprint(component)
            if fp and fp.get("pads"):
                component["footprint"] = fp
                applied = True
        # Tier-2 fallback for connectors: a traced vector land pattern, only if the
        # IPC path didn't apply and the traced pad count is plausible for the part.
        if not applied and traced_pads:
            npins = len(component.get("symbol", {}).get("pins", []))
            if not npins or abs(len(traced_pads) - npins) <= max(2, npins * 0.4):
                component.setdefault("footprint", {})["pads"] = traced_pads
                component["footprint"]["generated"] = "vector-trace"
    except Exception:
        pass

    # Ground the proposed footprint against the actual recommended-layout figure.
    # Grounding and the deterministic checks below are recorded as evidence and
    # warnings.  A valid Luna extraction is directly placeable: the PCB workflow
    # does not require a separate human-approval transaction.
    grounding: dict[str, Any] = {"status": "not_available"}
    try:
        with tempfile.TemporaryDirectory() as grounding_dir:
            grounded_pdf = Path(grounding_dir) / "datasheet.pdf"
            grounded_pdf.write_bytes(pdf_bytes)
            extractor_dir = Path(__file__).parents[2] / "tools" / "layout_extractor"
            if str(extractor_dir) not in sys.path:
                sys.path.insert(0, str(extractor_dir))
            import pipeline as layout_pipeline
            result = layout_pipeline.run(str(grounded_pdf), component)
            grounding = {
                "status": "passed" if result.get("ok") else "incomplete",
                "stage": result.get("stage"),
                "figure": result.get("figure"),
                "registration": result.get("registration"),
                "verification": result.get("verification"),
                "discrepancies": result.get("discrepancies") or [],
                "warnings": result.get("warnings") or [],
            }
    except Exception as exc:
        grounding = {"status": "error", "warnings": [str(exc)]}
    extraction["grounding"] = grounding

    from swarm.memory.footprint_verify import verify_footprint
    footprint_check = verify_footprint(component)
    warnings = list(footprint_check.get("issues") or [])
    if grounding.get("status") != "passed":
        warnings.append("recommended-layout visual grounding is incomplete")
    elif any(item.get("level") == "error" for item in grounding.get("discrepancies") or []):
        warnings.append("recommended-layout grounding contains pad errors")
    extraction["verification"] = {
        "state": "review_required",
        "geometry_confidence": footprint_check.get("confidence", "unverified"),
        "placement_allowed": False,
        "blockers": [],
        "warnings": sorted(set(warnings)),
    }

    try:
        from swarm.agents.schema_validator import validate_or_raise, ValidationError
        validate_or_raise(component, require_evidence=True)
    except ValidationError as exc:
        raise DatasheetError("Component trust-boundary validation failed: "
                             + "; ".join(exc.failures)) from exc

    if build_preview:
        try:
            from swarm.memory.component_cad_program import (
                ComponentCadError, prepare_blocked_component_preview,
                prepare_component_preview)
            preview = prepare_component_preview(
                component, inspection, preview_root=preview_root,
                compiler=component_cad_executable, datasheet_bytes=pdf_bytes)
            component = preview["component"]
        except ComponentCadError as exc:
            preview = prepare_blocked_component_preview(
                component, inspection, exc.issues, preview_root=preview_root)
            component = preview["component"]
        except Exception as exc:
            raise DatasheetError(f"Could not execute the typed component CAD program: {exc}") from exc
        try:
            validate_or_raise(component, require_evidence=True)
        except ValidationError as exc:
            raise DatasheetError("Generated component preview failed schema validation: "
                                 + "; ".join(exc.failures)) from exc

    return component


def process_batch(items: list[dict], timeout_each: int = 600) -> list[dict | None]:
    """Process multiple datasheets. items: [{"url": ..., "mpn": ...}, ...]
    Returns list of component/2 dicts (None on per-item failure).
    """
    results = []
    for item in items:
        try:
            r = process(item["url"], mpn=item.get("mpn", ""), timeout=timeout_each)
            results.append(r)
        except DatasheetError:
            results.append(None)
    return results

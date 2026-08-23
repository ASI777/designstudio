"""Low-cost visual triage of component datasheets with GPT-5.6 Sol.

Sol sees page contact sheets and identifies only the package-relevant regions.
The selected regions are then rendered losslessly for the Luna CAD-authoring
stage.  Sol never emits footprint coordinates or CAD geometry.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from swarm.runtime_paths import data_root


DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_REASONING_EFFORT = "medium"
DEFAULT_TIMEOUT_SECONDS = 300
SCHEMA_PATH = Path(__file__).parents[2] / "docs" / "schemas" / "datasheet-inspection-v1.schema.json"


class DatasheetInspectionError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def inspection_digest(inspection: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(inspection)).hexdigest()


def render_contact_sheets(pdf_path: str | Path, *, columns: int = 3,
                          rows: int = 4, max_pages: int = 120) -> list[dict[str, Any]]:
    """Render labelled page thumbnails, amortizing many pages into each image."""
    try:
        import fitz
    except ImportError as exc:
        raise DatasheetInspectionError("PyMuPDF is required for datasheet inspection") from exc

    source = fitz.open(str(pdf_path))
    sheets: list[dict[str, Any]] = []
    page_count = min(len(source), max_pages)
    per_sheet = columns * rows
    try:
        for start in range(0, page_count, per_sheet):
            canvas = fitz.open()
            width, height = 1500.0, 1900.0
            sheet = canvas.new_page(width=width, height=height)
            margin, header = 24.0, 26.0
            cell_w = (width - margin * (columns + 1)) / columns
            cell_h = (height - margin * (rows + 1)) / rows
            pages: list[int] = []
            for offset in range(per_sheet):
                index = start + offset
                if index >= page_count:
                    break
                row, column = divmod(offset, columns)
                x0 = margin + column * (cell_w + margin)
                y0 = margin + row * (cell_h + margin)
                sheet.insert_text((x0, y0 + 16), f"PDF page {index + 1}", fontsize=12)
                target = fitz.Rect(x0, y0 + header, x0 + cell_w, y0 + cell_h)
                sheet.show_pdf_page(target, source, index, keep_proportion=True)
                pages.append(index + 1)
            pixmap = sheet.get_pixmap(matrix=fitz.Matrix(1.0, 1.0), alpha=False)
            sheets.append({"name": f"contact-{start + 1:04d}.png",
                           "pages": pages, "bytes": pixmap.tobytes("png")})
            canvas.close()
    finally:
        source.close()
    if not sheets:
        raise DatasheetInspectionError("datasheet contains no renderable pages")
    return sheets


def render_selected_regions(pdf_path: str | Path, inspection: dict[str, Any],
                            *, dpi: int = 300) -> list[dict[str, Any]]:
    """Render Sol-selected normalized regions at manufacturing-drawing detail."""
    try:
        import fitz
    except ImportError as exc:
        raise DatasheetInspectionError("PyMuPDF is required for region rendering") from exc
    document = fitz.open(str(pdf_path))
    rendered: list[dict[str, Any]] = []
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    try:
        for index, region in enumerate(inspection.get("regions") or []):
            page_number = int(region.get("page", 0))
            if page_number < 1 or page_number > len(document):
                raise DatasheetInspectionError(f"inspection selected invalid page {page_number}")
            bbox = region.get("bbox_normalized") or [0, 0, 1, 1]
            if len(bbox) != 4:
                raise DatasheetInspectionError("inspection region has an invalid bounding box")
            x0, y0, x1, y1 = (float(value) for value in bbox)
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise DatasheetInspectionError("inspection region bounding box is not ordered")
            page = document[page_number - 1]
            rect = page.rect
            clip = fitz.Rect(rect.x0 + x0 * rect.width, rect.y0 + y0 * rect.height,
                             rect.x0 + x1 * rect.width, rect.y0 + y1 * rect.height)
            pixmap = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
            rendered.append({"page": page_number, "role": region.get("role", "other"),
                             "region": index + 1, "bytes": pixmap.tobytes("png"),
                             "width": pixmap.width, "height": pixmap.height})
    finally:
        document.close()
    return rendered


def build_prompt(*, mpn: str, sheets: list[dict[str, Any]]) -> str:
    coverage = "; ".join(f"image {index + 1}: pages {','.join(map(str, item['pages']))}"
                         for index, item in enumerate(sheets))
    return (
        "Visually inspect this electronic-component datasheet. Identify the exact requested "
        "MPN and package variant, then select tight regions needed to build its symbol, pin map, "
        "2D land pattern, orientation, and dimensionally correct package model. Return only the "
        "supplied inspection JSON schema. Bounding boxes are normalized [x0,y0,x1,y1] in the PDF "
        "page frame. Select printed evidence; do not derive millimetres from pixels and do not "
        "write CAD commands. If the exact package cannot be proven, lower confidence and explain "
        f"the ambiguity in warnings. Requested MPN: {mpn}. Contact-sheet coverage: {coverage}."
    )


def find_codex(executable: str | None = None) -> str:
    candidate = executable or os.environ.get("DESIGNSTUDIO_CODEX", "").strip()
    if candidate:
        path = shutil.which(candidate) if os.path.basename(candidate) == candidate else candidate
        if path and os.access(path, os.X_OK):
            return str(Path(path).resolve())
        raise DatasheetInspectionError(f"Codex CLI is not executable: {candidate}")
    found = shutil.which("codex")
    if not found:
        local = Path.home() / ".local" / "bin" / "codex"
        found = str(local) if os.access(local, os.X_OK) else ""
    if not found:
        raise DatasheetInspectionError("Codex CLI was not found; install it and run `codex login`")
    return str(Path(found).resolve())


def build_command(*, codex: str, output_path: str | Path,
                  image_paths: list[str | Path], working_directory: str | Path) -> list[str]:
    command = [
        codex, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--strict-config", "--sandbox", "read-only", "--skip-git-repo-check",
        "-C", str(Path(working_directory).resolve()), "-m", DEFAULT_MODEL,
        "-c", f'model_reasoning_effort="{DEFAULT_REASONING_EFFORT}"',
        "-c", 'web_search="disabled"', "--output-schema", str(SCHEMA_PATH.resolve()),
        "--output-last-message", str(Path(output_path).resolve()), "--color", "never",
    ]
    for image in image_paths:
        command.extend(("--image", str(Path(image).resolve())))
    command.append("-")
    return command


def _child_setup() -> None:
    os.setsid()
    try:
        ctypes.CDLL(None).prctl(1, signal.SIGTERM)
    except (AttributeError, OSError):
        pass


def _validate(inspection: dict[str, Any], mpn: str) -> None:
    try:
        from jsonschema import Draft202012Validator
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        errors = sorted(Draft202012Validator(schema).iter_errors(inspection),
                        key=lambda item: list(item.absolute_path))
    except (ImportError, ModuleNotFoundError):
        required = {"schema", "requested_mpn", "identified_mpn", "package_variant",
                    "confidence", "regions", "warnings"}
        errors = []
        if not isinstance(inspection, dict) or set(inspection) - required:
            errors.append("inspection contains unsupported fields")
        if not required.issubset(inspection):
            errors.append("inspection is missing required fields")
        if inspection.get("schema") != "design-studio.datasheet-inspection/1":
            errors.append("inspection schema is invalid")
        if not isinstance(inspection.get("regions"), list) or not inspection.get("regions"):
            errors.append("inspection requires selected regions")
        for region in inspection.get("regions") or []:
            bbox = region.get("bbox_normalized") if isinstance(region, dict) else None
            if not (isinstance(region, dict) and isinstance(region.get("page"), int)
                    and region.get("page", 0) >= 1 and isinstance(bbox, list)
                    and len(bbox) == 4 and all(isinstance(value, (int, float))
                                               and 0 <= value <= 1 for value in bbox)):
                errors.append("inspection region is invalid")
                break
    if errors:
        messages = [error.message if hasattr(error, "message") else str(error)
                    for error in errors[:8]]
        raise DatasheetInspectionError("Sol inspection failed schema validation: " +
                                       "; ".join(messages))
    normalize = lambda value: "".join(ch for ch in str(value).upper() if ch.isalnum())
    if mpn and normalize(inspection.get("requested_mpn")) != normalize(mpn):
        raise DatasheetInspectionError("Sol inspection changed the requested MPN")
    if float(inspection.get("confidence", 0)) < 0.55:
        raise DatasheetInspectionError("datasheet package variant is too ambiguous for CAD generation")


def call_codex(*, prompt: str, sheets: list[dict[str, Any]], mpn: str,
               working_directory: str | Path, timeout: int = DEFAULT_TIMEOUT_SECONDS,
               executable: str | None = None, popen_factory=subprocess.Popen) -> tuple[dict[str, Any], dict[str, Any]]:
    codex = find_codex(executable)
    with tempfile.TemporaryDirectory(prefix="designstudio-sol-") as directory:
        temp = Path(directory)
        images: list[Path] = []
        for index, sheet in enumerate(sheets):
            path = temp / f"contact-{index + 1:04d}.png"
            path.write_bytes(sheet["bytes"])
            images.append(path)
        output = temp / "inspection.json"
        command = build_command(codex=codex, output_path=output, image_paths=images,
                                working_directory=working_directory)
        process = popen_factory(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, preexec_fn=_child_setup)
        try:
            stdout, stderr = process.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (AttributeError, ProcessLookupError):
                pass
            raise DatasheetInspectionError(f"Sol inspection timed out after {timeout} seconds") from exc
        if process.returncode != 0:
            raise DatasheetInspectionError((stderr or stdout or "Sol inspection failed").strip()[-2000:])
        if not output.is_file():
            raise DatasheetInspectionError("Sol completed without an inspection manifest")
        try:
            inspection = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DatasheetInspectionError(f"Sol returned invalid JSON: {exc}") from exc
    _validate(inspection, mpn)
    return inspection, {"provider": "codex-cli", "model": DEFAULT_MODEL,
                        "reasoning_effort": DEFAULT_REASONING_EFFORT}


def inspect_datasheet(pdf_path: str | Path, *, mpn: str, datasheet_sha256: str,
                      working_directory: str | Path, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> tuple[dict[str, Any], dict[str, Any], bool]:
    cache_root = Path(os.environ.get("DESIGNSTUDIO_INSPECTION_CACHE",
                                     data_root() / "datasheet-inspections"))
    schema_digest = hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()
    cache_key = hashlib.sha256(
        f"{DEFAULT_MODEL}\0{DEFAULT_REASONING_EFFORT}\0{schema_digest}\0{datasheet_sha256}\0{mpn}"
        .encode("utf-8")).hexdigest()
    cached = cache_root / f"{cache_key}.json"
    if cached.is_file():
        inspection = json.loads(cached.read_text(encoding="utf-8"))
        _validate(inspection, mpn)
        return inspection, {"provider": "cache", "model": DEFAULT_MODEL,
                            "reasoning_effort": DEFAULT_REASONING_EFFORT}, True
    sheets = render_contact_sheets(pdf_path)
    inspection, provenance = call_codex(
        prompt=build_prompt(mpn=mpn, sheets=sheets), sheets=sheets, mpn=mpn,
        working_directory=working_directory, timeout=timeout)
    cache_root.mkdir(parents=True, exist_ok=True)
    temporary = cached.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(inspection, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, cached)
    return inspection, provenance, False

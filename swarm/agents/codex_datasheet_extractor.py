"""Supervised Codex CLI extraction for component datasheets.

Selected PDF pages are rendered locally and attached to a non-interactive
``codex exec`` run.  The CLI is pinned to Luna/xhigh and constrained by the
component/2 JSON schema.  It reuses the user's existing ``codex login`` session;
DesignStudio never stores a second OpenAI credential.
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable


DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_REASONING_EFFORT = "xhigh"
DEFAULT_TIMEOUT_SECONDS = 600


class CodexDatasheetError(RuntimeError):
    pass


def render_pages(pdf_path: str | Path, page_indices: Iterable[int],
                 dpi: int = 240) -> list[dict[str, Any]]:
    """Render selected zero-based pages as lossless PNGs."""
    try:
        import fitz
    except ImportError as exc:
        raise CodexDatasheetError("PyMuPDF is required for multimodal extraction") from exc

    document = fitz.open(str(pdf_path))
    rendered: list[dict[str, Any]] = []
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    try:
        for page_index in dict.fromkeys(int(index) for index in page_indices):
            if page_index < 0 or page_index >= len(document):
                continue
            page = document[page_index]
            rotation = page.rotation
            try:
                page.set_rotation(0)
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                raw = pixmap.tobytes("png")
            finally:
                page.set_rotation(rotation)
            rendered.append({
                "page": page_index + 1,
                "mime_type": "image/png",
                "bytes": raw,
                "width": pixmap.width,
                "height": pixmap.height,
            })
    finally:
        document.close()
    return rendered


def build_prompt(*, instructions: str, datasheet_text: str,
                 pages: list[dict[str, Any]], mpn: str = "",
                 inspection: dict[str, Any] | None = None) -> str:
    page_list = ", ".join(str(page["page"]) for page in pages)
    return (
        f"{instructions.strip()}\n\n"
        "Return only the JSON object required by the supplied output schema. "
        "Do not run commands, browse, edit files, or infer dimensions from image "
        "pixel scale. Printed manufacturer callouts and tables are authoritative.\n\n"
        f"Requested manufacturer part number: {mpn or 'unknown'}\n"
        f"Attached datasheet pages (one-based): {page_list}\n\n"
        "GPT-5.6 Sol Medium has already selected the attached package regions. "
        "Use that manifest as visual routing evidence, but independently read every "
        "printed dimension before authoring the component CAD construction.\n\n"
        f"SOL INSPECTION MANIFEST:\n{json.dumps(inspection or {}, sort_keys=True)}\n\n"
        f"DATASHEET TEXT:\n{datasheet_text}"
    )


def find_codex(executable: str | None = None) -> str:
    candidate = executable or os.environ.get("DESIGNSTUDIO_CODEX", "").strip()
    if candidate:
        path = shutil.which(candidate) if os.path.basename(candidate) == candidate else candidate
        if path and os.access(path, os.X_OK):
            return str(Path(path).resolve())
        raise CodexDatasheetError(f"Codex CLI is not executable: {candidate}")
    found = shutil.which("codex")
    if not found:
        local = Path.home() / ".local" / "bin" / "codex"
        found = str(local) if os.access(local, os.X_OK) else ""
    if not found:
        raise CodexDatasheetError("Codex CLI was not found; install it and run `codex login`")
    return str(Path(found).resolve())


def build_command(*, codex: str, schema_path: str | Path,
                  output_path: str | Path, image_paths: Iterable[str | Path],
                  working_directory: str | Path) -> list[str]:
    command = [
        codex, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--strict-config", "--sandbox", "read-only", "--skip-git-repo-check",
        "-C", str(Path(working_directory).resolve()),
        "-m", DEFAULT_MODEL,
        "-c", f'model_reasoning_effort="{DEFAULT_REASONING_EFFORT}"',
        "-c", 'web_search="disabled"',
        "--output-schema", str(Path(schema_path).resolve()),
        "--output-last-message", str(Path(output_path).resolve()),
        "--color", "never",
    ]
    for image_path in image_paths:
        command.extend(("--image", str(Path(image_path).resolve())))
    command.append("-")
    return command


def _child_setup() -> None:
    """Create a killable group and terminate Codex if its agent parent dies."""
    os.setsid()
    try:
        libc = ctypes.CDLL(None)
        libc.prctl(1, signal.SIGTERM)  # Linux PR_SET_PDEATHSIG
    except (AttributeError, OSError):
        pass


def _terminate_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def call_codex(*, prompt: str, pages: list[dict[str, Any]],
               schema_path: str | Path, working_directory: str | Path,
               timeout: int = DEFAULT_TIMEOUT_SECONDS,
               executable: str | None = None,
               popen_factory=subprocess.Popen) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run Luna through Codex CLI and return component plus safe provenance."""
    codex = find_codex(executable)
    with tempfile.TemporaryDirectory(prefix="designstudio-codex-") as directory:
        temp = Path(directory)
        image_paths: list[Path] = []
        for index, page in enumerate(pages):
            path = temp / f"datasheet-page-{int(page['page']):04d}-{index}.png"
            path.write_bytes(page["bytes"])
            image_paths.append(path)
        output_path = temp / "component.json"
        command = build_command(
            codex=codex, schema_path=schema_path, output_path=output_path,
            image_paths=image_paths, working_directory=working_directory)
        process = popen_factory(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, preexec_fn=_child_setup)
        try:
            stdout, stderr = process.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _terminate_group(process)
            raise CodexDatasheetError(
                f"Codex datasheet extraction timed out after {timeout} seconds") from exc
        if process.returncode != 0:
            detail = (stderr or stdout or "unknown Codex CLI error").strip()[-2000:]
            raise CodexDatasheetError(f"Codex datasheet extraction failed: {detail}")
        if not output_path.exists():
            raise CodexDatasheetError("Codex completed without producing structured output")
        try:
            component = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CodexDatasheetError(f"Codex returned invalid component JSON: {exc}") from exc

    version = ""
    try:
        version = subprocess.run(
            [codex, "--version"], capture_output=True, text=True, timeout=5,
            check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return component, {
        "provider": "codex-cli",
        "model": DEFAULT_MODEL,
        "reasoning_effort": DEFAULT_REASONING_EFFORT,
        "response_id": "",
        "codex_version": version,
    }

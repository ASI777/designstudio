from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from swarm.agents.codex_datasheet_inspector import (  # noqa: E402
    build_command, build_prompt, call_codex, render_contact_sheets,
    render_selected_regions,
)


def result() -> dict:
    return {
        "schema": "design-studio.datasheet-inspection/1",
        "requested_mpn": "ABC-123", "identified_mpn": "ABC-123",
        "package_variant": "QFN-16", "confidence": 0.97,
        "regions": [
            {"page": 2, "role": "pinout", "bbox_normalized": [0.1, 0.1, 0.9, 0.9],
             "evidence_summary": "pin table"},
            {"page": 3, "role": "package", "bbox_normalized": [0, 0, 1, 1],
             "evidence_summary": "package drawing", "printed_callouts": ["D=3.0 mm"]},
        ], "warnings": [],
    }


class FakeProcess:
    def __init__(self, command, **_kwargs):
        self.command = command; self.returncode = 0

    def communicate(self, prompt, timeout):
        output = Path(self.command[self.command.index("--output-last-message") + 1])
        output.write_text(json.dumps(result()), encoding="utf-8")
        return "", ""


def test_sol_command_and_prompt() -> None:
    command = build_command(codex="/bin/true", output_path="out.json",
                            image_paths=["one.png"], working_directory=".")
    assert "gpt-5.6-sol" in command
    assert 'model_reasoning_effort="medium"' in command
    assert command.count("--image") == 1 and "read-only" in command
    prompt = build_prompt(mpn="ABC-123", sheets=[{"pages": [1, 2], "bytes": b"png"}])
    assert "do not write CAD commands" in prompt and "ABC-123" in prompt


def test_contact_sheet_region_render_and_structured_call() -> None:
    try:
        import fitz
    except ImportError:
        sheets = [{"pages": [1, 2, 3], "bytes": b"synthetic-contact-sheet"}]
        parsed, provenance = call_codex(
            prompt="inspect", sheets=sheets, mpn="ABC-123", working_directory=ROOT,
            executable="/bin/true", popen_factory=FakeProcess)
        assert parsed["package_variant"] == "QFN-16"
        assert provenance["model"] == "gpt-5.6-sol"
        return
    with tempfile.TemporaryDirectory(prefix="ds-sol-test-") as raw:
        root = Path(raw); pdf = root / "part.pdf"
        document = fitz.open()
        for page_number in range(1, 5):
            page = document.new_page()
            page.insert_text((72, 72), f"ABC-123 page {page_number} package land pattern")
        document.save(pdf)
        sheets = render_contact_sheets(pdf, columns=2, rows=2)
        assert len(sheets) == 1 and sheets[0]["pages"] == [1, 2, 3, 4]
        regions = render_selected_regions(pdf, result(), dpi=72)
        assert [region["page"] for region in regions] == [2, 3]
        assert all(region["bytes"].startswith(b"\x89PNG") for region in regions)
        parsed, provenance = call_codex(
            prompt="inspect", sheets=sheets, mpn="ABC-123", working_directory=root,
            executable="/bin/true", popen_factory=FakeProcess)
        assert parsed["package_variant"] == "QFN-16"
        assert provenance == {"provider": "codex-cli", "model": "gpt-5.6-sol",
                              "reasoning_effort": "medium"}


if __name__ == "__main__":
    test_sol_command_and_prompt()
    test_contact_sheet_region_render_and_structured_call()
    print("Codex Sol datasheet inspection tests passed")

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from swarm.agents.codex_datasheet_extractor import (
    build_command,
    build_prompt,
    call_codex,
    render_pages,
)
from swarm.agents.datasheet_agent import _select_relevant_page_indices, process
from swarm.memory.footprint_verify import placement_allowed


def _component() -> dict:
    return {
        "schema": "design-studio.component/2",
        "component": {"manufacturer": "Example", "mpn": "ABC-123"},
        "symbol": {"pins": [
            {"number": "1", "name": "IN", "electrical_type": "input"},
            {"number": "2", "name": "GND", "electrical_type": "power_in"},
        ]},
        "electrical": {"parameters": [{"name": "test", "typ": 1, "unit": "V"}]},
        "footprint": {
            "name": "SOT_TEST", "mount": "smd", "generated": "ipc7351",
            "pads": [
                {"number": "1", "x_mm": -0.5, "y_mm": 0, "width_mm": 0.4,
                 "height_mm": 0.8, "shape": "rect"},
                {"number": "2", "x_mm": 0.5, "y_mm": 0, "width_mm": 0.4,
                 "height_mm": 0.8, "shape": "rect"},
            ],
        },
        "package_3d": {
            "height_mm": 1.0, "standoff_mm": 0.0, "shape": "box",
            "construction": {
                "author": "gpt-5.6-luna-xhigh", "method": "parametric",
                "complexity": "simple", "complexity_reasons": [],
                "datasheet_pages": [1], "assumptions": [],
                "primitives": [{
                    "id": "body", "role": "body", "shape": "box",
                    "center_mm": [0, 0, 0.5], "size_mm": [1.4, 1.6, 1.0],
                    "rotation_deg_xyz": [0, 0, 0],
                    "color_rgba": [0.05, 0.06, 0.07, 1.0],
                    "provenance": ["datasheet:page:1:package body"],
                }],
            },
        },
        "extraction": {
            "provider": "codex-cli",
            "grounding": {"status": "passed"},
            "verification": {
                "state": "review_required", "placement_allowed": False,
                "blockers": [], "warnings": [],
            },
        },
    }


class _FakeProcess:
    def __init__(self, command, **_kwargs):
        self.command = command
        self.returncode = 0

    def communicate(self, prompt, timeout):
        self.prompt = prompt
        self.timeout = timeout
        output = Path(self.command[self.command.index("--output-last-message") + 1])
        output.write_text(json.dumps(_component()), encoding="utf-8")
        return json.dumps(_component()), ""

    def poll(self):
        return self.returncode


class CodexDatasheetExtractorTests(unittest.TestCase):
    def test_command_pins_luna_xhigh_schema_images_and_read_only(self):
        command = build_command(
            codex="/bin/true", schema_path="schema.json", output_path="out.json",
            image_paths=("page-1.png", "page-2.png"), working_directory=".")
        self.assertIn("gpt-5.6-luna", command)
        self.assertIn('model_reasoning_effort="xhigh"', command)
        self.assertIn("--output-schema", command)
        self.assertIn("read-only", command)
        self.assertEqual(command.count("--image"), 2)
        self.assertEqual(command[-1], "-")

    def test_prompt_forbids_pixel_scale_and_identifies_pages(self):
        prompt = build_prompt(
            instructions="Extract the package.", datasheet_text="ABC-123 pin table",
            pages=[{"page": 3}], mpn="ABC-123")
        self.assertIn("ABC-123", prompt)
        self.assertIn("Attached datasheet pages (one-based): 3", prompt)
        self.assertIn("pixel scale", prompt)
        self.assertIn("Return only the JSON object", prompt)

    def test_call_parses_structured_codex_output(self):
        with tempfile.TemporaryDirectory() as directory:
            schema = Path(directory) / "schema.json"
            schema.write_text('{"type":"object"}', encoding="utf-8")
            parsed, provenance = call_codex(
                prompt="extract", pages=[{"page": 1, "bytes": b"png"}],
                schema_path=schema, working_directory=directory,
                executable="/bin/true", popen_factory=_FakeProcess)
        self.assertEqual(parsed["component"]["mpn"], "ABC-123")
        self.assertEqual(provenance["provider"], "codex-cli")
        self.assertEqual(provenance["model"], "gpt-5.6-luna")
        self.assertEqual(provenance["reasoning_effort"], "xhigh")

    def test_render_and_page_selection_prioritize_pin_and_layout_pages(self):
        import fitz
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "part.pdf"
            doc = fitz.open()
            for text in ("ABC-123 overview", "ordinary specifications",
                         "PIN CONFIGURATION PIN DESCRIPTION",
                         "RECOMMENDED PCB LAYOUT LAND PATTERN"):
                page = doc.new_page()
                page.insert_text((72, 72), text)
            doc.save(pdf)
            selected = _select_relevant_page_indices(pdf, limit=4)
            self.assertEqual(selected, [0, 1, 2, 3])
            rendered = render_pages(pdf, [2, 3], dpi=72)
            self.assertEqual([page["page"] for page in rendered], [3, 4])
            self.assertTrue(all(page["bytes"].startswith(b"\x89PNG") for page in rendered))

    def test_codex_preview_is_not_placeable_before_approval(self):
        component = _component()
        component["extraction"]["verification"].update(
            {"state": "review_required", "placement_allowed": False})
        allowed, reason = placement_allowed(component)
        self.assertFalse(allowed)
        self.assertIn("approval", reason)

    def test_review_required_record_stays_blocked(self):
        component = _component()
        component["extraction"]["verification"] = {
            "state": "review_required", "placement_allowed": False,
            "blockers": ["visual review was required by an older build"],
        }
        allowed, reason = placement_allowed(component)
        self.assertFalse(allowed)
        self.assertIn("approval", reason)

    def test_published_through_hole_extraction_is_placeable(self):
        from swarm.agents.schema_validator import validate_or_raise
        component = _component()
        component["footprint"].update({
            "name": "HEADER_THT", "mount": "through_hole", "generated": "vector-trace",
            "pads": [
                {"number": "1", "x_mm": -1.27, "y_mm": 0, "width_mm": 1.8,
                 "height_mm": 1.8, "drill_mm": 1.0, "shape": "circle"},
                {"number": "2", "x_mm": 1.27, "y_mm": 0, "width_mm": 1.8,
                 "height_mm": 1.8, "drill_mm": 1.0, "shape": "circle"},
            ],
        })
        validate_or_raise(component)
        component["extraction"]["verification"].update(
            {"state": "approved", "placement_allowed": True})
        component["preview"] = {"state": "published"}
        allowed, _reason = placement_allowed(component)
        self.assertTrue(allowed)

    def test_local_pdf_runs_sol_then_luna_and_returns_review_preview(self):
        import fitz
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "ABC-123.pdf"
            doc = fitz.open()
            page = doc.new_page()
            page.insert_textbox(
                fitz.Rect(50, 50, 550, 750),
                ("ABC-123 Example component datasheet. PIN CONFIGURATION: "
                 "pin 1 IN, pin 2 GND. RECOMMENDED PCB LAYOUT LAND PATTERN. " * 8))
            doc.save(pdf)

            model_component = _component()
            model_component["footprint"].pop("generated", None)
            provenance = {
                "provider": "codex-cli", "model": "gpt-5.6-luna",
                "response_id": "", "reasoning_effort": "xhigh",
                "codex_version": "codex-cli test",
            }
            sol_inspection = {
                "schema": "design-studio.datasheet-inspection/1",
                "requested_mpn": "ABC-123", "identified_mpn": "ABC-123",
                "package_variant": "SOT-TEST", "confidence": 0.99,
                "regions": [
                    {"page": 1, "role": "pinout", "bbox_normalized": [0, 0, 1, 1],
                     "evidence_summary": "pinout"},
                    {"page": 1, "role": "package", "bbox_normalized": [0, 0, 1, 1],
                     "evidence_summary": "package"},
                    {"page": 1, "role": "land_pattern", "bbox_normalized": [0, 0, 1, 1],
                     "evidence_summary": "land pattern"},
                ], "warnings": [],
            }
            with patch("swarm.agents.codex_datasheet_extractor.call_codex",
                       return_value=(model_component, provenance)), \
                    patch("swarm.agents.codex_datasheet_inspector.inspect_datasheet",
                          return_value=(sol_inspection, {"provider": "codex-cli",
                              "model": "gpt-5.6-sol", "reasoning_effort": "medium"}, False)), \
                    patch.dict("os.environ", {
                        "DESIGNSTUDIO_COMPONENT_ASSET_ROOT": str(Path(directory) / "assets")
                    }):
                result = process(str(pdf), mpn="ABC-123", timeout=10, build_preview=False)

            self.assertEqual(result["evidence"]["mpn_match"], "exact")
            self.assertEqual(result["extraction"]["provider"], "codex-cli")
            self.assertEqual(result["extraction"]["reasoning_effort"], "xhigh")
            self.assertEqual(result["inspection"]["package_variant"], "SOT-TEST")
            self.assertEqual(result["extraction"]["verification"]["state"],
                             "review_required")
            self.assertFalse(result["extraction"]["verification"]["placement_allowed"])
            from swarm.agents.schema_validator import validate_or_raise
            validate_or_raise(result, require_evidence=True)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "reference_image_set.py"
SPEC = importlib.util.spec_from_file_location("reference_image_set", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class ReferenceImageSetTests(unittest.TestCase):
    def make_browser_capture(self, path: Path, product_shift: int = 0) -> None:
        image = Image.new("RGB", (800, 500), (22, 23, 25))
        draw = ImageDraw.Draw(image)
        draw.rectangle((105, 55, 690, 455), fill=(238, 238, 238))
        draw.rounded_rectangle((245 + product_shift, 170, 555 + product_shift, 350),
                               radius=28, fill=(190, 194, 200), outline=(60, 60, 65), width=5)
        draw.ellipse((420 + product_shift, 205, 535 + product_shift, 320),
                     fill=(30, 31, 35), outline=(105, 108, 115), width=8)
        image.save(path)

    def test_preserves_sources_groups_views_and_fails_stale_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "front.png"
            duplicate = root / "front-copy.jpg"
            detail = root / "detail.webp"
            self.make_browser_capture(first)
            Image.open(first).save(duplicate, quality=95)
            self.make_browser_capture(detail, 7)

            view_map = root / "views.json"
            view_map.write_text(json.dumps({
                first.name: {"view": "front", "confidence": 1.0, "source": "test"},
                duplicate.name: {"view": "front", "confidence": 0.9, "source": "test"},
                detail.name: {"view": "control_detail", "confidence": 0.8, "source": "test"},
            }))
            manifest_path = module.create_reference_image_set(
                [first, duplicate, detail], root / "sets", view_map, "camera")
            manifest = module.validate_reference_image_set(manifest_path)

            schema = json.loads((ROOT / "docs/schemas/reference-image-set-v1.schema.json")
                                .read_text(encoding="utf-8"))
            Draft202012Validator(schema).validate(manifest)
            self.assertEqual(manifest["source_count"], 3)
            self.assertFalse(manifest["millimetre_inference_allowed"])
            self.assertTrue(all(item["geometry_authority"] is False for item in manifest["sources"]))
            self.assertTrue(any(item["browser_chrome_detected"] for item in manifest["sources"]))
            self.assertLessEqual(len(manifest["representative_asset_ids"]), 8)
            self.assertTrue(all(
                item["input_detail"] == "original"
                for item in manifest["sources"]
                if item["asset_id"] in manifest["representative_asset_ids"]
            ))
            self.assertTrue(any(item["duplicate_relationships"] for item in manifest["sources"]))

            crop = manifest_path.parent / manifest["sources"][0]["cropped_path"]
            crop.write_bytes(crop.read_bytes() + b"tamper")
            with self.assertRaisesRegex(module.ReferenceImageSetError, "stale crop hash"):
                module.validate_reference_image_set(manifest_path)

    def test_rejects_unsupported_and_empty_sets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(module.ReferenceImageSetError, "at least one"):
                module.create_reference_image_set([], root)
            bad = root / "shape.svg"
            bad.write_text("<svg/>")
            with self.assertRaisesRegex(module.ReferenceImageSetError, "unsupported"):
                module.create_reference_image_set([bad], root)


if __name__ == "__main__":
    unittest.main()

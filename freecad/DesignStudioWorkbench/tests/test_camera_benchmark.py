#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

from jsonschema import Draft202012Validator

WORKBENCH = Path(__file__).resolve().parents[1]
ROOT = WORKBENCH.parents[1]
sys.path.insert(0, str(WORKBENCH))
from DesignStudio import camera_benchmark as module  # noqa: E402
from DesignStudio.vector_native_cad import validate_vector_program  # noqa: E402


def test_programs_and_evidence_contract() -> None:
    passing_checks = {name: True for name in module.REQUIRED_CANDIDATE_CHECKS}
    assert module.candidate_checks_pass(passing_checks)
    for critical in ("occt_validity", "watertight_solids", "semantic_ids_unique"):
        failing_checks = dict(passing_checks)
        failing_checks[critical] = False
        assert not module.candidate_checks_pass(failing_checks), critical
    missing_check = dict(passing_checks)
    del missing_check["occt_validity"]
    assert not module.candidate_checks_pass(missing_check)

    for label in ("precision", "grip", "serviceable"):
        program = module.mechanical_program(label)
        schema = json.loads((ROOT / "docs/schemas/mechanical-cad-program-v2.schema.json").read_text())
        Draft202012Validator(schema).validate(program)
        validate_vector_program(program)
        assert program["envelope"] == {"min_mm": [0, -21.7, 0], "max_mm": [145, 32, 98]}
        assert next(check for check in program["checks"] if check["kind"] == "bbox")["value"] == [145, 32, 95]
        feature_ids = {command["id"] for command in program["commands"]
                       if command["op"] in {"feature.loft", "feature.boolean", "feature.compound"}}
        common = {
            f"camera.{label}.midframe", f"camera.{label}.front-cover",
            f"camera.{label}.rear-cover", f"camera.{label}.rear-display-window",
            f"camera.{label}.lens-mount", f"camera.{label}.lens-barrel",
            f"camera.{label}.lens-glass", f"camera.{label}.control-dial",
            f"camera.{label}.shutter", "camera.reservation.optical-module",
            "camera.reservation.rear-display", "camera.reservation.battery",
            "camera.reservation.logic-pcb", "camera.reservation.usb-c",
            "camera.reservation.speaker",
        }
        common.update(f"camera.{label}.rib-{index:02d}" for index in range(1, 17))
        assert common <= feature_ids
        if label == "grip":
            assert "camera.grip.grip-transition" in feature_ids
        if label == "serviceable":
            assert {"camera.serviceable.service-fastener-1",
                    "camera.serviceable.service-fastener-2"} <= feature_ids
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        original = root / "original.png"; original.write_bytes(b"original")
        crop = root / "crop.png"; crop.write_bytes(b"crop")
        manifest = {
            "schema": "design-studio.reference-image-set/1", "set_id": "camera-test-set",
            "revision": 1, "authority": "inspiration-and-visual-evidence-only",
            "measurement_status": "uncalibrated", "millimetre_inference_allowed": False,
            "source_count": 1, "representative_asset_ids": ["image-1"],
            "sources": [{
                "asset_id": "image-1", "source_path": str(original),
                "stored_original_path": "original.png", "cropped_path": "crop.png",
                "source_sha256": module.digest_file(original),
                "crop_sha256": module.digest_file(crop), "media_type": "image/png",
                "pixel_size": {"width": 1, "height": 1},
                "crop_bbox_px": {"x": 0, "y": 0, "width": 1, "height": 1,
                                 "detected": False, "method": "test", "confidence": 1.0},
                "browser_chrome_detected": False,
                "view_classification": {"view": "front", "confidence": 1.0, "source": "test"},
                "perceptual_hash": "0" * 64, "duplicate_relationships": [],
                "geometry_authority": False, "measurement_status": "uncalibrated",
                "input_detail": "original", "selection_reason": "test representative",
            }],
            "provenance": {"created_at_utc": "2026-08-20T00:00:00Z", "created_by": "test",
                           "importer_version": "1", "sources_preserved": True,
                           "crop_policy": "non-authoritative-derived-copy"},
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        evidence = module.write_visual_evidence(manifest_path, root / "evidence.json")
        schema = json.loads((ROOT / "docs/schemas/visual-evidence-v2.schema.json").read_text())
        Draft202012Validator(schema).validate(evidence)
        assert evidence["measurement_status"] == "uncalibrated"
        assert any(value["unknown_id"] == "unknown-scale" for value in evidence["explicit_unknowns"])
        crop.write_bytes(b"tampered")
        try:
            module.write_visual_evidence(manifest_path, root / "tampered-evidence.json")
        except ValueError:
            pass
        else:
            raise AssertionError("tampered reference crop reached camera evidence generation")
        malformed = dict(manifest)
        malformed["authority"] = "CAD-authority"
        manifest_path.write_text(json.dumps(malformed))
        try:
            module.write_visual_evidence(manifest_path, root / "malformed-evidence.json")
        except Exception:
            pass
        else:
            raise AssertionError("malformed reference authority reached camera evidence generation")


def test_candidate_actions_are_digest_bound_and_restart_safe() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        records = {}
        for label in module.VARIANTS:
            record = {"candidate": label}
            for prefix in ("fcstd", "step", "program", "validation"):
                path = root / f"{label}.{prefix}"
                path.write_bytes(f"{label}:{prefix}".encode())
                record[f"{prefix}_path"] = str(path)
                record[f"{prefix}_sha256"] = module.digest_file(path)
            review_path = root / f"{label}.review.png"
            review_path.write_bytes(f"{label}:review".encode())
            record["review_artifacts"] = [{"path": str(review_path),
                                            "sha256": module.digest_file(review_path),
                                            "authority": "human-review-only"}]
            record["candidate_digest"] = module.canonical_json_digest(record)
            records[label] = record
        initial = module._seal_candidate_review({
            "schema": "design-studio.camera-candidate-review/2",
            "status": "awaiting-user-selection", "candidate_order": list(module.VARIANTS),
            "candidates": records, "allowed_actions": ["apply", "modify", "reject", "undo"],
            "revision": 0, "canonical_revision": None, "rejected_candidates": [],
            "modification_requests": [], "history": [], "last_action": None,
            "integrity_rule": "only a digest-bound applied candidate may become canonical; concept is never release authority",
        })
        precision_digest = records["precision"]["candidate_digest"]
        applied = module.apply_candidate_review_action(
            initial, "apply", candidate="precision", candidate_digest=precision_digest)
        assert applied["canonical_revision"]["candidate"] == "precision"
        assert applied["canonical_revision"]["release_authority"] is False
        state_path = root / "candidate-review.json"
        module.save_candidate_review(state_path, applied)
        restarted = module.load_candidate_review(state_path, verify_artifacts=True)
        assert restarted["state_sha256"] == applied["state_sha256"]

        grip_digest = records["grip"]["candidate_digest"]
        modified = module.apply_candidate_review_action(
            restarted, "modify", candidate="grip", candidate_digest=grip_digest,
            modification_request="Increase the right-hand grip transition by 2 mm.")
        assert modified["canonical_revision"] == restarted["canonical_revision"]
        assert module.apply_candidate_review_action(modified, "undo")["canonical_revision"] == \
            restarted["canonical_revision"]

        rejected = module.apply_candidate_review_action(
            restarted, "reject", candidate="grip", candidate_digest=grip_digest)
        assert rejected["canonical_revision"] == restarted["canonical_revision"]
        rejection_undone = module.apply_candidate_review_action(rejected, "undo")
        assert rejection_undone["rejected_candidates"] == []
        assert rejection_undone["canonical_revision"] == restarted["canonical_revision"]

        service_digest = records["serviceable"]["candidate_digest"]
        replaced = module.apply_candidate_review_action(
            restarted, "apply", candidate="serviceable", candidate_digest=service_digest)
        restored = module.apply_candidate_review_action(replaced, "undo")
        assert restored["canonical_revision"] == restarted["canonical_revision"]
        try:
            module.apply_candidate_review_action(
                initial, "apply", candidate="precision", candidate_digest="0" * 64)
        except ValueError:
            pass
        else:
            raise AssertionError("candidate with a stale digest became canonical")

        # Artifact bytes are re-read at action and persistence time, not only
        # when the state was first loaded.
        Path(records["precision"]["review_artifacts"][0]["path"]).write_bytes(b"tampered")
        for action, arguments in (
                ("apply", {"candidate": "precision", "candidate_digest": precision_digest}),
                ("modify", {"candidate": "precision", "candidate_digest": precision_digest,
                            "modification_request": "change"}),
                ("reject", {"candidate": "precision", "candidate_digest": precision_digest}),
                ("undo", {})):
            candidate_state = applied if action == "undo" else initial
            try:
                module.apply_candidate_review_action(candidate_state, action, **arguments)
            except ValueError:
                pass
            else:
                raise AssertionError(f"{action} accepted tampered review evidence")
        try:
            module.save_candidate_review(root / "tampered-state.json", initial)
        except ValueError:
            pass
        else:
            raise AssertionError("tampered candidate state was persisted")


if __name__ == "__main__":
    test_programs_and_evidence_contract()
    test_candidate_actions_are_digest_bound_and_restart_safe()
    print("CAMERA_BENCHMARK_CONTRACT_OK")

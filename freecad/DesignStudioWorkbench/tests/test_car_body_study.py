from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile


MODULE = (
    Path(__file__).resolve().parents[1]
    / "DesignStudio"
    / "car_body_study.py"
)
sys.path.insert(0, str(MODULE.parent))
SPEC = importlib.util.spec_from_file_location("car_body_study", MODULE)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)


def write_asset(root: Path, name: str) -> tuple[str, int]:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("fixture-" + name).encode())
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size


def valid_manifest(root: Path):
    manifest = module.new_study_manifest()
    manifest["assets"] = []
    for name in module.LOW_RES_PROVENANCE_NAMES:
        digest, size = write_asset(root, name)
        manifest["assets"].append({
            "path": name, "sha256": digest, "bytes": size,
            "source_tier": "low_resolution_provenance",
        })
    roles_by_name = {
        "front.jpg": ["front", "lights", "intakes"],
        "rear.jpg": ["rear", "spoiler", "badges"],
        "left.jpg": ["left", "windows", "mirrors", "wheel_arches"],
        "right.jpg": ["right"],
        "front-left.jpg": ["front_left_three_quarter"],
        "front-right.jpg": ["front_right_three_quarter"],
        "rear-left.jpg": ["rear_left_three_quarter"],
        "rear-right.jpg": ["rear_right_three_quarter"],
        "roof-a.jpg": ["elevated_roof"],
        "roof-b.jpg": ["elevated_roof"],
    }
    for name, roles in roles_by_name.items():
        digest, size = write_asset(root, name)
        manifest["assets"].append({
            "path": name, "sha256": digest, "bytes": size,
            "source_tier": "official_porsche_press",
            "source_url": "https://newsroom.porsche.com/fixture/" + name,
            "model_year": 2026,
            "configuration": "911 Turbo S Coupé (992.2), Vanadium Grey Metallic",
            "wheels": "Turbonite Turbo S wheels",
            "mirrors": "production exterior mirrors",
            "spoiler_state": "raised active rear spoiler",
            "copyright": "Porsche AG",
            "width_px": 3840, "height_px": 2160,
            "permitted_use_status": "review_required",
            "roles": roles,
            "mask": {
                "suggestion_model": "SAM 2.1",
                "brush_corrected": True, "approved": True,
                "revision_sha256": hashlib.sha256(
                    ("mask-" + name).encode()
                ).hexdigest(),
            },
        })
    manifest["silhouette_revisions"] = [{
        "viewpoint": viewpoint,
        "brush_corrected": True,
        "approved": True,
        "revision_sha256": hashlib.sha256(viewpoint.encode()).hexdigest(),
    } for viewpoint in module.VIEWPOINTS]
    return manifest


def valid_candidate(mesh: Path, revision: int, seed: int = 100):
    mesh.write_bytes(b"non-empty-mesh-fixture")
    return {
        "schema": module.CANDIDATE_SCHEMA,
        "candidate_id": f"omni-{seed}",
        "reference_revision": revision,
        "seed": seed,
        "mesh_sha256": hashlib.sha256(mesh.read_bytes()).hexdigest(),
        "mesh_bytes": mesh.stat().st_size,
        "mesh_valid": True,
        "dimensions_mm": list(module.TARGET_BOUNDS_MM),
        "body_width_excluding_mirrors_mm": 83.50,
        "bounding_box_error_mm": [0.01, 0.02, 0.01],
        "silhouette_iou": {
            viewpoint: 0.97 for viewpoint in module.VIEWPOINTS
        },
        "landmark_error_mm": {
            name: 0.3 for name in (
                "axles", "wheel_arches", "roof_peak", "lamps", "windows",
                "intakes", "spoiler",
            )
        },
        "release_eligible": False,
    }


def test_reference_gate_requires_files_coverage_masks_and_silhouettes():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        blocked = module.audit_reference_evidence(
            module.new_study_manifest(), root
        )
        assert blocked["status"] == "blocked"
        assert blocked["compute_provisioning_allowed"] is False
        assert any("user-supplied provenance" in reason for reason in blocked["errors"])

        manifest = valid_manifest(root)
        passed = module.audit_reference_evidence(manifest, root)
        assert passed["status"] == "pass", passed
        assert passed["compute_provisioning_allowed"] is True
        assert passed["bootstrap_compute_allowed"] is True
        assert passed["omni_generation_allowed"] is True
        assert passed["distribution_authorized"] is False
        assert passed["official_asset_count"] == 10

        manifest["silhouette_revisions"] = []
        bootstrap_only = module.audit_reference_evidence(manifest, root)
        assert bootstrap_only["status"] == "blocked"
        assert bootstrap_only["bootstrap_compute_allowed"] is True
        assert bootstrap_only["compute_provisioning_allowed"] is True
        assert bootstrap_only["omni_generation_allowed"] is False

        manifest = valid_manifest(root)
        manifest["assets"][-1]["width_px"] = 1024
        manifest["assets"][-1]["height_px"] = 768
        failed = module.audit_reference_evidence(manifest, root)
        assert failed["status"] == "blocked"
        assert any("below 2048" in reason for reason in failed["errors"])


def test_candidate_gates_and_four_sequential_seed_ranking():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        candidates = []
        for seed in range(200, 204):
            path = root / f"{seed}.glb"
            candidate = valid_candidate(path, 9, seed)
            report = module.candidate_diagnostics(
                candidate, expected_revision=9, mesh_path=path
            )
            assert report["passed"], report
            candidates.append(candidate)
        candidates[1]["silhouette_iou"]["rear"] = 0.949
        candidates[2]["landmark_error_mm"]["axles"] = 0.501
        candidates[3]["bounding_box_error_mm"][0] = 0.101
        ranking = module.rank_omni_candidates(
            candidates, expected_revision=9
        )
        assert ranking["passing_candidate_ids"] == ["omni-200"]
        assert ranking["recommended_candidate_id"] == "omni-200"
        assert ranking["automatic_cad_handoff"] is False
        assert ranking["release_eligible"] is False

        candidates[3]["seed"] = 999
        try:
            module.rank_omni_candidates(candidates, expected_revision=9)
            raise AssertionError("non-sequential seeds passed")
        except module.CarBodyStudyError:
            pass


def test_immutable_baseline_and_local_patch_refit():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        manifest = valid_manifest(root)
        audit = module.audit_reference_evidence(manifest, root)
        mesh = root / "selected.glb"
        candidate = valid_candidate(mesh, 12)
        report = module.candidate_diagnostics(
            candidate, expected_revision=12, mesh_path=mesh
        )
        patches = {
            "roof.center": "1" * 64,
            "roof.transition": "2" * 64,
            "fender.rear.left": "3" * 64,
            "door.left": "4" * 64,
        }
        import mesh_surface_fit
        fit_evidence = {
            "schema": mesh_surface_fit.FIT_SCHEMA,
            "request_sha256": "5" * 64,
            "native_mesh_sha256": candidate["mesh_sha256"],
            "deviation_reference_sha256": candidate["mesh_sha256"],
            "density_results": [
                {"kind": "decimated", "faces": 100_000},
                {"kind": "decimated", "faces": 300_000},
                {"kind": "native", "faces": 766_672},
                {"kind": "subdivided", "faces": 1_500_000},
            ],
            "patch_count": len(patches),
            "patch_hashes": patches,
            "continuity": {
                "roof.center/roof.transition": "G2",
                "roof.transition/door.left": "G1",
            },
            "deviation_metrics": {
                "median_mm": 0.42, "p95_mm": 0.70, "maximum_mm": 1.4,
            },
            "valid_outer_shell": True,
            "ap242_roundtrip_valid": True,
            "llm_geometry_authority": False,
        }
        fit_evidence["result_sha256"] = mesh_surface_fit._digest(fit_evidence)
        baseline = module.make_baseline_manifest(
            reference_audit=audit,
            candidate=candidate,
            candidate_report=report,
            patch_hashes=patches,
            continuity={
                "roof.center/roof.transition": "G2",
                "roof.transition/door.left": "G1",
            },
            deviation_metrics={
                "median_mm": 0.42, "p95_mm": 0.70, "maximum_mm": 1.4,
            },
            ap242_roundtrip_valid=True,
            manual_approval={
                "approved": True, "reviewer": "fixture-user",
                "approved_utc": "2026-07-24T00:00:00+00:00",
                "overlay_views": list(module.VIEWPOINTS),
            },
            fit_evidence=fit_evidence,
        )
        assert baseline["revision_name"] == "CarBodyBaseline"
        assert baseline["immutable"] is True
        assert module.baseline_filename(baseline).startswith("CarBodyBaseline-")

        output = dict(patches)
        output["roof.center"] = "a" * 64
        output["roof.transition"] = "b" * 64
        experiment = module.make_experiment(
            baseline,
            experiment_id="lower-roofline",
            parameters=module.INITIAL_VARIANTS["lower-roofline"],
            changed_patches=["roof.center"],
            transition_patches=["roof.transition"],
            locked_patches=["fender.rear.left"],
            output_patch_hashes=output,
            continuity_failures=[],
            dimensions_mm=dict(module.DATUMS_MM),
            silhouette_changes={
                viewpoint: 0.01 for viewpoint in module.VIEWPOINTS
            },
        )
        assert experiment["branch_of"] == "CarBodyBaseline"
        assert experiment["non_destructive"] is True

        escaped = dict(output)
        escaped["door.left"] = "c" * 64
        try:
            module.make_experiment(
                baseline,
                experiment_id="escaped",
                parameters={},
                changed_patches=["roof.center"],
                transition_patches=["roof.transition"],
                locked_patches=["fender.rear.left"],
                output_patch_hashes=escaped,
                continuity_failures=[],
                dimensions_mm=dict(module.DATUMS_MM),
                silhouette_changes={
                    viewpoint: 0.0 for viewpoint in module.VIEWPOINTS
                },
            )
            raise AssertionError("non-local patch change passed")
        except module.CarBodyStudyError:
            pass


def test_region_labels_are_exact_and_stale_marks_fail():
    patch_ids = {"roof.center"}
    marks = [{
        "mark_id": "mark-1", "patch_id": "roof.center",
        "label": "lock/preserve",
        "stroke": [[0, 0], [1, 1]], "revision": 4,
    }]
    assert module.validate_region_marks(marks, patch_ids) == marks
    marks[0]["label"] = "protect-ish"
    try:
        module.validate_region_marks(marks, patch_ids)
        raise AssertionError("unknown region label passed")
    except module.CarBodyStudyError:
        pass


if __name__ == "__main__":
    test_reference_gate_requires_files_coverage_masks_and_silhouettes()
    test_candidate_gates_and_four_sequential_seed_ranking()
    test_immutable_baseline_and_local_patch_refit()
    test_region_labels_are_exact_and_stale_marks_fail()
    print("CAR_BODY_STUDY_OK")

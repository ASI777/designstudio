"""Contracts for exact solver dispatch and advisory AI feedback."""
from __future__ import annotations

import json
import os
from pathlib import Path

from mechanical_feedback import build_mechanical_feedback
from solver_workers import (
    SolverRegistry,
    WorkerContractError,
    canonical_digest,
    validate_approved_job,
)


ROOT = Path(__file__).resolve().parents[2]


def _job(kind: str = "linear_static"):
    return {
        "schema": "design-studio.mechanical-analysis-job/1",
        "job_id": "job-camera-grip-001",
        "candidate_id": "candidate-balanced",
        "analysis_kind": kind,
        "geometry": {
            "artifact_sha256": "a" * 64,
            "format": "step",
            "units": "mm",
            "artifact_uri": "workspace://camera-grip/candidate.step",
        },
        "approval": {
            "status": "approved",
            "approved_by": "engineer@example.com",
            "approval_digest": "approval-001",
        },
        "solver": {"worker": "designcore-linear-static", "backend": "cpu"},
        "solver_input": {
            "csr": {
                "rows": 2,
                "cols": 2,
                "row_offsets": [0, 2, 4],
                "columns": [0, 1, 0, 1],
                "values": [2.0, -1.0, -1.0, 2.0],
            },
            "rhs": [0.0, 1.0],
            "fixed_dofs": [0],
            "fixed_values": [0.0],
        },
    }


def _modal_job():
    job = _job("modal_vibration")
    job["solver"] = {
        "worker": "designcore-uniform-cantilever-modal", "backend": "cpu"
    }
    job["solver_input"] = {
        "uniform_cantilever": {
            "length_m": 1.0,
            "youngs_modulus_pa": 70.0e9,
            "second_moment_m4": 1.0e-8,
            "cross_section_area_m2": 1.0e-3,
            "density_kg_per_m3": 2700.0,
            "mode_count": 3,
            "shape_sample_count": 21,
        }
    }
    return job


def test_approved_geometry_gate():
    validate_approved_job(_job())
    pending = _job()
    pending["approval"] = {"status": "pending", "approved_by": "", "approval_digest": ""}
    try:
        validate_approved_job(pending)
    except WorkerContractError:
        pass
    else:
        raise AssertionError("pending geometry crossed the exact-worker gate")


def test_unavailable_worker_is_truthful():
    result = SolverRegistry.default(library_path="/does/not/exist/libdesigncore.so").submit(_job())
    assert result["status"] == "unavailable"
    assert result["release_eligible"] is False
    assert result["evidence"]["analysis_available"] is False
    assert len(result["result_digest"]) == 64


def test_all_worker_kinds_have_a_contract():
    registry = SolverRegistry.default(library_path="/does/not/exist/libdesigncore.so")
    modal = registry.submit(_modal_job())
    assert modal["status"] == "unavailable"
    assert modal["release_eligible"] is False
    for kind in (
        "thermal",
        "nonlinear_contact",
        "drop_impact",
        "plastic_creep_fatigue",
        "injection_moulding_flow_warpage",
    ):
        result = registry.submit(_job(kind))
        assert result["status"] == "unavailable"
        assert result["release_eligible"] is False


def test_native_uniform_cantilever_modal_worker():
    library_path = os.environ.get("DESIGNSTUDIO_CORE_LIBRARY")
    if not library_path:
        return
    result = SolverRegistry.default().submit(_modal_job())
    assert result["status"] == "pass", result
    assert result["worker"]["name"] == "designcore-uniform-cantilever-modal"
    assert result["metrics"]["mode_count"] == 3
    assert result["metrics"]["first_mode_hz"] > 0.0
    assert result["metrics"]["frequencies_hz"][1] > result["metrics"]["frequencies_hz"][0]
    assert result["evidence"]["analysis_available"] is True
    assert result["evidence"]["formulation_scope"].startswith("uniform_slender")
    assert result["release_eligible"] is False

    cuda_job = _modal_job()
    cuda_job["solver"]["backend"] = "cuda"
    rejected = SolverRegistry.default().submit(cuda_job)
    assert rejected["status"] == "fail"
    assert "CPU-only" in rejected["warnings"][0]


def test_feedback_is_advisory_and_digest_bound():
    first = _job()
    result_a = {
        "schema": "design-studio.mechanical-analysis-result/1",
        "job_id": first["job_id"],
        "candidate_id": first["candidate_id"],
        "analysis_kind": first["analysis_kind"],
        "status": "pass",
        "worker": {"name": "test-exact", "version": "1", "backend": "cpu"},
        "metrics": {"max_displacement_mm": 0.2, "residual_norm": 1.0e-10},
        "evidence": {"analysis_available": True},
        "warnings": [],
        "release_eligible": True,
    }
    result_a["result_digest"] = canonical_digest(result_a, without="result_digest")
    second = _job()
    second["candidate_id"] = "candidate-compact"
    result_b = dict(result_a)
    result_b["candidate_id"] = second["candidate_id"]
    result_b["metrics"] = {"max_displacement_mm": 0.5, "residual_norm": 1.0e-8}
    result_b["result_digest"] = canonical_digest(result_b, without="result_digest")
    feedback = build_mechanical_feedback({
        "schema": "design-studio.mechanical-study/1",
        "study_id": "study-001",
        "candidates": [
            {"candidate_id": first["candidate_id"], "analysis_result": result_a},
            {"candidate_id": second["candidate_id"], "analysis_result": result_b},
        ],
    })
    assert feedback["recommendation"] == first["candidate_id"]
    assert feedback["approval_policy"]["ai_can_approve_geometry"] is False
    assert feedback["approval_policy"]["release_eligible"] is False
    assert feedback["feedback_digest"] == canonical_digest(feedback, without="feedback_digest")


def test_json_schemas_accept_contract_examples():
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return
    result = SolverRegistry.default(library_path="/does/not/exist/libdesigncore.so").submit(_job())
    feedback = build_mechanical_feedback({
        "study_id": "study-schema",
        "candidates": [{"candidate_id": "candidate-balanced", "analysis_result": result}],
    })
    for filename, value in (
        ("mechanical-analysis-job-v1.schema.json", _job()),
        ("mechanical-analysis-result-v1.schema.json", result),
        ("mechanical-feedback-v1.schema.json", feedback),
    ):
        schema = json.loads((ROOT / "docs" / "schemas" / filename).read_text())
        errors = list(Draft202012Validator(schema).iter_errors(value))
        assert not errors, f"{filename}: {errors}"
    if os.environ.get("DESIGNSTUDIO_CORE_LIBRARY"):
        modal_result = SolverRegistry.default().submit(_modal_job())
        modal_schema = json.loads(
            (ROOT / "docs" / "schemas" / "mechanical-analysis-result-v1.schema.json").read_text()
        )
        errors = list(Draft202012Validator(modal_schema).iter_errors(modal_result))
        assert not errors, errors


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_"):
            function()
    print("MECHANICAL_SOLVER_WORKER_OK")

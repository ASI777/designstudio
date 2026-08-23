"""Deterministic, advisory ranking of exact solver results.

This module is deliberately not a physics solver. It removes candidates with
hard failures, exposes missing evidence, and produces reviewable suggestions.
The host qualification workflow owns acceptance.
"""
from __future__ import annotations

from typing import Any

from solver_workers import canonical_digest


def _score(result: dict[str, Any]) -> float | None:
    if result.get("status") != "pass":
        return None
    metrics = result.get("metrics", {})
    values: list[float] = []
    for key in ("max_displacement_mm", "solution_l2_norm", "residual_norm"):
        value = metrics.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            values.append(float(value))
    if not values:
        return None
    # Lower residual/displacement is preferred, but the formula is kept
    # explicit so a future product can replace it with a requirement-weighted
    # Pareto ranking without changing the trust boundary.
    return 1.0 / (1.0 + sum(values) / len(values))


def build_mechanical_feedback(study: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(study, dict):
        raise ValueError("mechanical study must be an object")
    study_id = study.get("study_id")
    candidates = study.get("candidates")
    if not isinstance(study_id, str) or not study_id:
        raise ValueError("study_id is required")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("study candidates must be a non-empty list")

    rankings: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict) or not isinstance(item.get("candidate_id"), str):
            raise ValueError("each candidate requires candidate_id")
        result = item.get("analysis_result")
        if not isinstance(result, dict):
            result = {"status": "unavailable", "warnings": ["analysis result missing"]}
        integrity_valid = (
            isinstance(result.get("result_digest"), str)
            and result.get("result_digest") == canonical_digest(result, without="result_digest")
        )
        score = _score(result)
        release_eligible = result.get("release_eligible") is True
        qualification_complete = (
            result.get("status") == "pass" and release_eligible and integrity_valid)
        rankings.append({
            "candidate_id": item["candidate_id"],
            "analysis_status": result.get("status", "unavailable"),
            "qualification_complete": qualification_complete,
            "result_integrity_valid": integrity_valid,
            "advisory_score": score,
            "release_eligible": release_eligible,
        })
        if not integrity_valid:
            proposals.append({
                "candidate_id": item["candidate_id"],
                "kind": "reject_tampered_or_missing_result",
                "message": "The result digest is missing or does not match its evidence; rerun the exact worker.",
                "requires_human_approval": True,
            })
        elif result.get("status") != "pass":
            proposals.append({
                "candidate_id": item["candidate_id"],
                "kind": "run_exact_solver",
                "message": "Submit approved geometry to the registered exact worker before ranking this candidate.",
                "requires_human_approval": True,
            })
        elif not release_eligible:
            proposals.append({
                "candidate_id": item["candidate_id"],
                "kind": "complete_required_qualification",
                "message": "Solver convergence is advisory until required stress, displacement, contact, and DFM evidence is complete.",
                "requires_human_approval": True,
            })

    rankings.sort(
        key=lambda item: (
            not item["qualification_complete"],
            item["advisory_score"] is None,
            -(item["advisory_score"] or 0.0),
            item["candidate_id"],
        )
    )
    feasible = [item for item in rankings if item["qualification_complete"]]
    if feasible:
        decision_status = "advisory_ranked"
        recommendation = feasible[0]["candidate_id"]
    else:
        decision_status = "needs_exact_analysis_or_approval"
        recommendation = None

    feedback = {
        "schema": "design-studio.mechanical-feedback/1",
        "study_id": study_id,
        "decision_status": decision_status,
        "recommendation": recommendation,
        "rankings": rankings,
        "proposals": proposals,
        "approval_policy": {
            "ai_role": "advisory_rank_and_explain_only",
            "ai_can_approve_geometry": False,
            "human_approval_required": True,
            "exact_worker_required": True,
            "release_eligible": False,
        },
        "feedback_digest": "",
    }
    feedback["feedback_digest"] = canonical_digest(feedback, without="feedback_digest")
    return feedback


__all__ = ["build_mechanical_feedback"]

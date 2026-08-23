from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile

WORKBENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKBENCH))

from DesignStudio import physical_validation as pv  # noqa: E402


def _write(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return pv._artifact(root, path)


def _plan(root):
    now = "2026-08-14T00:00:00+00:00"
    session = _write(root, "contracts/session.json", b"session")
    candidates = []
    for index, label in enumerate(pv.LABELS):
        source = _write(root, f"mechanical/{label}.FCStd", f"fcstd-{label}".encode())
        fcstd = _write(root, f"bucks/{label}.FCStd", f"buck-fcstd-{label}".encode())
        step = _write(root, f"bucks/{label}.step", f"buck-step-{label}".encode())
        stl = _write(root, f"bucks/{label}.stl", f"buck-stl-{label}".encode())
        candidates.append({"candidate_id": f"fixture-{label}", "label": label,
            "semantic_id": f"candidate-fixture-{label}", "candidate_fcstd": source,
            "candidate_brep_digest": pv.canonical_digest({"candidate": label}),
            "grip_region": {"method": "user_confirmed_section_planes",
                "semantic_ids": [f"candidate-body-fixture-{label}"],
                "section_boxes_mm": [{"min_mm": [0, 0, 0], "max_mm": [40, 106, 66]}],
                "confirmed_by_user": True},
            "grip_buck": {"fcstd": fcstd, "step": step, "stl": stl,
                "brep_digest": pv.canonical_digest({"buck": label})},
            "material": "PETG", "print_settings": {"process": "FDM", "material": "PETG",
                "nozzle_mm": 0.4, "layer_height_mm": 0.2, "wall_mm": 2.0,
                "infill_percent": 15, "orientation": "split-plane-down"}})
    value = {"schema": pv.PLAN_SCHEMA, "plan_id": "fixture-plan", "session_id": "fixture-session",
        "session": session, "created_utc": now, "observer": "contract-test", "synthetic_fixture": True,
        "candidates": candidates, "participant_requirements": {"required_size_groups": list(pv.SIZE_GROUPS),
            "minimum_per_group": 1, "adult_participants": True, "each_tests_all_candidates": True,
            "counterbalanced_order": True}, "metrics": list(pv.METRICS),
        "provenance": {"created_by": "contract-test", "created_utc": now,
            "source": "synthetic contract fixture", "synthetic_fixture": True}}
    value["plan_digest"] = pv._self_digest(value, "plan_digest")
    return value


def _observation(plan, candidate, participant="participant-small", group="small", order=1, rating=3):
    now = "2026-08-14T01:00:00+00:00"
    value = {"schema": pv.OBSERVATION_SCHEMA, "observation_id": f"obs-{participant}-{candidate['label']}",
        "plan_id": plan["plan_id"], "plan_digest": plan["plan_digest"], "session_id": plan["session_id"],
        "candidate_id": candidate["candidate_id"], "candidate_semantic_id": candidate["semantic_id"],
        "candidate_fcstd_digest": candidate["candidate_fcstd"]["sha256"],
        "candidate_brep_digest": candidate["candidate_brep_digest"],
        "buck_step_digest": candidate["grip_buck"]["step"]["sha256"],
        "buck_stl_digest": candidate["grip_buck"]["stl"]["sha256"],
        "participant": {"anonymous_id": participant, "adult_confirmed": True, "hand_size_group": group,
            "hand_length_mm": 175.0, "hand_breadth_mm": 82.0}, "test_order": order,
        "ratings": {metric: rating for metric in pv.METRICS}, "tested_at": now, "observer": "tester",
        "provenance": {"entered_by": "contract-test", "entered_utc": now,
            "source": "synthetic workflow fixture", "synthetic_fixture": True}}
    value["observation_digest"] = pv._self_digest(value, "observation_digest")
    return value


def test_plan_schema_and_stale_artifacts():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        plan = _plan(root)
        assert pv.validate_test_plan(plan, root)["plan_id"] == "fixture-plan"
        invalid = deepcopy(plan)
        invalid["candidates"][0]["print_settings"]["wall_mm"] = 1.9
        invalid["plan_digest"] = pv._self_digest(invalid, "plan_digest")
        try:
            pv.validate_test_plan(invalid, root)
            raise AssertionError("undersized structural wall accepted")
        except pv.PhysicalValidationError:
            pass
        path = root / plan["candidates"][0]["grip_buck"]["step"]["path"]
        path.write_bytes(b"changed")
        try:
            pv.validate_test_plan(plan, root)
            raise AssertionError("stale buck digest accepted")
        except pv.PhysicalValidationError:
            pass


def test_fixed_ratings_and_bindings():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        plan = _plan(root)
        observation = _observation(plan, plan["candidates"][0])
        assert pv.validate_observation(observation, root, plan)["ratings"]["comfort"] == 3
        for bad in (0, 6, 2.5):
            invalid = deepcopy(observation)
            invalid["ratings"]["comfort"] = bad
            invalid["observation_digest"] = pv._self_digest(invalid, "observation_digest")
            try:
                pv.validate_observation(invalid, root, plan)
                raise AssertionError("invalid 1-5 value accepted")
            except pv.PhysicalValidationError:
                pass
        invalid = deepcopy(observation)
        invalid["buck_stl_digest"] = "0" * 64
        invalid["observation_digest"] = pv._self_digest(invalid, "observation_digest")
        try:
            pv.validate_observation(invalid, root, plan)
            raise AssertionError("stale observation binding accepted")
        except pv.PhysicalValidationError:
            pass


def test_duplicate_missing_coverage_and_synthetic_decision_rejection():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        plan = _plan(root)
        plan_path = root / "physical-validation/fixture-plan/physical-test-plan.json"
        plan_path.parent.mkdir(parents=True)
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        first = _observation(plan, plan["candidates"][0])
        result = pv.record_observation(root, plan_path, first)
        try:
            pv.record_observation(root, plan_path, {k: v for k, v in first.items()
                                                   if k not in {"schema", "plan_id", "plan_digest", "session_id",
                                                                "candidate_semantic_id", "candidate_fcstd_digest",
                                                                "candidate_brep_digest", "buck_step_digest",
                                                                "buck_stl_digest", "observation_digest"}})
            raise AssertionError("duplicate participant/candidate observation accepted")
        except pv.PhysicalValidationError:
            pass
        try:
            pv.create_candidate_decision(root, plan_path, [result["path"]],
                decision_id="fixture-decision", observer="contract-test",
                created_utc="2026-08-14T02:00:00+00:00")
            raise AssertionError("missing size-group coverage accepted")
        except pv.PhysicalValidationError as exc:
            assert "all three" in str(exc) or "coverage" in str(exc)

        paths = []
        groups = (("participant-small", "small"), ("participant-medium", "medium"),
                  ("participant-large", "large"))
        for participant_index, (participant, group) in enumerate(groups):
            for candidate_index, candidate in enumerate(plan["candidates"]):
                order = (candidate_index - participant_index) % 3 + 1
                observation = _observation(plan, candidate, participant, group, order, 3 + (candidate_index == 1))
                if participant == "participant-small" and candidate_index == 0:
                    paths.append(result["path"])
                else:
                    paths.append(pv.record_observation(root, plan_path, {
                        key: value for key, value in observation.items()
                        if key not in {"schema", "plan_id", "plan_digest", "session_id", "candidate_semantic_id",
                                       "candidate_fcstd_digest", "candidate_brep_digest", "buck_step_digest",
                                       "buck_stl_digest", "observation_digest"}})["path"])
        try:
            pv.create_candidate_decision(root, plan_path, paths,
                decision_id="fixture-decision", observer="contract-test",
                created_utc="2026-08-14T02:00:00+00:00")
            raise AssertionError("synthetic evidence produced a physical winner")
        except pv.PhysicalValidationError as exc:
            assert "synthetic" in str(exc)
        assert not (plan_path.parent / "physical-candidate-decision.json").exists()


if __name__ == "__main__":
    test_plan_schema_and_stale_artifacts()
    test_fixed_ratings_and_bindings()
    test_duplicate_missing_coverage_and_synthetic_decision_rejection()
    print("PHYSICAL_VALIDATION_CONTRACT_OK")

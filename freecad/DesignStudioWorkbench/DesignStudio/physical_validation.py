"""Digest-bound physical validation for editable product candidates.

Images and sketches are never interpreted here.  This layer starts from the
verified physical-design session and the candidate FreeCAD B-Reps, exports
explicit user-selected grip regions, and records human observations without
allowing synthetic fixtures to unlock a real decision.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from .constraint_candidates import validate_physical_design_session_v2
from .physical_design import canonical_digest, file_digest, shape_digest

PLAN_SCHEMA = "design-studio.physical-test-plan/1"
OBSERVATION_SCHEMA = "design-studio.physical-test-observation/1"
DECISION_SCHEMA = "design-studio.physical-candidate-decision/1"
METRICS = ("comfort", "finger_reach", "pressure_points", "wrist_angle",
           "slip_resistance", "preference")
SIZE_GROUPS = ("small", "medium", "large")
LABELS = ("compact", "balanced", "comfort")


class PhysicalValidationError(ValueError):
    """Fail-closed physical-validation contract error."""


def _utc(value):
    if not isinstance(value, str) or not value.strip():
        raise PhysicalValidationError("timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PhysicalValidationError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise PhysicalValidationError("timestamp must include a timezone")
    return value


def _digest(value, label):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise PhysicalValidationError(f"{label} must be a lowercase SHA-256")
    return value


def _id(value, label):
    if not isinstance(value, str) or not value or not value[0].isalpha() or len(value) > 128 \
            or any(not (c.isalnum() or c in "_.-") for c in value):
        raise PhysicalValidationError(f"{label} is invalid")
    return value


def _schema_path(name):
    candidates = [
        Path(__file__).resolve().parents[3] / "docs" / "schemas" / name,
        Path(__file__).resolve().parent / "docs" / "schemas" / name,
        Path(__file__).resolve().parents[1] / "docs" / "schemas" / name,
    ]
    return next((item for item in candidates if item.is_file()), candidates[0])


def _schema_validate(value, name):
    try:
        import jsonschema
    except ImportError:
        return
    schema = json.loads(_schema_path(name).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema,
        format_checker=jsonschema.FormatChecker())
    errors = sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path))
    if errors:
        where = ".".join(str(item) for item in errors[0].absolute_path)
        raise PhysicalValidationError(f"schema validation failed at {where or '<root>'}: {errors[0].message}")


def _resolve(root, relative, label, suffix=None):
    if not isinstance(relative, str) or not relative:
        raise PhysicalValidationError(f"{label}.path is required")
    path = Path(relative).expanduser()
    path = path.resolve() if path.is_absolute() else (root / path).resolve()
    if not path.is_relative_to(root):
        raise PhysicalValidationError(f"{label}.path escapes the workspace")
    if suffix and path.suffix.lower() != suffix:
        raise PhysicalValidationError(f"{label}.path must end in {suffix}")
    return path


def _artifact(root, path):
    path = Path(path).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size <= 0:
        raise PhysicalValidationError("artifact is absent, empty, or outside the workspace")
    return {"path": str(path.relative_to(root)), "sha256": file_digest(path), "bytes": path.stat().st_size}


def _verify_artifact(root, value, label, suffix=None):
    if not isinstance(value, dict) or set(value) != {"path", "sha256", "bytes"}:
        raise PhysicalValidationError(f"{label} artifact binding is invalid")
    path = _resolve(root, value["path"], label, suffix)
    if not path.is_file() or path.stat().st_size != value["bytes"] or file_digest(path) != value["sha256"]:
        raise PhysicalValidationError(f"{label} has a stale digest or size")
    return path


def _self_digest(value, field):
    payload = deepcopy(value)
    payload.pop(field, None)
    return canonical_digest(payload)


def validate_test_plan(value, workspace_root=None):
    plan = deepcopy(value)
    _schema_validate(plan, "physical-test-plan-v1.schema.json")
    if plan.get("schema") != PLAN_SCHEMA:
        raise PhysicalValidationError(f"schema must be {PLAN_SCHEMA}")
    _id(plan["plan_id"], "plan_id")
    _id(plan["session_id"], "session_id")
    _utc(plan["created_utc"])
    if plan["provenance"]["synthetic_fixture"] != plan["synthetic_fixture"]:
        raise PhysicalValidationError("plan synthetic provenance does not match")
    if tuple(plan["metrics"]) != METRICS:
        raise PhysicalValidationError("the fixed six-metric form cannot be changed")
    labels = [item["label"] for item in plan["candidates"]]
    if tuple(labels) != LABELS or len({item["candidate_id"] for item in plan["candidates"]}) != 3:
        raise PhysicalValidationError("controller plan requires compact, balanced and comfort exactly once")
    for candidate in plan["candidates"]:
        if candidate["material"] != candidate["print_settings"]["material"]:
            raise PhysicalValidationError("candidate material and print material differ")
        if candidate["print_settings"]["wall_mm"] < 2.0:
            raise PhysicalValidationError("grip buck wall must be at least 2.0 mm")
        if not candidate["grip_region"]["confirmed_by_user"]:
            raise PhysicalValidationError("grip region must be explicitly user-confirmed")
        _digest(candidate["candidate_brep_digest"], "candidate_brep_digest")
        _digest(candidate["grip_buck"]["brep_digest"], "grip_buck.brep_digest")
    if _self_digest(plan, "plan_digest") != plan["plan_digest"]:
        raise PhysicalValidationError("plan_digest is stale")
    if workspace_root is not None:
        root = Path(workspace_root).expanduser().resolve()
        _verify_artifact(root, plan["session"], "session", ".json")
        for candidate in plan["candidates"]:
            _verify_artifact(root, candidate["candidate_fcstd"], "candidate_fcstd", ".fcstd")
            for kind in ("fcstd", "step", "stl"):
                _verify_artifact(root, candidate["grip_buck"][kind], f"grip_buck.{kind}", f".{kind}")
    return plan


def validate_observation(value, workspace_root=None, plan=None):
    observation = deepcopy(value)
    _schema_validate(observation, "physical-test-observation-v1.schema.json")
    if observation.get("schema") != OBSERVATION_SCHEMA:
        raise PhysicalValidationError(f"schema must be {OBSERVATION_SCHEMA}")
    _utc(observation["tested_at"])
    _utc(observation["provenance"]["entered_utc"])
    for metric in METRICS:
        if type(observation["ratings"][metric]) is not int or not 1 <= observation["ratings"][metric] <= 5:
            raise PhysicalValidationError(f"ratings.{metric} must be an integer from 1 to 5")
    if observation["participant"]["hand_size_group"] not in SIZE_GROUPS:
        raise PhysicalValidationError("hand size group is invalid")
    if not observation["participant"]["adult_confirmed"]:
        raise PhysicalValidationError("only confirmed adult observations satisfy this plan")
    if _self_digest(observation, "observation_digest") != observation["observation_digest"]:
        raise PhysicalValidationError("observation_digest is stale")
    if plan is not None:
        if observation["plan_id"] != plan["plan_id"] or observation["plan_digest"] != plan["plan_digest"] \
                or observation["session_id"] != plan["session_id"]:
            raise PhysicalValidationError("observation is bound to another plan or session")
        candidate = next((item for item in plan["candidates"]
                          if item["candidate_id"] == observation["candidate_id"]), None)
        if candidate is None:
            raise PhysicalValidationError("observation candidate is not in the plan")
        bindings = {
            "candidate_semantic_id": candidate["semantic_id"],
            "candidate_fcstd_digest": candidate["candidate_fcstd"]["sha256"],
            "candidate_brep_digest": candidate["candidate_brep_digest"],
            "buck_step_digest": candidate["grip_buck"]["step"]["sha256"],
            "buck_stl_digest": candidate["grip_buck"]["stl"]["sha256"],
        }
        if any(observation[key] != expected for key, expected in bindings.items()):
            raise PhysicalValidationError("observation candidate or buck digest is stale")
    return observation


def validate_decision(value):
    decision = deepcopy(value)
    _schema_validate(decision, "physical-candidate-decision-v1.schema.json")
    if decision.get("schema") != DECISION_SCHEMA or decision.get("ranking_status") != "physical_validated":
        raise PhysicalValidationError("physical decision schema/status is invalid")
    if decision["provenance"]["synthetic_fixture"] is not False:
        raise PhysicalValidationError("synthetic evidence cannot create a real decision")
    if _self_digest(decision, "decision_digest") != decision["decision_digest"]:
        raise PhysicalValidationError("decision_digest is stale")
    return decision


def _find_semantic(document, semantic_id):
    matches = [obj for obj in getattr(document, "Objects", [])
               if str(getattr(obj, "DesignStudioSemanticId", "")) == semantic_id]
    if len(matches) != 1:
        raise PhysicalValidationError(f"semantic object {semantic_id} is missing or ambiguous")
    obj = matches[0]
    if getattr(obj, "Shape", None) is None or obj.Shape.isNull() or not obj.Shape.isValid():
        raise PhysicalValidationError(f"semantic object {semantic_id} has no valid B-Rep")
    return obj


def export_grip_test_plan(document, workspace_root, session_path, request):
    """Export three candidate-specific B-Rep grip bucks and their test plan."""
    root = Path(workspace_root).expanduser().resolve()
    source = _resolve(root, str(session_path), "session", ".json")
    if not source.is_file():
        raise PhysicalValidationError("session does not exist")
    session = validate_physical_design_session_v2(json.loads(source.read_text(encoding="utf-8")), root)
    if session["candidates"] is None or len(session["candidates"]) != 3:
        raise PhysicalValidationError("generate exactly three candidates before exporting grip bucks")
    if any(item["status"] == "rejected" or item["hard_failures"] for item in session["candidates"]):
        raise PhysicalValidationError("a rejected or hard-failed candidate cannot enter physical testing")
    if not isinstance(request, dict):
        raise PhysicalValidationError("test-plan request must be an object")
    plan_id = _id(request.get("plan_id"), "plan_id")
    observer = str(request.get("observer", "")).strip()
    if not observer:
        raise PhysicalValidationError("observer is required")
    created = _utc(request.get("created_utc"))
    material = str(request.get("material", "")).strip()
    settings = deepcopy(request.get("print_settings"))
    if not material or not isinstance(settings, dict):
        raise PhysicalValidationError("material and print_settings are required")
    settings["material"] = material
    regions = request.get("grip_regions")
    if not isinstance(regions, dict) or set(regions) != set(LABELS):
        raise PhysicalValidationError("explicit grip regions are required for all three candidates")
    output = root / "physical-validation" / plan_id / "bucks"
    output.mkdir(parents=True, exist_ok=True)
    source_fcstd = _resolve(root, session["document"]["path"], "document", ".fcstd")
    source_binding = _artifact(root, source_fcstd)
    candidates = []
    created_paths = []
    try:
        import FreeCAD as App
        import Mesh
        import Part
        for candidate in session["candidates"]:
            label = candidate["label"]
            body_semantic = candidate["semantic_ids"][-1]
            body = _find_semantic(document, body_semantic)
            if shape_digest(body.Shape) != candidate["shape_digest"]:
                raise PhysicalValidationError(f"{label} candidate B-Rep digest is stale")
            region = deepcopy(regions[label])
            if region.get("method") not in {"semantic_region", "user_confirmed_section_planes"} \
                    or region.get("confirmed_by_user") is not True:
                raise PhysicalValidationError(f"{label} grip region is not user-confirmed")
            boxes = region.get("section_boxes_mm")
            if not isinstance(boxes, list) or not boxes:
                raise PhysicalValidationError(f"{label} requires at least one explicit section box")
            pieces = []
            for box in boxes:
                low, high = box.get("min_mm"), box.get("max_mm")
                if not isinstance(low, list) or not isinstance(high, list) or len(low) != 3 or len(high) != 3 \
                        or any(not math.isfinite(float(v)) for v in low + high) \
                        or any(float(high[i]) <= float(low[i]) for i in range(3)):
                    raise PhysicalValidationError(f"{label} section box is invalid")
                clip = Part.makeBox(*(float(high[i]) - float(low[i]) for i in range(3)), App.Vector(*map(float, low)))
                common = body.Shape.common(clip)
                if not common.isNull() and common.Volume > 1e-6:
                    pieces.extend(common.Solids or [common])
            if not pieces:
                raise PhysicalValidationError(f"{label} explicit grip region does not intersect its B-Rep")
            buck_shape = Part.makeCompound(pieces) if len(pieces) > 1 else pieces[0]
            if buck_shape.isNull() or not buck_shape.isValid():
                raise PhysicalValidationError(f"{label} grip buck is invalid")
            candidate_dir = output / label
            candidate_dir.mkdir(parents=True, exist_ok=True)
            fcstd = candidate_dir / f"{label}-grip-buck.FCStd"
            step = candidate_dir / f"{label}-grip-buck.step"
            stl = candidate_dir / f"{label}-grip-buck.stl"
            buck_doc = App.newDocument(f"DSBuck_{plan_id}_{label}")
            try:
                feature = buck_doc.addObject("PartDesign::Feature", "GripBuck")
                feature.Label = f"{label.title()} grip-only buck"
                feature.Shape = buck_shape
                feature.addProperty("App::PropertyString", "DesignStudioSemanticId", "Physical Validation")
                feature.DesignStudioSemanticId = f"grip-buck-{session['session_id']}-{label}"
                feature.addProperty("App::PropertyString", "SourceCandidateDigest", "Physical Validation")
                feature.SourceCandidateDigest = candidate["shape_digest"]
                feature.addProperty("App::PropertyString", "GripRegionJSON", "Physical Validation")
                feature.GripRegionJSON = json.dumps(region, sort_keys=True, separators=(",", ":"))
                buck_doc.recompute()
                buck_doc.saveAs(str(fcstd))
                Part.export([feature], str(step))
                Mesh.export([feature], str(stl))
            finally:
                App.closeDocument(buck_doc.Name)
            created_paths.extend((fcstd, step, stl))
            # FCStd serialization can normalize compound ordering.  Bind the
            # receipt to the recomputed, reloaded B-Rep that users will open,
            # rather than to a transient in-memory ordering.
            reloaded = App.openDocument(str(fcstd))
            try:
                reloaded.recompute()
                reloaded.recompute()
                reloaded_feature = reloaded.getObject("GripBuck")
                if reloaded_feature is None or reloaded_feature.Shape.isNull() \
                        or not reloaded_feature.Shape.isValid():
                    raise PhysicalValidationError(f"{label} grip buck did not survive FCStd reload")
                stable_buck_digest = shape_digest(reloaded_feature.Shape)
            finally:
                App.closeDocument(reloaded.Name)
            candidates.append({
                "candidate_id": candidate["candidate_id"], "label": label,
                "semantic_id": candidate["semantic_ids"][0],
                "candidate_fcstd": source_binding,
                "candidate_brep_digest": candidate["shape_digest"],
                "grip_region": region,
                "grip_buck": {"fcstd": _artifact(root, fcstd), "step": _artifact(root, step),
                              "stl": _artifact(root, stl), "brep_digest": stable_buck_digest},
                "material": material, "print_settings": settings,
            })
        if len({item["grip_buck"]["brep_digest"] for item in candidates}) != 3 \
                or len({item["grip_buck"]["stl"]["sha256"] for item in candidates}) != 3:
            raise PhysicalValidationError("grip buck outputs are not candidate-specific")
        plan = {
            "schema": PLAN_SCHEMA, "plan_id": plan_id, "session_id": session["session_id"],
            "session": _artifact(root, source), "created_utc": created, "observer": observer,
            "synthetic_fixture": bool(request.get("synthetic_fixture", False)), "candidates": candidates,
            "participant_requirements": {"required_size_groups": list(SIZE_GROUPS), "minimum_per_group": 1,
                                         "adult_participants": True, "each_tests_all_candidates": True,
                                         "counterbalanced_order": True},
            "metrics": list(METRICS),
            "provenance": {"created_by": observer, "created_utc": created,
                           "source": "FreeCAD B-Rep grip-region export",
                           "synthetic_fixture": bool(request.get("synthetic_fixture", False))},
        }
        plan["plan_digest"] = _self_digest(plan, "plan_digest")
        validate_test_plan(plan, root)
        destination = root / "physical-validation" / plan_id / "physical-test-plan.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {"path": str(destination), "plan_digest": plan["plan_digest"],
                "candidate_count": 3, "artifacts": candidates, "ranking_status": "provisional"}
    except Exception:
        for path in created_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def record_observation(workspace_root, plan_path, value):
    root = Path(workspace_root).expanduser().resolve()
    plan_file = _resolve(root, str(plan_path), "plan", ".json")
    plan = validate_test_plan(json.loads(plan_file.read_text(encoding="utf-8")), root)
    incoming = deepcopy(value)
    candidate_id = incoming.get("candidate_id")
    candidate_label = incoming.get("candidate_label")
    candidate = next((item for item in plan["candidates"]
                      if item["candidate_id"] == candidate_id
                      or (candidate_id is None and item["label"] == candidate_label)), None)
    if candidate is None:
        raise PhysicalValidationError("candidate_id is not present in the plan")
    candidate_id = candidate["candidate_id"]
    observation = {
        "schema": OBSERVATION_SCHEMA, "observation_id": _id(incoming.get("observation_id"), "observation_id"),
        "plan_id": plan["plan_id"], "plan_digest": plan["plan_digest"], "session_id": plan["session_id"],
        "candidate_id": candidate_id, "candidate_semantic_id": candidate["semantic_id"],
        "candidate_fcstd_digest": candidate["candidate_fcstd"]["sha256"],
        "candidate_brep_digest": candidate["candidate_brep_digest"],
        "buck_step_digest": candidate["grip_buck"]["step"]["sha256"],
        "buck_stl_digest": candidate["grip_buck"]["stl"]["sha256"],
        "participant": deepcopy(incoming.get("participant")), "test_order": incoming.get("test_order"),
        "ratings": deepcopy(incoming.get("ratings")), "tested_at": incoming.get("tested_at"),
        "observer": str(incoming.get("observer", "")).strip(),
        "provenance": deepcopy(incoming.get("provenance")),
    }
    observation["observation_digest"] = _self_digest(observation, "observation_digest")
    validate_observation(observation, root, plan)
    directory = plan_file.parent / "observations"
    directory.mkdir(parents=True, exist_ok=True)
    for existing in directory.glob("*.json"):
        prior = json.loads(existing.read_text(encoding="utf-8"))
        if prior.get("participant", {}).get("anonymous_id") == observation["participant"]["anonymous_id"] \
                and prior.get("candidate_id") == candidate_id:
            raise PhysicalValidationError("duplicate participant/candidate observation")
        if prior.get("observation_id") == observation["observation_id"]:
            raise PhysicalValidationError("duplicate observation_id")
    destination = directory / f"{observation['observation_id']}.json"
    destination.write_text(json.dumps(observation, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"path": str(destination), "observation_digest": observation["observation_digest"],
            "synthetic_fixture": observation["provenance"]["synthetic_fixture"]}


def create_candidate_decision(workspace_root, plan_path, observation_paths, *, decision_id, observer, created_utc):
    root = Path(workspace_root).expanduser().resolve()
    plan_file = _resolve(root, str(plan_path), "plan", ".json")
    plan = validate_test_plan(json.loads(plan_file.read_text(encoding="utf-8")), root)
    if not isinstance(observation_paths, list) or not observation_paths:
        raise PhysicalValidationError("real observations are required")
    observations = []
    for item in observation_paths:
        path = _resolve(root, str(item), "observation", ".json")
        observation = validate_observation(json.loads(path.read_text(encoding="utf-8")), root, plan)
        observations.append(observation)
    if len({item["observation_digest"] for item in observations}) != len(observations):
        raise PhysicalValidationError("duplicate observation digest")
    participants = {}
    for item in observations:
        participants.setdefault(item["participant"]["anonymous_id"], []).append(item)
    candidate_ids = {item["candidate_id"] for item in plan["candidates"]}
    for anonymous_id, records in participants.items():
        if {item["candidate_id"] for item in records} != candidate_ids or len(records) != 3 \
                or {item["test_order"] for item in records} != {1, 2, 3}:
            raise PhysicalValidationError(f"participant {anonymous_id} must test all three candidates once in recorded order")
    coverage = {group: len({item["participant"]["anonymous_id"] for item in observations
                            if item["participant"]["hand_size_group"] == group}) for group in SIZE_GROUPS}
    if any(coverage[group] < plan["participant_requirements"]["minimum_per_group"] for group in SIZE_GROUPS):
        raise PhysicalValidationError("small, medium and large adult hand coverage is required")
    first_counts = {candidate_id: 0 for candidate_id in candidate_ids}
    for records in participants.values():
        first = next(item for item in records if item["test_order"] == 1)
        first_counts[first["candidate_id"]] += 1
    if max(first_counts.values()) - min(first_counts.values()) > 1:
        raise PhysicalValidationError("test order is not counterbalanced")
    if plan["synthetic_fixture"] or any(item["provenance"]["synthetic_fixture"] for item in observations):
        raise PhysicalValidationError("synthetic evidence cannot create a real candidate decision")
    scores = {}
    for candidate_id in candidate_ids:
        values = [rating for item in observations if item["candidate_id"] == candidate_id
                  for rating in (item["ratings"][metric] for metric in METRICS)]
        scores[candidate_id] = round(sum(values) / len(values), 6)
    maximum = max(scores.values())
    tied = sorted(candidate_id for candidate_id, score in scores.items() if score == maximum)
    balanced_id = next(item["candidate_id"] for item in plan["candidates"] if item["label"] == "balanced")
    selected = balanced_id if balanced_id in tied else tied[0]
    decision = {
        "schema": DECISION_SCHEMA, "decision_id": _id(decision_id, "decision_id"),
        "plan_id": plan["plan_id"], "plan_digest": plan["plan_digest"], "session_id": plan["session_id"],
        "ranking_status": "physical_validated", "selected_candidate_id": selected, "scores": scores,
        "coverage": {**coverage, "participants": len(participants),
                     "each_participant_tested_all_candidates": True, "counterbalanced": True},
        "aggregate_method": "equal-participant-mean-of-six-1-to-5-ratings", "exclusions": [],
        "tie_handling": "balanced", "observation_digests": sorted(item["observation_digest"] for item in observations),
        "created_utc": _utc(created_utc), "observer": str(observer).strip(),
        "provenance": {"created_by": str(observer).strip(), "created_utc": created_utc,
                       "source": "recorded physical grip-buck observations", "synthetic_fixture": False},
    }
    if not decision["observer"]:
        raise PhysicalValidationError("decision observer is required")
    decision["decision_digest"] = _self_digest(decision, "decision_digest")
    validate_decision(decision)
    destination = plan_file.parent / "physical-candidate-decision.json"
    destination.write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"path": str(destination), "decision_digest": decision["decision_digest"],
            "selected_candidate_id": selected, "ranking_status": "physical_validated"}

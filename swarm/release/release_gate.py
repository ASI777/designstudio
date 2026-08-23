#!/usr/bin/env python3
"""Fail-closed industrial handoff gate for a DesignStudio project."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from swarm.memory.component_binding import BindingError, validate_binding
from swarm.vendors.base import exact_mpn_match
from swarm.release.signing import load_trust_store, verify_manifest

REQUIRED_ARTIFACTS = {"gerber_archive", "drill", "bom", "pick_place", "board_step"}
PREVIEW_GENERATORS = {"designstudio-preview-fab-export", "fab_export_agent.py"}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _canonical_json(value) -> bytes:
    def normalize(item):
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, float) and item.is_integer():
            return int(item)
        return item

    return json.dumps(normalize(value), sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def _validate_component_record(project_dir: Path, ref: str, mpn: str,
                               binding_ref: dict) -> str | None:
    record_uri = str(binding_ref.get("record_uri", "")).strip()
    if not record_uri:
        return f"{ref} bound-component reference has no record_uri"
    record_path = _resolve(project_dir, record_uri)
    try:
        binding = _load(record_path)
        validate_binding(binding, require_complete=True, verify_asset=True,
                         library_root=record_path.parent)
    except (OSError, ValueError, BindingError) as exc:
        return f"{ref} bound-component record is invalid: {exc}"
    if binding.get("binding_id") != binding_ref.get("binding_id") \
            or binding.get("binding_digest") != binding_ref.get("binding_digest"):
        return f"{ref} bound-component record identity or digest does not match the project"
    identity = binding.get("component") or {}
    if not exact_mpn_match(mpn, identity.get("mpn", "")):
        return f"{ref} bound-component record MPN does not match the footprint"
    record_model = binding.get("model_3d") or {}
    compact_model = binding_ref.get("model_3d") or {}
    if record_model.get("sha256") != compact_model.get("sha256") \
            or record_model.get("model_to_footprint") != compact_model.get("model_to_footprint"):
        return f"{ref} bound-component STEP digest or transform differs from its reviewed record"
    return None


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _artifact_signature(role: str, path: Path) -> bool:
    try:
        if role == "gerber_archive":
            return zipfile.is_zipfile(path) and any(
                name.lower().endswith((".gbr", ".ger", ".gtl", ".gbl"))
                for name in zipfile.ZipFile(path).namelist())
        head = path.read_bytes()[:4096]
        if role == "drill": return b"M48" in head
        if role == "bom": return b"MPN" in head and b"Qty" in head
        if role == "pick_place": return b"Ref" in head and b"X_mm" in head
        if role.endswith("step"): return b"ISO-10303-21" in head.upper()
    except (OSError, zipfile.BadZipFile):
        return False
    return True


def validate_release(project_path: str, verification_path: str, manifest_path: str,
                     now: datetime | None = None, max_report_age_hours: float = 24.0,
                     max_quote_age_days: float = 30.0) -> dict:
    now = now or datetime.now(timezone.utc)
    project_file, verification_file, manifest_file = map(Path,
        (project_path, verification_path, manifest_path))
    errors: list[dict] = []; warnings: list[dict] = []
    def error(code, message): errors.append({"code": code, "message": message})
    def warning(code, message): warnings.append({"code": code, "message": message})

    try:
        project, verification, manifest = _load(project_file), _load(verification_file), _load(manifest_file)
    except Exception as exc:
        return {"schema": "design-studio.release-gate/1", "generated_utc": now.isoformat(),
                "status": "blocked", "project_sha256": None, "verification_sha256": None,
                "errors": [{"code": "INPUT_READ", "message": str(exc)}], "warnings": [],
                "counts": {"components": 0, "artifacts": 0, "approvals": 0}}
    base = manifest_file.resolve().parent
    project_hash, verification_hash = _sha(project_file), _sha(verification_file)

    try:
        signature_ok, signature_message = verify_manifest(manifest, load_trust_store())
        if not signature_ok:
            error("MANIFEST_SIGNATURE", signature_message)
    except Exception as exc:
        error("MANIFEST_SIGNATURE", f"Release trust store cannot be validated: {exc}")

    acknowledgements: dict[tuple[str, str], dict] = {}
    for item in manifest.get("incomplete_acknowledgements", []):
        key = (str(item.get("domain", "")), str(item.get("category", "")))
        stamp = _parse_time(item.get("utc", ""))
        valid = key[0] in ("verification", "electrical") and bool(key[1]) \
            and item.get("acknowledged") is True and bool(item.get("reviewer")) \
            and len(str(item.get("rationale", "")).strip()) >= 8 and stamp is not None \
            and (stamp - now).total_seconds() <= 300
        if key in acknowledgements:
            error("INCOMPLETE_ACK_DUPLICATE",
                  f"Duplicate incomplete acknowledgement for {key[0]}.{key[1]}")
        elif not valid:
            error("INCOMPLETE_ACK_INVALID",
                  f"Incomplete acknowledgement for {key[0]}.{key[1]} is invalid")
        else:
            acknowledgements[key] = item

    used_acknowledgements: set[tuple[str, str]] = set()

    def acknowledge_incomplete(domain: str, category: str) -> bool:
        key = (domain, category)
        item = acknowledgements.get(key)
        if item is None:
            return False
        used_acknowledgements.add(key)
        warning("INCOMPLETE_ACK_NO_RELEASE_AUTHORITY",
                f"{domain}.{category} acknowledgement is recorded for visibility but "
                "cannot override a critical release gate")
        return False

    if verification.get("schema") != "design-studio.verification/1":
        error("VERIFICATION_SCHEMA", "Unified verification report schema is missing or unsupported")
    binding = verification.get("project", {})
    if (binding.get("document_id") != project.get("document_id")
        or int(binding.get("revision", -1)) != int(project.get("revision", 0))
        or binding.get("file_sha256") != project_hash):
        error("STALE_VERIFICATION", "Verification report is not bound to the exact project bytes/revision")
    generated = _parse_time(verification.get("generated_utc", ""))
    if not generated or (now - generated).total_seconds() > max_report_age_hours * 3600 \
            or (generated - now).total_seconds() > 300:
        error("VERIFICATION_AGE", "Verification report is missing a valid recent timestamp")
    if verification.get("overall_status") == "fail":
        error("VERIFICATION_STATUS", "Unified verification status is not pass")
    elif verification.get("overall_status") not in ("pass", "incomplete"):
        error("VERIFICATION_STATUS", "Unified verification status is invalid")
    for name, category in verification.get("categories", {}).items():
        status = category.get("status")
        if status == "fail":
            error("CATEGORY_FAILED", f"Verification category '{name}' has failed findings")
        elif category.get("required") and status == "incomplete":
            acknowledge_incomplete("verification", name)
            error("CATEGORY_NOT_PASS",
                  f"Required verification category '{name}' is incomplete")
        elif category.get("required") and status != "pass":
            error("CATEGORY_NOT_PASS", f"Required verification category '{name}' is not pass")
    profile = verification.get("constraint_profile", {})
    if str(profile.get("source", "")).startswith("builtin:") or not profile.get("fabricator"):
        error("FAB_PROFILE", "Production release requires a named fabricator profile, not the built-in baseline")

    if manifest.get("schema") != "design-studio.release-manifest/1":
        error("MANIFEST_SCHEMA", "Release manifest schema is missing or unsupported")
    manifest_binding = manifest.get("project", {})
    if manifest_binding.get("sha256") != project_hash \
            or manifest_binding.get("document_id") != project.get("document_id") \
            or int(manifest_binding.get("revision", -1)) != int(project.get("revision", 0)):
        error("MANIFEST_PROJECT", "Manifest project binding is stale")
    manifest_verification = manifest.get("verification", {})
    declared_verification = _resolve(base, manifest_verification.get("path", ""))
    if manifest_verification.get("sha256") != verification_hash \
            or declared_verification != verification_file.resolve():
        error("MANIFEST_VERIFICATION", "Manifest verification digest does not match")

    compliance_items = manifest.get("compliance_reviews", [])
    compliance = {item.get("subject"): item for item in compliance_items}
    if len(compliance) != len(compliance_items):
        error("COMPLIANCE_REVIEW", "Compliance reviews contain duplicate subjects")
    for subject in ("freecad-distribution", "hunyuan3d-omni-model"):
        review = compliance.get(subject, {})
        reviewed_at = _parse_time(review.get("utc", ""))
        evidence_path = _resolve(base, review.get("evidence_path", ""))
        if review.get("status") != "approved" or not review.get("reviewer") \
                or review.get("distribution_authorized") is not True or not reviewed_at \
                or (reviewed_at - now).total_seconds() > 300:
            error("COMPLIANCE_REVIEW", f"Required compliance review '{subject}' is not approved")
        elif not evidence_path.is_file() or review.get("evidence_sha256") != _sha(evidence_path):
            error("COMPLIANCE_EVIDENCE", f"Compliance evidence for '{subject}' is missing or changed")
    analysis_item = manifest.get("electrical_analysis", {})
    analysis_path = _resolve(base, analysis_item.get("path", ""))
    try:
        if not analysis_path.is_file() or analysis_item.get("sha256") != _sha(analysis_path):
            raise ValueError("analysis digest mismatch")
        electrical_analysis = _load(analysis_path)
        if electrical_analysis.get("schema") != "design-studio.incremental-analysis/1":
            error("ELECTRICAL_SCHEMA", "Incremental electrical analysis schema is unsupported")
        digest_payload = dict(electrical_analysis)
        declared_digest = digest_payload.pop("report_digest", "")
        actual_digest = hashlib.sha256(_canonical_json(digest_payload)).hexdigest()
        if not re.fullmatch(r"[0-9a-f]{64}", declared_digest) \
                or declared_digest != actual_digest:
            error("ELECTRICAL_REPORT_DIGEST",
                  "Electrical analysis canonical digest is missing or changed")
        analysis_binding = electrical_analysis.get("project", {})
        if analysis_binding.get("document_id") != project.get("document_id") \
                or int(analysis_binding.get("revision", -1)) != int(project.get("revision", 0)) \
                or analysis_binding.get("file_sha256") != project_hash:
            error("STALE_ELECTRICAL_ANALYSIS",
                  "Electrical analysis is not bound to the exact project bytes/revision")
        analysis_time = _parse_time(electrical_analysis.get("generated_utc", ""))
        if not analysis_time or (now - analysis_time).total_seconds() > max_report_age_hours * 3600 \
                or (analysis_time - now).total_seconds() > 300:
            error("ELECTRICAL_ANALYSIS_AGE", "Electrical analysis is missing a valid recent timestamp")
        for name, category in electrical_analysis.get("categories", {}).items():
            status = category.get("status")
            if status == "fail":
                error("ELECTRICAL_CATEGORY_FAILED", f"Electrical category '{name}' failed")
            elif status == "incomplete":
                if category.get("critical", True):
                    acknowledge_incomplete("electrical", name)
                    error("ELECTRICAL_CATEGORY_INCOMPLETE",
                          f"Critical electrical category '{name}' is incomplete")
                else:
                    warning("ELECTRICAL_CATEGORY_ADVISORY",
                            f"Non-critical electrical category '{name}' is incomplete")
            elif status not in ("pass", "not_applicable"):
                error("ELECTRICAL_CATEGORY_STATUS",
                      f"Electrical category '{name}' has invalid status")
        if electrical_analysis.get("status") == "fail":
            error("ELECTRICAL_STATUS", "Incremental electrical analysis is not pass")
        elif electrical_analysis.get("status") not in ("pass", "incomplete"):
            error("ELECTRICAL_STATUS", "Incremental electrical analysis status is invalid")
    except Exception as exc:
        error("ELECTRICAL_ANALYSIS", f"Electrical analysis cannot be validated: {exc}")

    distributed_required = (project.get("system_architecture") or {}).get("kind") \
        == "distributed_six_axis_robot" or bool(manifest.get("distributed_analysis"))
    if distributed_required:
        distributed_item = manifest.get("distributed_analysis") or {}
        distributed_path = _resolve(base, distributed_item.get("path", ""))
        distributed_hash = ""
        try:
            distributed_hash = _sha(distributed_path)
            if distributed_item.get("sha256") != distributed_hash:
                raise ValueError("distributed analysis digest mismatch")
            distributed = _load(distributed_path)
            if distributed.get("schema") != "design-studio.distributed-robot-analysis/1":
                raise ValueError("unsupported distributed robot analysis schema")
            payload = dict(distributed)
            declared = payload.pop("report_digest", "")
            if declared != hashlib.sha256(_canonical_json(payload)).hexdigest():
                raise ValueError("distributed robot report digest mismatch")
            axes = distributed.get("axis_reports") or []
            if len(axes) != 6 or [axis.get("axis_id") for axis in axes] \
                    != [f"axis-{index}" for index in range(1, 7)]:
                error("DISTRIBUTED_AXIS_COUNT", "Release requires exactly six ordered axis reports")
            reports = axes + [distributed.get("coordinator_report") or {},
                              distributed.get("system_report") or {}]
            for index, report in enumerate(reports):
                scope = f"axis-{index + 1}" if index < 6 else \
                    "coordinator" if index == 6 else "system"
                if report.get("status") != "pass":
                    error("DISTRIBUTED_ANALYSIS_STATUS",
                          f"Critical distributed report '{scope}' is failed or incomplete")
                for digest_key in ("input_digest", "output_digest"):
                    if not re.fullmatch(r"[0-9a-f]{64}", str(report.get(digest_key, ""))):
                        error("DISTRIBUTED_ANALYSIS_DIGEST",
                              f"Distributed report '{scope}' lacks immutable {digest_key}")
            if distributed.get("status") != "pass" or distributed.get("release_blockers"):
                error("DISTRIBUTED_RELEASE_BLOCKERS",
                      "Axis, coordinator, or aggregate system analysis still has critical blockers")
        except Exception as exc:
            error("DISTRIBUTED_ANALYSIS", f"Distributed robot analysis cannot be validated: {exc}")

        expected_boards = {"coordinator", *(f"joint-{index}" for index in range(1, 7))}
        board_items = manifest.get("board_projects") or []
        boards = {str(item.get("board_id")): item for item in board_items}
        if set(boards) != expected_boards or len(boards) != len(board_items):
            error("DISTRIBUTED_BOARD_SET",
                  "Release requires one exact coordinator and six exact joint board projects")
        board_hashes = {}
        for board_id, item in boards.items():
            path = _resolve(base, item.get("path", ""))
            if not path.is_file() or item.get("sha256") != _sha(path):
                error("DISTRIBUTED_BOARD_DIGEST",
                      f"Board project '{board_id}' is missing or changed")
            else:
                board_hashes[board_id] = item["sha256"]
        distributed_approvals = manifest.get("distributed_approvals") or []
        approval_map = {(item.get("scope"), item.get("role")): item
                        for item in distributed_approvals}
        if len(approval_map) != len(distributed_approvals):
            error("DISTRIBUTED_APPROVAL_DUPLICATE", "Distributed approvals contain duplicates")
        for scope in sorted(expected_boards | {"system"}):
            expected_hash = project_hash if scope == "system" else board_hashes.get(scope)
            for role in ("engineering_review", "electrical_review",
                         "mechanical_review", "manufacturing_review"):
                approval = approval_map.get((scope, role), {})
                approved_at = _parse_time(approval.get("utc", ""))
                if not approval.get("approved") or not approval.get("reviewer") or not approved_at:
                    error("DISTRIBUTED_APPROVAL_MISSING",
                          f"{scope} lacks exact-project {role}")
                elif approval.get("project_sha256") != expected_hash \
                        or approval.get("analysis_sha256") != distributed_hash:
                    error("DISTRIBUTED_APPROVAL_STALE",
                          f"{scope} {role} is stale for this board or analysis")
    if project.get("unresolved_components"):
        error("UNRESOLVED_COMPONENTS", "Project still contains unresolved component records")

    footprints = project.get("footprints", [])
    ref_to_fp = {fp.get("ref"): fp for fp in footprints}
    if len(ref_to_fp) != len(footprints) or None in ref_to_fp:
        error("PART_IDENTITY", "Project contains missing or duplicate reference designators")
    quantities = Counter(fp.get("mpn", "") for fp in footprints)
    component_items = manifest.get("components", [])
    manifest_components = {item.get("ref"): item for item in component_items}
    if len(manifest_components) != len(component_items):
        error("MANIFEST_COMPONENT", "Manifest contains duplicate component refs")
    for ref, fp in ref_to_fp.items():
        mpn = fp.get("mpn", "")
        evidence = fp.get("datasheet_evidence", {})
        if not mpn or not fp.get("manufacturer"):
            error("PART_IDENTITY", f"{ref} lacks manufacturer/MPN identity")
            continue
        if evidence.get("mpn_match") != "exact" or not exact_mpn_match(mpn, evidence.get("expected_mpn", "")):
            error("DATASHEET_BINDING", f"{ref} lacks exact-MPN datasheet evidence")
        binding_ref = fp.get("bound_component", {})
        model = binding_ref.get("model_3d", {}) if isinstance(binding_ref, dict) else {}
        if binding_ref.get("schema") != "design-studio.bound-component-ref/1" \
                or not re.fullmatch(r"[0-9a-f]{64}", binding_ref.get("binding_digest", "")) \
                or model.get("format") != "step" \
                or not exact_mpn_match(mpn, model.get("claimed_mpn", "")):
            error("COMPONENT_BINDING", f"{ref} lacks a complete bound-component reference")
        else:
            semantic_issue = _validate_component_record(
                project_file.resolve().parent, str(ref), mpn, binding_ref)
            if semantic_issue:
                error("COMPONENT_SEMANTICS", semantic_issue)
            model_path = _resolve(project_file.resolve().parent, model.get("asset_uri", ""))
            if not model_path.is_file() or _sha(model_path) != model.get("sha256") \
                    or not _artifact_signature("board_step", model_path):
                error("COMPONENT_STEP", f"{ref} bound STEP asset is missing or changed")
        item = manifest_components.get(ref)
        if not item or not exact_mpn_match(mpn, item.get("mpn", "")):
            error("MANIFEST_COMPONENT", f"{ref} is missing or mismatched in the release manifest")
            continue
        evidence_path = _resolve(base, item.get("datasheet_path", ""))
        if not evidence_path.is_file() or _sha(evidence_path) != evidence.get("sha256") \
                or evidence_path.read_bytes()[:4] != b"%PDF":
            error("DATASHEET_FILE", f"{ref} datasheet file/digest is invalid")
        quote_path = _resolve(base, item.get("quote_path", ""))
        try:
            if item.get("quote_sha256") != _sha(quote_path):
                raise ValueError("quote digest mismatch")
            quote = _load(quote_path)
            quote_time = _parse_time(quote.get("retrieved_utc", ""))
            offers = [offer for offer in quote.get("offers", [])
                      if offer.get("exact_mpn_match") and exact_mpn_match(mpn, offer.get("mpn", ""))
                      and (offer.get("stock") or 0) >= quantities[mpn] and offer.get("price")]
            if quote.get("schema") != "design-studio.procurement-quote/1" \
                    or quote.get("quantity", 0) < quantities[mpn] or not quote_time \
                    or (now - quote_time).total_seconds() > max_quote_age_days * 86400 \
                    or (quote_time - now).total_seconds() > 300 or not offers:
                error("PROCUREMENT_QUOTE", f"{ref} has no fresh, in-stock exact-MPN quote")
        except Exception:
            error("PROCUREMENT_QUOTE", f"{ref} quote evidence cannot be read")

    extra_refs = set(manifest_components) - set(ref_to_fp)
    if extra_refs:
        error("MANIFEST_COMPONENT", "Manifest contains component refs absent from the project")

    required_roles = set(REQUIRED_ARTIFACTS)
    if project.get("verification_requirements", {}).get("require_enclosure_evidence"):
        required_roles |= {"enclosure_step", "enclosure_evidence"}
    artifact_items = manifest.get("artifacts", [])
    artifacts = {item.get("role"): item for item in artifact_items}
    if len(artifacts) != len(artifact_items):
        error("ARTIFACT_DUPLICATE", "Manifest contains duplicate artifact roles")
    for role in sorted(required_roles):
        item = artifacts.get(role)
        if not item:
            error("ARTIFACT_MISSING", f"Required handoff artifact '{role}' is missing")
            continue
        path = _resolve(base, item.get("path", ""))
        if not path.is_file() or _sha(path) != item.get("sha256"):
            error("ARTIFACT_DIGEST", f"Artifact '{role}' is missing or its digest changed")
            continue
        if role != "enclosure_evidence" and not _artifact_signature(role, path):
            error("ARTIFACT_FORMAT", f"Artifact '{role}' failed its format signature check")
        if item.get("generator") in PREVIEW_GENERATORS or not item.get("generator_version"):
            error("ARTIFACT_GENERATOR", f"Artifact '{role}' lacks an approved versioned generator")
        if role == "enclosure_evidence" and project.get("mechanical_contract"):
            try:
                collision = _load(path)
                if collision.get("schema") != "design-studio.mechanical-collision/1":
                    raise ValueError("FreeCAD mechanical-collision/1 evidence is required")
                digest_payload = dict(collision)
                declared = digest_payload.pop("report_digest", "")
                actual = hashlib.sha256(_canonical_json(digest_payload)).hexdigest()
                if declared != actual:
                    raise ValueError("collision report digest mismatch")
                collision_binding = collision.get("project", {})
                if collision_binding.get("document_id") != project.get("document_id") \
                        or int(collision_binding.get("revision", -1)) != int(project.get("revision", 0)) \
                        or collision_binding.get("file_sha256") != project_hash:
                    raise ValueError("collision report is stale for the project")
                contract = project.get("mechanical_contract", {})
                if collision.get("contract_digest") != contract.get("contract_digest"):
                    raise ValueError("collision report is stale for the mechanical contract")
                collision_time = _parse_time(collision.get("generated_utc", ""))
                if not collision_time \
                        or (now - collision_time).total_seconds() > max_report_age_hours * 3600 \
                        or (collision_time - now).total_seconds() > 300:
                    raise ValueError("collision report timestamp is not recent")
                if int(collision.get("components_checked", -1)) != len(footprints):
                    raise ValueError("collision report did not check every placed component")
                expected_fixed = len(contract.get("fixed_items", [])) \
                    + len(contract.get("connector_locations", []))
                if int(collision.get("fixed_items_checked", -1)) < expected_fixed:
                    raise ValueError("collision report did not check every fixed item")
                collision_status = collision.get("status")
                if collision_status == "fail" or collision.get("collisions"):
                    error("MECHANICAL_COLLISION", "FreeCAD reports solid intersections")
                elif collision_status == "incomplete":
                    acknowledge_incomplete("verification", "mechanical_collision")
                    error("MECHANICAL_COLLISION_INCOMPLETE",
                          "FreeCAD solid collision checking is incomplete")
                elif collision_status != "pass":
                    raise ValueError("collision report status is invalid")
            except Exception as exc:
                error("MECHANICAL_COLLISION_EVIDENCE",
                      f"FreeCAD collision evidence cannot be validated: {exc}")

    approvals = {item.get("role"): item for item in manifest.get("approvals", [])}
    if len(approvals) != len(manifest.get("approvals", [])):
        error("APPROVAL_DUPLICATE", "Release approvals contain duplicate roles")
    approval_roles = {"manufacturing_review", "mechanical_review",
                      "engineering_review", "electrical_review"}
    for role in approval_roles:
        approval = approvals.get(role, {})
        approved_at = _parse_time(approval.get("utc", ""))
        if not approval.get("approved") or not approval.get("reviewer") or not approved_at \
                or (approved_at - now).total_seconds() > 300:
            error("APPROVAL_MISSING", f"Required approval '{role}' is missing or incomplete")
        elif approval.get("project_sha256") != project_hash \
                or approval.get("verification_sha256") != verification_hash:
            error("APPROVAL_STALE",
                  f"Required approval '{role}' is not bound to this project and verification report")

    for domain, category in sorted(set(acknowledgements) - used_acknowledgements):
        warning("INCOMPLETE_ACK_UNUSED",
                f"Acknowledgement for {domain}.{category} does not match an incomplete category")

    return {"schema": "design-studio.release-gate/1",
            "generated_utc": now.isoformat(), "status": "ready" if not errors else "blocked",
            "project_sha256": project_hash, "verification_sha256": verification_hash,
            "errors": errors, "warnings": warnings,
            "counts": {"components": len(footprints), "artifacts": len(artifacts),
                       "approvals": len(approvals)}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True); parser.add_argument("--verification", required=True)
    parser.add_argument("--manifest", required=True); parser.add_argument("--out", required=True)
    args = parser.parse_args()
    report = validate_release(args.project, args.verification, args.manifest)
    Path(args.out).write_text(json.dumps(report, indent=2))
    print(f"Release gate: {report['status']} ({len(report['errors'])} errors)")
    return 0 if report["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())

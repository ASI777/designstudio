"""Fail-closed ProposalContract v2 builder and authorization boundary.

``design-studio.proposal/1`` is deliberately display-only.  It cannot identify
the document revision or host geometry it intends to mutate, and its proposer
can choose the gates, so it is never accepted for commit.

Version 2 supports one operation today: ``replace_line_constraints``.  The
gateway may build and structurally validate that untrusted proposal, but only a
trusted host can authorize it.  Authorization requires an exact document,
sketch, and host-geometry revision match; all gates selected by the trusted
operation policy; and an atomic replay/idempotency check.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
import threading
from typing import Any, Mapping, Sequence
import uuid


SCHEMA_V1 = "design-studio.proposal/1"
SCHEMA_V2 = "design-studio.proposal/2"
OP_REPLACE_LINE_CONSTRAINTS = "replace_line_constraints"

INT64_MIN = -(1 << 63)
INT64_MAX = (1 << 63) - 1

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_STABLE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")

# This policy is host-owned.  It is intentionally absent from proposal JSON:
# an AI/proposer can request additional analysis but cannot weaken this set.
MANDATORY_GATES_BY_OPERATION: Mapping[str, frozenset[str]] = {
    OP_REPLACE_LINE_CONSTRAINTS: frozenset(
        {
            "units_valid",
            "target_exists",
            "frame_binding_valid",
            "constraints_solved",
            "kernel_valid",
        }
    )
}


class ProposalBuildError(ValueError):
    """Raised when trusted code cannot construct a valid typed proposal."""


@dataclass(frozen=True)
class TrustedMutationContext:
    """Current host state and deterministic results supplied outside the proposal.

    The caller creating this object is the trusted Design Studio host, never the
    model/gateway.  Every identity and revision is compared to the proposal before
    any gate result is considered.
    """

    document_id: str
    document_revision: int
    document_sha256: str
    object_id: str
    sketch_id: str
    host_geometry_id: str
    geometry_index: int
    host_geometry_revision: int
    sketch_revision: int
    gate_results: Mapping[str, bool]


class ReplayGuard:
    """Process-local atomic replay guard for proposal UUIDs and mutation keys.

    A durable product implementation should persist these keys with the project
    commit log.  This object defines and tests the required consume-once behavior
    without pretending that a gateway-owned cache can authorize host mutation.
    """

    def __init__(self) -> None:
        self._proposal_ids: set[str] = set()
        self._idempotency_keys: set[str] = set()
        self._lock = threading.Lock()

    def consume(self, proposal_id: str, idempotency_key: str) -> tuple[bool, str]:
        with self._lock:
            if proposal_id in self._proposal_ids:
                return False, "proposal replay: proposal_id was already consumed"
            if idempotency_key in self._idempotency_keys:
                return False, "proposal replay: idempotency_key was already consumed"
            self._proposal_ids.add(proposal_id)
            self._idempotency_keys.add(idempotency_key)
            return True, ""


def _is_int(value: Any) -> bool:
    return type(value) is int


def _is_int64(value: Any) -> bool:
    return _is_int(value) and INT64_MIN <= value <= INT64_MAX


def _is_revision(value: Any, *, minimum: int = 0) -> bool:
    return _is_int(value) and minimum <= value <= INT64_MAX


def _is_finite_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value))


def _is_uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        return False
    return str(parsed) == value


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _is_stable_id(value: Any) -> bool:
    return isinstance(value, str) and _STABLE_ID_RE.fullmatch(value) is not None


def _shape(
    value: Any,
    path: str,
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(value, dict):
        return None, [f"{path} must be an object"]
    allowed = required | (optional or set())
    errors = [f"{path}.{key} is required" for key in sorted(required - value.keys())]
    errors.extend(f"{path}.{key} is not allowed" for key in sorted(value.keys() - allowed))
    return value, errors


def _point2_nm(value: Any, path: str) -> list[str]:
    if not isinstance(value, list) or len(value) != 2:
        return [f"{path} must contain exactly two int64 nanometre coordinates"]
    return [
        f"{path}[{index}] must be an int64 nanometre coordinate"
        for index, coordinate in enumerate(value)
        if not _is_int64(coordinate)
    ]


def _mutation_material(proposal: Mapping[str, Any]) -> dict[str, Any]:
    """Fields defining mutation equivalence, excluding UUID and provenance."""

    return {
        "schema": proposal["schema"],
        "kind": proposal["kind"],
        "document": proposal["document"],
        "operation": proposal["operation"],
        "evidence": proposal["evidence"],
    }


def _canonical_sha256(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ProposalBuildError(f"mutation material is not canonical JSON: {error}") from error
    return hashlib.sha256(encoded).hexdigest()


def proposal_idempotency_key(proposal: Mapping[str, Any]) -> str:
    """Return the required deterministic key for a v2 proposal."""

    return _canonical_sha256(_mutation_material(proposal))


def validate_proposal_structure(proposal: Any) -> tuple[bool, list[str]]:
    """Validate untrusted v2 shape and mutation semantics, without authorizing it."""

    if not isinstance(proposal, dict):
        return False, ["proposal must be an object"]
    if proposal.get("schema") == SCHEMA_V1:
        return False, ["design-studio.proposal/1 is deprecated and display-only"]
    if proposal.get("schema") != SCHEMA_V2:
        return False, [f"schema must be '{SCHEMA_V2}'"]

    top, errors = _shape(
        proposal,
        "proposal",
        required={
            "schema",
            "proposal_id",
            "idempotency_key",
            "kind",
            "document",
            "operation",
            "evidence",
            "provenance",
        },
    )
    if top is None:
        return False, errors

    if not _is_uuid(top.get("proposal_id")):
        errors.append("proposal.proposal_id must be a canonical lowercase UUID")
    if not _is_sha256(top.get("idempotency_key")):
        errors.append("proposal.idempotency_key must be a lowercase SHA-256 digest")
    if top.get("kind") != "geometry_correction":
        errors.append("proposal.kind must equal 'geometry_correction'")

    document, shape_errors = _shape(
        top.get("document"),
        "proposal.document",
        required={"id", "base_revision", "base_sha256"},
    )
    errors.extend(shape_errors)
    if document is not None:
        if not _is_stable_id(document.get("id")):
            errors.append("proposal.document.id must be a stable identifier")
        if not _is_revision(document.get("base_revision")):
            errors.append("proposal.document.base_revision must be a nonnegative int64")
        if not _is_sha256(document.get("base_sha256")):
            errors.append("proposal.document.base_sha256 must be a lowercase SHA-256 digest")

    operation, shape_errors = _shape(
        top.get("operation"),
        "proposal.operation",
        required={
            "op",
            "object_id",
            "sketch_id",
            "host_geometry_id",
            "geometry_index",
            "host_geometry_revision",
            "sketch_revision",
            "coordinate_space",
            "units",
            "target_start_nm",
            "target_end_nm",
        },
    )
    errors.extend(shape_errors)
    if operation is not None:
        if operation.get("op") != OP_REPLACE_LINE_CONSTRAINTS:
            errors.append("proposal.operation.op must equal 'replace_line_constraints'")
        for field in ("object_id", "sketch_id", "host_geometry_id"):
            if not _is_stable_id(operation.get(field)):
                errors.append(f"proposal.operation.{field} must be a stable identifier")
        for field in ("geometry_index", "host_geometry_revision", "sketch_revision"):
            if not _is_revision(operation.get(field)):
                errors.append(f"proposal.operation.{field} must be a nonnegative int64")
        if operation.get("coordinate_space") != "host_sketch_local":
            errors.append("proposal.operation.coordinate_space must equal 'host_sketch_local'")
        if operation.get("units") != "nm":
            errors.append("proposal.operation.units must equal 'nm'")
        errors.extend(_point2_nm(operation.get("target_start_nm"), "proposal.operation.target_start_nm"))
        errors.extend(_point2_nm(operation.get("target_end_nm"), "proposal.operation.target_end_nm"))
        if operation.get("target_start_nm") == operation.get("target_end_nm"):
            errors.append("proposal.operation replacement line must not be degenerate")

    evidence, shape_errors = _shape(
        top.get("evidence"),
        "proposal.evidence",
        required={
            "session_id",
            "session_revision",
            "source_sha256",
            "viewbox_id",
            "element_id",
            "deviation_id",
            "target_ids",
        },
    )
    errors.extend(shape_errors)
    if evidence is not None:
        for field in ("session_id", "viewbox_id", "element_id", "deviation_id"):
            if not _is_uuid(evidence.get(field)):
                errors.append(f"proposal.evidence.{field} must be a canonical lowercase UUID")
        if not _is_revision(evidence.get("session_revision"), minimum=1):
            errors.append("proposal.evidence.session_revision must be a positive int64")
        if not _is_sha256(evidence.get("source_sha256")):
            errors.append("proposal.evidence.source_sha256 must be a lowercase SHA-256 digest")
        target_ids = evidence.get("target_ids")
        if not isinstance(target_ids, list) or not target_ids:
            errors.append("proposal.evidence.target_ids must be a non-empty array")
        else:
            if len(set(item for item in target_ids if isinstance(item, str))) != len(target_ids):
                errors.append("proposal.evidence.target_ids must be unique")
            for index, target_id in enumerate(target_ids):
                if not _is_uuid(target_id):
                    errors.append(
                        f"proposal.evidence.target_ids[{index}] must be a canonical lowercase UUID"
                    )

    provenance, shape_errors = _shape(
        top.get("provenance"),
        "proposal.provenance",
        required={"source", "confidence"},
        optional={"prompt_id"},
    )
    errors.extend(shape_errors)
    if provenance is not None:
        source = provenance.get("source")
        if not isinstance(source, str) or not (1 <= len(source) <= 256):
            errors.append("proposal.provenance.source must contain 1..256 characters")
        prompt_id = provenance.get("prompt_id")
        if prompt_id is not None and (
            not isinstance(prompt_id, str) or not (1 <= len(prompt_id) <= 128)
        ):
            errors.append("proposal.provenance.prompt_id must contain 1..128 characters")
        confidence = provenance.get("confidence")
        if not _is_finite_number(confidence) or not (0.0 <= float(confidence) <= 1.0):
            errors.append("proposal.provenance.confidence must be finite and in [0,1]")

    # Verify the deterministic key only after the required subobjects exist.
    if not errors:
        expected_key = proposal_idempotency_key(top)
        if top["idempotency_key"] != expected_key:
            errors.append("proposal.idempotency_key does not match the canonical mutation material")

    return not errors, errors


def validate_proposal_for_display(proposal: Any) -> tuple[bool, list[str]]:
    """Permit historical v1 rendering without ever granting mutation authority."""

    if not isinstance(proposal, dict):
        return False, ["proposal must be an object"]
    if proposal.get("schema") == SCHEMA_V2:
        return validate_proposal_structure(proposal)
    if proposal.get("schema") != SCHEMA_V1:
        return False, ["unsupported proposal schema"]
    required = {"schema", "kind", "payload", "provenance", "must_pass"}
    missing = required - proposal.keys()
    if missing:
        return False, [f"legacy proposal is missing {', '.join(sorted(missing))}"]
    if not isinstance(proposal.get("payload"), dict):
        return False, ["legacy proposal payload must be an object"]
    return True, ["design-studio.proposal/1 is deprecated and display-only"]


def build_replace_line_constraints_proposal(
    *,
    document_id: str,
    base_revision: int,
    base_sha256: str,
    object_id: str,
    sketch_id: str,
    host_geometry_id: str,
    geometry_index: int,
    host_geometry_revision: int,
    sketch_revision: int,
    target_start_nm: Sequence[int],
    target_end_nm: Sequence[int],
    session_id: str,
    session_revision: int,
    source_sha256: str,
    viewbox_id: str,
    element_id: str,
    deviation_id: str,
    target_ids: Sequence[str],
    source: str,
    confidence: float,
    prompt_id: str | None = None,
    proposal_id: str | None = None,
) -> dict[str, Any]:
    """Build a typed proposal; gate names/results are intentionally not accepted."""

    provenance: dict[str, Any] = {"source": source, "confidence": confidence}
    if prompt_id is not None:
        provenance["prompt_id"] = prompt_id
    proposal: dict[str, Any] = {
        "schema": SCHEMA_V2,
        "proposal_id": proposal_id or str(uuid.uuid4()),
        "idempotency_key": "0" * 64,
        "kind": "geometry_correction",
        "document": {
            "id": document_id,
            "base_revision": base_revision,
            "base_sha256": base_sha256,
        },
        "operation": {
            "op": OP_REPLACE_LINE_CONSTRAINTS,
            "object_id": object_id,
            "sketch_id": sketch_id,
            "host_geometry_id": host_geometry_id,
            "geometry_index": geometry_index,
            "host_geometry_revision": host_geometry_revision,
            "sketch_revision": sketch_revision,
            "coordinate_space": "host_sketch_local",
            "units": "nm",
            "target_start_nm": list(target_start_nm),
            "target_end_nm": list(target_end_nm),
        },
        "evidence": {
            "session_id": session_id,
            "session_revision": session_revision,
            "source_sha256": source_sha256,
            "viewbox_id": viewbox_id,
            "element_id": element_id,
            "deviation_id": deviation_id,
            "target_ids": list(target_ids),
        },
        "provenance": provenance,
    }
    proposal["idempotency_key"] = proposal_idempotency_key(proposal)
    valid, reasons = validate_proposal_structure(proposal)
    if not valid:
        raise ProposalBuildError("cannot build proposal: " + "; ".join(reasons))
    return proposal


def validate_proposal(
    proposal: Any,
    *,
    trusted_context: TrustedMutationContext | None = None,
    replay_guard: ReplayGuard | None = None,
) -> tuple[bool, list[str]]:
    """Authorize one mutation, failing closed without trusted host inputs.

    This is intentionally stricter than :func:`validate_proposal_structure`.
    Successful return consumes the proposal UUID and idempotency key atomically.
    """

    valid, reasons = validate_proposal_structure(proposal)
    if not valid:
        return False, reasons
    if trusted_context is None:
        return False, ["trusted host mutation context is required"]
    if replay_guard is None:
        return False, ["trusted replay guard is required"]

    document = proposal["document"]
    operation = proposal["operation"]
    checks = (
        (trusted_context.document_id == document["id"], "proposal targets the wrong document"),
        (
            trusted_context.document_revision == document["base_revision"],
            "proposal base revision is stale",
        ),
        (
            trusted_context.document_sha256 == document["base_sha256"],
            "proposal base document hash is stale or wrong",
        ),
        (trusted_context.object_id == operation["object_id"], "proposal targets the wrong object"),
        (trusted_context.sketch_id == operation["sketch_id"], "proposal targets the wrong sketch"),
        (
            trusted_context.host_geometry_id == operation["host_geometry_id"],
            "proposal targets the wrong host geometry",
        ),
        (
            trusted_context.geometry_index == operation["geometry_index"],
            "proposal geometry index is stale or wrong",
        ),
        (
            trusted_context.host_geometry_revision == operation["host_geometry_revision"],
            "proposal host geometry revision is stale",
        ),
        (
            trusted_context.sketch_revision == operation["sketch_revision"],
            "proposal sketch revision is stale",
        ),
    )
    reasons = [message for accepted, message in checks if not accepted]

    mandatory = MANDATORY_GATES_BY_OPERATION[operation["op"]]
    for gate in sorted(mandatory):
        if gate not in trusted_context.gate_results:
            reasons.append(f"trusted gate result '{gate}' is missing")
        elif type(trusted_context.gate_results[gate]) is not bool:
            reasons.append(f"trusted gate result '{gate}' must be boolean")
        elif not trusted_context.gate_results[gate]:
            reasons.append(f"trusted gate '{gate}' failed")
    if reasons:
        return False, reasons

    consumed, replay_reason = replay_guard.consume(
        proposal["proposal_id"], proposal["idempotency_key"]
    )
    if not consumed:
        return False, [replay_reason]
    return True, []

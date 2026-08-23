"""Translate a validated CAD-assist deviation into ProposalContract v2.

CAD-assist session geometry is expressed in millimetres.  Proposal v2 carries
checked int64 nanometres.  Conversion uses decimal ``ROUND_HALF_EVEN`` at the
nanometre boundary: ``nm = round_half_even(decimal(mm) * 1_000_000)``.  Floats
are first converted through their shortest decimal string, avoiding binary-float
arithmetic in the rounding decision.  Non-finite values and int64 overflow fail.

The session's confirmed view binding lets this builder create an untrusted
proposal.  It does *not* authorize the coordinate-frame claim: the Design Studio
host must independently supply the mandatory ``frame_binding_valid`` gate result
when calling ``proposal.validate_proposal``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Any, Mapping, Sequence

from proposal import (
    INT64_MAX,
    INT64_MIN,
    ProposalBuildError,
    build_replace_line_constraints_proposal,
)


CAD_ASSIST_SCHEMA_V1 = "urn:design-studio:schema:cad-assist-session:1"
NM_PER_MM = Decimal(1_000_000)


def mm_to_nm_int64(value: Any) -> int:
    """Convert one finite decimal millimetre value to rounded int64 nanometres."""

    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ProposalBuildError("millimetre value must be a decimal-compatible number")
    try:
        decimal_mm = value if isinstance(value, Decimal) else Decimal(str(value))
        if not decimal_mm.is_finite():
            raise ProposalBuildError("millimetre value must be finite")
        rounded = (decimal_mm * NM_PER_MM).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
        nanometres = int(rounded)
    except (InvalidOperation, ValueError, OverflowError) as error:
        raise ProposalBuildError("millimetre value cannot be represented as nanometres") from error
    if not INT64_MIN <= nanometres <= INT64_MAX:
        raise ProposalBuildError("millimetre value overflows int64 nanometres")
    return nanometres


def mm_point_to_nm_int64(value: Any, *, path: str) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ProposalBuildError(f"{path} must contain exactly two millimetre coordinates")
    try:
        return [mm_to_nm_int64(value[0]), mm_to_nm_int64(value[1])]
    except ProposalBuildError as error:
        raise ProposalBuildError(f"{path}: {error}") from error


def _object(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ProposalBuildError(f"{path} must be an object")
    return value


def _array(value: Any, path: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ProposalBuildError(f"{path} must be an array")
    return value


def _find_record(records: Any, record_id: Any, path: str) -> Mapping[str, Any]:
    matches = [
        record
        for record in _array(records, path)
        if isinstance(record, dict) and record.get("id") == record_id
    ]
    if len(matches) != 1:
        raise ProposalBuildError(f"{path} must contain exactly one record with id {record_id!r}")
    return matches[0]


def build_proposal_from_cad_assist_deviation(
    session: Mapping[str, Any],
    deviation_id: str,
    *,
    sketch_id: str,
    host_geometry_revision: int,
    source: str,
    confidence: float,
    prompt_id: str | None = None,
) -> dict[str, Any]:
    """Build, but do not authorize, a typed line-constraint replacement.

    ``session`` is expected to have already passed the CAD-assist contract
    reader.  Essential cross-references are still checked here so this adapter
    cannot fabricate missing evidence or silently default a frame/target.
    """

    root = _object(session, "session")
    if root.get("schema") != CAD_ASSIST_SCHEMA_V1:
        raise ProposalBuildError(f"session.schema must equal {CAD_ASSIST_SCHEMA_V1!r}")
    if root.get("geometry_units") != "mm" or root.get("canonical_scale_nm_per_mm") != 1_000_000:
        raise ProposalBuildError("session must declare millimetres and 1,000,000 nm/mm")

    project = _object(root.get("project"), "session.project")
    source_asset = _object(root.get("source"), "session.source")
    deviation = _find_record(root.get("deviations"), deviation_id, "session.deviations")
    element = _find_record(root.get("elements"), deviation.get("element_id"), "session.elements")
    viewbox = _find_record(root.get("viewboxes"), element.get("viewbox_id"), "session.viewboxes")
    frame = _object(viewbox.get("frame"), "session.viewboxes[].frame")
    if frame.get("binding_status") != "confirmed":
        raise ProposalBuildError("CAD-assist view frame must be confirmed before proposing a correction")

    if deviation.get("cad_object_id") != project.get("object_id"):
        raise ProposalBuildError("deviation CAD object does not match session project object")
    if deviation.get("status") not in {"deviates", "within_tolerance"}:
        raise ProposalBuildError("only a measured matched deviation can produce a correction proposal")

    suggested = _object(deviation.get("suggested_target"), "session.deviation.suggested_target")
    if suggested.get("kind") != "replace_line_constraints":
        raise ProposalBuildError("deviation does not contain a replace_line_constraints target")
    target_start_nm = mm_point_to_nm_int64(
        suggested.get("target_start_view_mm"), path="suggested_target.target_start_view_mm"
    )
    target_end_nm = mm_point_to_nm_int64(
        suggested.get("target_end_view_mm"), path="suggested_target.target_end_view_mm"
    )

    target_ids = element.get("tracker_ids")
    if not isinstance(target_ids, list) or not target_ids:
        raise ProposalBuildError("evidence element must reference at least one stable target")

    return build_replace_line_constraints_proposal(
        document_id=project.get("document_id"),
        base_revision=project.get("base_revision"),
        base_sha256=project.get("base_sha256"),
        object_id=project.get("object_id"),
        sketch_id=sketch_id,
        host_geometry_id=deviation.get("host_geometry_id"),
        geometry_index=deviation.get("geometry_index"),
        host_geometry_revision=host_geometry_revision,
        sketch_revision=deviation.get("sketch_revision"),
        target_start_nm=target_start_nm,
        target_end_nm=target_end_nm,
        session_id=root.get("session_id"),
        session_revision=root.get("revision"),
        source_sha256=source_asset.get("sha256"),
        viewbox_id=viewbox.get("id"),
        element_id=element.get("id"),
        deviation_id=deviation.get("id"),
        target_ids=target_ids,
        source=source,
        confidence=confidence,
        prompt_id=prompt_id,
        proposal_id=deviation.get("proposal_id"),
    )

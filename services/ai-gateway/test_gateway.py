"""Gateway and fail-closed ProposalContract v2 tests (mock mode; no GPU)."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import struct

import httpx

from app import ChatCacheDescriptor, _cache_parameters, _canonical_prefix_sha256, app
from cad_assist_proposal import (
    build_proposal_from_cad_assist_deviation,
    mm_to_nm_int64,
)
from proposal import (
    INT64_MAX,
    MANDATORY_GATES_BY_OPERATION,
    OP_REPLACE_LINE_CONSTRAINTS,
    ProposalBuildError,
    ReplayGuard,
    TrustedMutationContext,
    build_replace_line_constraints_proposal,
    validate_proposal,
    validate_proposal_for_display,
    validate_proposal_structure,
)
from reference_form_jobs import ReferenceFormJobManager
from shape_backend import hunyuan_native_target_m
from token_accounting import aggregate_usage, normalize_usage


UUIDS = {
    "proposal": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "proposal2": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    "session": "11111111-1111-4111-8111-111111111111",
    "viewbox": "44444444-4444-4444-8444-444444444444",
    "element": "77777777-7777-4777-8777-777777777777",
    "deviation": "99999999-9999-4999-8999-999999999999",
    "target1": "55555555-5555-4555-8555-555555555555",
    "target2": "66666666-6666-4666-8666-666666666666",
}
DOC_HASH = "0123456789abcdef" * 4
SOURCE_HASH = "abcdef0123456789" * 4


def request(method: str, path: str, **kwargs):
    """Exercise the real ASGI boundary without a thread/event-loop shim."""

    async def call():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(call())


def test_chat_cache_disabled_is_backward_compatible():
    response = request(
        "POST", "/v1/chat", json={"messages": [{"role": "user", "content": "hello"}]}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["inference_evidence"]["cache_mode"] == "disabled"
    assert body["inference_evidence"]["cache_applied"] is False
    assert body["token_usage"]["status"] == "unavailable"
    assert body["token_usage"]["budget_policy"] == "observe_only_no_hard_cap"


def test_provider_reported_token_usage_and_cache_efficiency():
    first = normalize_usage(
        {"prompt_tokens": 8000, "completion_tokens": 1200, "total_tokens": 9200,
         "prompt_tokens_details": {"cached_tokens": 6000}},
        provider="vllm", model="fixture/model", purpose="cad_program",
        cache_requested=True,
    )
    second = normalize_usage(
        {"prompt_tokens": 2000, "completion_tokens": 500, "total_tokens": 2500,
         "prompt_tokens_details": {"cached_tokens": 0}},
        provider="vllm", model="fixture/model", purpose="engineering_review",
        cache_requested=False,
    )
    assert first["status"] == "measured"
    assert first["cache_reuse_ratio"] == 0.75
    assert first["uncached_input_tokens"] == 2000
    assert len(first["usage_digest"]) == 64
    summary = aggregate_usage([first, second])
    assert summary["total_tokens"] == 11700
    assert summary["cached_input_tokens"] == 6000
    assert summary["budget_policy"] == "observe_only_no_hard_cap"

    try:
        normalize_usage(
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 99},
            provider="vllm", model="fixture/model", purpose="invalid",
            cache_requested=False,
        )
    except ValueError as error:
        assert "must equal" in str(error)
    else:
        raise AssertionError("inconsistent provider usage must be rejected")


def _with_cache_key(value: str):
    class CacheKeyContext:
        def __enter__(self):
            self.previous = os.environ.get("DS_CACHE_SALT_KEY")
            os.environ["DS_CACHE_SALT_KEY"] = value

        def __exit__(self, _type, _value, _traceback):
            if self.previous is None:
                os.environ.pop("DS_CACHE_SALT_KEY", None)
            else:
                os.environ["DS_CACHE_SALT_KEY"] = self.previous

    return CacheKeyContext()


def test_scoped_chat_cache_requires_matching_canonical_prefix():
    messages = [
        {"role": "system", "content": "project rules"},
        {"role": "user", "content": "route this PCB"},
    ]
    digest = _canonical_prefix_sha256(messages, 1)
    with _with_cache_key("k" * 32):
        response = request(
            "POST",
            "/v1/chat",
            json={
                "messages": messages,
                "cache": {
                    "mode": "project",
                    "scope_id": "controller-demo",
                    "prefix_message_count": 1,
                    "prefix_sha256": digest,
                },
            },
        )
    assert response.status_code == 200
    evidence = response.json()["inference_evidence"]
    assert evidence["cache_applied"] is True
    assert evidence["prefix_sha256"] == digest
    assert evidence["scope_sha256"] == hashlib.sha256(
        b"controller-demo"
    ).hexdigest()
    assert "salt" not in json.dumps(evidence)

    bad = request(
        "POST",
        "/v1/chat",
        json={
            "messages": messages,
            "cache": {
                "mode": "project",
                "scope_id": "controller-demo",
                "prefix_message_count": 1,
                "prefix_sha256": "0" * 64,
            },
        },
    )
    assert bad.status_code == 422


def test_cache_salt_is_deterministic_and_scope_isolated():
    messages = [{"role": "system", "content": "same shared prefix"}]
    digest = _canonical_prefix_sha256(messages, 1)
    descriptor = ChatCacheDescriptor(
        mode="project",
        scope_id="project-a",
        prefix_message_count=1,
        prefix_sha256=digest,
    )
    with _with_cache_key("server-secret-" * 3):
        first, _ = _cache_parameters(messages, descriptor)
        repeat, _ = _cache_parameters(messages, descriptor)
        isolated, _ = _cache_parameters(
            messages,
            ChatCacheDescriptor(
                mode="project",
                scope_id="project-b",
                prefix_message_count=1,
                prefix_sha256=digest,
            ),
        )
    assert first == repeat
    assert first != isolated


def _proposal(
    *,
    proposal_id: str = UUIDS["proposal"],
    target_start_nm=None,
    target_end_nm=None,
):
    return build_replace_line_constraints_proposal(
        document_id="motor-controller-r1",
        base_revision=42,
        base_sha256=DOC_HASH,
        object_id="enclosure.main",
        sketch_id="sketch.front",
        host_geometry_id="sketch.front.edge-12",
        geometry_index=12,
        host_geometry_revision=7,
        sketch_revision=42,
        target_start_nm=[0, 0] if target_start_nm is None else target_start_nm,
        target_end_nm=[25_000_000, 0] if target_end_nm is None else target_end_nm,
        session_id=UUIDS["session"],
        session_revision=3,
        source_sha256=SOURCE_HASH,
        viewbox_id=UUIDS["viewbox"],
        element_id=UUIDS["element"],
        deviation_id=UUIDS["deviation"],
        target_ids=[UUIDS["target1"], UUIDS["target2"]],
        source="ai:test@1",
        confidence=0.9,
        proposal_id=proposal_id,
    )


def _all_gates():
    return {gate: True for gate in MANDATORY_GATES_BY_OPERATION[OP_REPLACE_LINE_CONSTRAINTS]}


def _trusted(proposal, *, gates=None, **overrides):
    document = proposal["document"]
    operation = proposal["operation"]
    values = {
        "document_id": document["id"],
        "document_revision": document["base_revision"],
        "document_sha256": document["base_sha256"],
        "object_id": operation["object_id"],
        "sketch_id": operation["sketch_id"],
        "host_geometry_id": operation["host_geometry_id"],
        "geometry_index": operation["geometry_index"],
        "host_geometry_revision": operation["host_geometry_revision"],
        "sketch_revision": operation["sketch_revision"],
        "gate_results": _all_gates() if gates is None else gates,
    }
    values.update(overrides)
    return TrustedMutationContext(**values)


def _assert_raises(exception_type, call):
    try:
        call()
    except exception_type:
        return
    raise AssertionError(f"expected {exception_type.__name__}")


def _endpoint_context():
    proposal = _proposal()
    return {
        **proposal["document"],
        "document_id": proposal["document"]["id"],
        "base_revision": proposal["document"]["base_revision"],
        "base_sha256": proposal["document"]["base_sha256"],
        **proposal["operation"],
        **proposal["evidence"],
        "proposal_id": proposal["proposal_id"],
    }


def _support_spec():
    document = {
        "document_id": "agent-keyboard-enclosure",
        "revision": 7,
        "sha256": DOC_HASH,
    }
    return {
        "schema": "design-studio.support-generation/1",
        "document": document,
        "placements": [{
            "schema": "design-studio.component-placement/1",
            "placement_id": "12345678-1234-4234-8234-123456789abc",
            "component_id": "component.encoder",
            "component_sha256": SOURCE_HASH,
            "document": dict(document),
            "transform": {
                "translation_mm": [30.0, 25.0, 12.0],
                "rotation_xyzw": [0.0, 0.0, 0.0, 1.0],
            },
            "coordinate_system": "enclosure_local",
            "snap": {
                "grid": True, "surface": True, "axis": True,
                "symmetry": False, "clearance": True,
            },
            "locks": {
                "position": True, "orientation": True,
                "surface_anchor": False,
            },
            "clearance_mm": 1.0,
        }],
        "design_volume_mm": {
            "min": [0.0, 0.0, 0.0],
            "max": [120.0, 70.0, 25.0],
        },
        "anchors": [{
            "id": "encoder-mount",
            "position_mm": [30.0, 25.0, 8.0],
            "kind": "mount",
        }],
        "forbidden_bounds_mm": [{
            "min": [25.0, 20.0, 6.0],
            "max": [35.0, 30.0, 18.0],
        }],
        "load_cases": [{
            "anchor_id": "encoder-mount",
            "force_n": [0.0, 0.0, -12.0],
        }],
        "manufacturing": {
            "process": "injection_molding",
            "minimum_wall_mm": 1.5,
            "minimum_rib_mm": 1.0,
            "clearance_mm": 0.5,
        },
        "seed": 19,
    }


def _reference_form_spec(six_views: bool = False):
    image_asset = {
        "asset_id": "asset.image.front",
        "path": "reference-form/assets/sha256/aa/" + "a" * 64 + ".png",
        "sha256": "a" * 64,
        "media_type": "image/png",
        "bytes": 128,
    }
    assets = [image_asset]
    views = [{
        "view_id": "view.front",
        "viewpoint": "front",
        "image_asset_id": image_asset["asset_id"],
        "mask_asset_id": None,
        "silhouette_asset_id": None,
        "approved": True,
        "projection": "perspective",
        "camera": None,
    }]
    if six_views:
        assets = []
        views = []
        for index, viewpoint in enumerate(
            ("front", "rear", "left", "right", "top", "bottom")
        ):
            digest = f"{index + 1:x}" * 64
            image = {
                "asset_id": f"asset.image.{viewpoint}",
                "path": f"reference-form/assets/{digest}.png",
                "sha256": digest,
                "media_type": "image/png",
                "bytes": 128,
            }
            silhouette_digest = f"{index + 7:x}" * 64
            silhouette = {
                "asset_id": f"asset.silhouette.{viewpoint}",
                "path": f"reference-form/assets/{silhouette_digest}.png",
                "sha256": silhouette_digest,
                "media_type": "image/png",
                "bytes": 64,
            }
            assets.extend((image, silhouette))
            views.append({
                "view_id": f"view.{viewpoint}",
                "viewpoint": viewpoint,
                "image_asset_id": image["asset_id"],
                "mask_asset_id": silhouette["asset_id"],
                "silhouette_asset_id": silhouette["asset_id"],
                "approved": True,
                "projection": "orthographic",
                "camera": {"calibrated": True},
            })
    return {
        "schema": "design-studio.reference-form/1",
        "reference_form_id": "keyboard-car-inspired",
        "revision": 3,
        "document": {
            "document_id": "keyboard-enclosure",
            "revision": 7,
            "sha256": DOC_HASH,
        },
        "assets": assets,
        "views": views,
        "target_bounds_mm": {
            "x": 330.0, "y": 145.0, "z": 38.0,
            "scale_source": "mixed_confirmed",
        },
        "known_dimensions_mm": [{
            "dimension_id": "overall-width",
            "axis": "x",
            "value_mm": 330.0,
            "source": "user_measurement",
        }],
        "curves": [],
        "motifs": [],
        "engineering_controls": {
            "section_curves": [],
            "symmetry_planes": ["yz"],
            "keep_points": [],
            "avoid_points": [],
            "protected_volumes": [],
            "functional_regions": [],
            "connector_access": [],
            "function_over_style": True,
        },
        "seams": [],
        "candidate_selection": {
            "selected_candidate_sha256": None,
            "accepted": False,
            "accepted_utc": None,
        },
        "provenance": {
            "source": "synthetic car-inspired keyboard fixture",
            "license_status": "approved_for_project",
            "user_intent_text": "Preserve the roofline as editable motif evidence.",
        },
    }
def test_health_mock():
    response = request("GET", "/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["mock"] is True
    assert body["gpu"] == "none"


def test_propose_returns_structural_v2_but_never_host_authorization():
    response = request(
        "POST",
        "/v1/propose",
        json={"kind": "geometry", "context": _endpoint_context()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["structurally_valid"] is True
    assert body["commit_authorized"] is False
    proposal = body["proposal"]
    assert proposal["schema"] == "design-studio.proposal/2"
    ok, reasons = validate_proposal_structure(proposal)
    assert ok, reasons
    ok, reasons = validate_proposal(proposal)
    assert not ok and any("trusted host" in reason for reason in reasons)


def test_propose_rejects_untyped_kinds_and_incomplete_context():
    response = request("POST", "/v1/propose", json={"kind": "advice", "context": {}})
    assert response.status_code == 422
    response = request("POST", "/v1/propose", json={"kind": "geometry", "context": {}})
    assert response.status_code == 422


def test_chat_mock_echo():
    response = request("POST", "/v1/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 200
    assert "hi" in response.json()["content"]


def test_support_job_is_revision_bound_idempotent_and_non_authoritative():
    spec = _support_spec()
    response = request(
        "POST",
        "/v1/support-jobs",
        json={"spec": spec, "idempotency_key": "keyboard-encoder-support-v1"},
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["status"] == "completed"
    assert job["document"] == spec["document"]

    repeated = request(
        "POST",
        "/v1/support-jobs",
        json={"spec": spec, "idempotency_key": "keyboard-encoder-support-v1"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["job_id"] == job["job_id"]

    status = request("GET", f"/v1/support-jobs/{job['job_id']}")
    assert status.status_code == 200
    result = request("GET", f"/v1/support-jobs/{job['job_id']}/result")
    assert result.status_code == 200
    graph = result.json()
    assert graph["schema"] == "design-studio.support-result/1"
    assert graph["document"] == spec["document"]
    assert graph["generator"]["authoritative_geometry"] is False
    assert graph["required_host_checks"]
    assert len(graph["ribs"]) == 1


def test_support_job_rejects_stale_or_unlocked_placements():
    stale = _support_spec()
    stale["placements"][0]["document"]["revision"] += 1
    response = request("POST", "/v1/support-jobs", json={"spec": stale})
    assert response.status_code == 422
    assert "stale document binding" in response.text

    unlocked = _support_spec()
    unlocked["placements"][0]["locks"]["orientation"] = False
    response = request("POST", "/v1/support-jobs", json={"spec": unlocked})
    assert response.status_code == 422
    assert "lock position and orientation" in response.text


def test_support_job_missing_result_fails_closed():
    response = request("GET", "/v1/support-jobs/00000000-0000-4000-8000-000000000000/result")
    assert response.status_code == 404


def test_reference_form_job_routes_single_view_and_returns_exact_seeded_candidates():
    spec = _reference_form_spec()
    response = request(
        "POST", "/v1/reference-form-jobs",
        json={
            "spec": spec,
            "active_revision": 3,
            "idempotency_key": "keyboard-reference-r3",
        },
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["status"] == "completed"
    assert job["backend"] == "hunyuan3d-2.1"
    assert len(job["seeds"]) == 4 and len(set(job["seeds"])) == 4

    result_response = request(
        "GET", f"/v1/reference-form-jobs/{job['job_id']}/result"
    )
    assert result_response.status_code == 200
    result = result_response.json()
    assert result["schema"] == "design-studio.reference-form-result/1"
    assert result["generator"]["text_conditioning"] == "recorded_not_conditioned"
    assert len(result["candidates"]) == 4
    first = result["candidates"][0]
    assert first["dimensions_mm"] == [330.0, 145.0, 38.0]
    assert first["bounding_box_error_mm"] == [0.0, 0.0, 0.0]
    provisional = {
        item["viewpoint"]: item["provisional"]
        for item in first["orthographic_previews"]
    }
    assert provisional["front"] is False
    assert all(provisional[name] for name in provisional if name != "front")
    artifact = request("GET", first["artifact_url"])
    assert artifact.status_code == 200
    assert __import__("hashlib").sha256(artifact.content).hexdigest() == first["sha256"]

    repeated = request(
        "POST", "/v1/reference-form-jobs",
        json={
            "spec": spec,
            "active_revision": 3,
            "idempotency_key": "keyboard-reference-r3",
        },
    )
    assert repeated.status_code == 200
    assert repeated.json()["job_id"] == job["job_id"]


def test_reference_form_six_approved_silhouettes_route_to_omni():
    spec = _reference_form_spec(six_views=True)
    response = request(
        "POST", "/v1/reference-form-jobs",
        json={"spec": spec, "active_revision": spec["revision"]},
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["backend"] == "hunyuan3d-omni"
    result = request(
        "GET", f"/v1/reference-form-jobs/{job['job_id']}/result"
    ).json()
    candidate = result["candidates"][0]
    assert len(candidate["scores"]["silhouette_iou"]) == 6
    assert min(candidate["scores"]["silhouette_iou"].values()) >= 0.90
    assert not any(
        preview["provisional"] for preview in candidate["orthographic_previews"]
    )


def test_reference_form_rejects_stale_revision_paths_and_pixel_scale():
    spec = _reference_form_spec()
    stale = request(
        "POST", "/v1/reference-form-jobs",
        json={"spec": spec, "active_revision": spec["revision"] + 1},
    )
    assert stale.status_code == 422
    assert "stale reference-form revision" in stale.text

    escaped = copy.deepcopy(spec)
    escaped["assets"][0]["path"] = "../outside.png"
    response = request(
        "POST", "/v1/reference-form-jobs",
        json={"spec": escaped, "active_revision": escaped["revision"]},
    )
    assert response.status_code == 422
    assert "inside the workspace" in response.text

    pixels = copy.deepcopy(spec)
    pixels["target_bounds_mm"]["scale_source"] = "pixels"
    response = request(
        "POST", "/v1/reference-form-jobs",
        json={"spec": pixels, "active_revision": pixels["revision"]},
    )
    assert response.status_code == 422
    assert "pixel-inferred" in response.text


def test_reference_form_queued_job_can_be_cancelled():
    manager = ReferenceFormJobManager(mock=False)
    spec = _reference_form_spec()
    job, created = manager.create(spec, spec["revision"], None)
    assert created and job["status"] == "queued"
    cancelled = manager.cancel(job["job_id"])
    assert cancelled["status"] == "cancelled"
    status, result = manager.result(job["job_id"])
    assert status == "cancelled" and result is None


def test_surrogate_has_error_bound():
    response = request("POST", "/v1/surrogate", json={"model_id": "stress_v1", "inputs": {}})
    assert "error_bound" in response.json()


def test_generate3d_mock_returns_dimensioned_binary_stl():
    response = request(
        "POST", "/v1/generate3d", json={"target_dimensions_mm": [10.0, 5.0, 2.0], "seed": 7}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mock"] is True
    assert body["format"] == "stl"
    assert body["dimensions_unit"] == "mm"
    assert body["mesh_coordinate_unit"] == "mm"
    assert body["dimensions_mm"] == [10.0, 5.0, 2.0]
    stl = base64.b64decode(body["mesh_base64"])
    assert struct.unpack_from("<I", stl, 80)[0] == 12
    assert len(stl) == 84 + 12 * 50


def test_generate3d_rejects_invalid_dimensions():
    response = request("POST", "/v1/generate3d", json={"target_dimensions_mm": [10.0, 0.0, 2.0]})
    assert response.status_code == 422


def test_vehicle_dimensions_map_to_hunyuan_native_axes():
    assert hunyuan_native_target_m((200.0, 89.34, 57.35)) == (
        0.08934, 0.05735, 0.2,
    )


def test_typed_proposal_authorizes_once_with_all_trusted_results():
    proposal = _proposal()
    guard = ReplayGuard()
    ok, reasons = validate_proposal(proposal, trusted_context=_trusted(proposal), replay_guard=guard)
    assert ok, reasons
    ok, reasons = validate_proposal(proposal, trusted_context=_trusted(proposal), replay_guard=guard)
    assert not ok and any("replay" in reason for reason in reasons)


def test_idempotency_rejects_equivalent_proposal_with_new_uuid():
    first = _proposal(proposal_id=UUIDS["proposal"])
    second = _proposal(proposal_id=UUIDS["proposal2"])
    assert first["proposal_id"] != second["proposal_id"]
    assert first["idempotency_key"] == second["idempotency_key"]
    guard = ReplayGuard()
    assert validate_proposal(first, trusted_context=_trusted(first), replay_guard=guard)[0]
    ok, reasons = validate_proposal(second, trusted_context=_trusted(second), replay_guard=guard)
    assert not ok and any("idempotency_key" in reason for reason in reasons)


def test_missing_or_failed_trusted_gate_results_reject():
    proposal = _proposal()
    gates = _all_gates()
    missing = sorted(gates)[0]
    del gates[missing]
    ok, reasons = validate_proposal(
        proposal, trusted_context=_trusted(proposal, gates=gates), replay_guard=ReplayGuard()
    )
    assert not ok and any(missing in reason and "missing" in reason for reason in reasons)

    gates = _all_gates()
    failed = sorted(gates)[0]
    gates[failed] = False
    ok, reasons = validate_proposal(
        proposal, trusted_context=_trusted(proposal, gates=gates), replay_guard=ReplayGuard()
    )
    assert not ok and any(failed in reason and "failed" in reason for reason in reasons)


def test_stale_revision_wrong_document_and_host_revisions_reject():
    proposal = _proposal()
    cases = [
        ({"document_id": "different-document"}, "wrong document"),
        ({"document_revision": 43}, "base revision is stale"),
        ({"document_sha256": "0" * 64}, "document hash is stale"),
        ({"host_geometry_revision": 8}, "host geometry revision is stale"),
        ({"sketch_revision": 43}, "sketch revision is stale"),
    ]
    for overrides, expected in cases:
        ok, reasons = validate_proposal(
            proposal,
            trusted_context=_trusted(proposal, **overrides),
            replay_guard=ReplayGuard(),
        )
        assert not ok and any(expected in reason for reason in reasons), (overrides, reasons)


def test_delete_everything_and_unknown_members_are_rejected():
    proposal = _proposal()
    attack = copy.deepcopy(proposal)
    attack["payload"] = {"delete_everything": True}
    ok, reasons = validate_proposal_structure(attack)
    assert not ok and any("payload" in reason and "not allowed" in reason for reason in reasons)

    attack = copy.deepcopy(proposal)
    attack["operation"] = {"op": "delete_everything"}
    ok, reasons = validate_proposal_structure(attack)
    assert not ok and any("replace_line_constraints" in reason for reason in reasons)


def test_int64_overflow_and_degenerate_lines_reject():
    _assert_raises(
        ProposalBuildError,
        lambda: _proposal(target_start_nm=[INT64_MAX + 1, 0]),
    )
    proposal = _proposal()
    proposal["operation"]["target_start_nm"] = [INT64_MAX + 1, 0]
    ok, reasons = validate_proposal_structure(proposal)
    assert not ok and any("int64" in reason for reason in reasons)

    proposal = _proposal()
    proposal["operation"]["target_end_nm"] = proposal["operation"]["target_start_nm"]
    ok, reasons = validate_proposal_structure(proposal)
    assert not ok and any("degenerate" in reason for reason in reasons)


def test_v1_is_display_only_and_never_commit_authorized():
    legacy = {
        "schema": "design-studio.proposal/1",
        "kind": "geometry",
        "payload": {"delete_everything": True},
        "provenance": {"source": "ai:legacy", "confidence": 0.9},
        "must_pass": ["units"],
    }
    display_ok, warnings = validate_proposal_for_display(legacy)
    assert display_ok and any("display-only" in warning for warning in warnings)
    commit_ok, reasons = validate_proposal(legacy, replay_guard=ReplayGuard())
    assert not commit_ok and any("display-only" in reason for reason in reasons)


def test_deterministic_mm_to_nm_rounding_and_overflow():
    assert mm_to_nm_int64("25.0") == 25_000_000
    assert mm_to_nm_int64("0.0000005") == 0       # half to even
    assert mm_to_nm_int64("0.0000015") == 2       # half to even
    assert mm_to_nm_int64("-0.0000015") == -2
    _assert_raises(ProposalBuildError, lambda: mm_to_nm_int64("NaN"))
    _assert_raises(ProposalBuildError, lambda: mm_to_nm_int64("9223372036854.775808"))


def test_cad_assist_deviation_builds_stable_evidenced_proposal():
    repo_root = Path(__file__).resolve().parents[2]
    fixture = repo_root / "app/QtDesignStudio/tests/cad_assist_contract/fixtures/valid-session.json"
    session = json.loads(fixture.read_text())
    proposal = build_proposal_from_cad_assist_deviation(
        session,
        UUIDS["deviation"],
        sketch_id="sketch.front",
        host_geometry_revision=7,
        source="human:cad-assist-review",
        confidence=1.0,
    )
    assert proposal["operation"]["target_start_nm"] == [0, 0]
    assert proposal["operation"]["target_end_nm"] == [25_000_000, 0]
    assert proposal["evidence"]["session_id"] == UUIDS["session"]
    assert proposal["evidence"]["target_ids"] == [UUIDS["target1"], UUIDS["target2"]]
    ok, reasons = validate_proposal_structure(proposal)
    assert ok, reasons

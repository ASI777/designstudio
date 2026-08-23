#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import hashlib
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "freecad" / "DesignStudioWorkbench"))

from DesignStudio.freecad_adapter import component_world_transform  # noqa: E402
from DesignStudio.glb_mesh import read_glb  # noqa: E402
from swarm.agents.schema_validator import validate_or_raise  # noqa: E402
from swarm.memory.component_3d_assets import (  # noqa: E402
    CARDINAL_VIEWS, materialize_component_asset, meshes_from_component,
    render_six_views, submit_hunyuan_omni, write_glb,
)
import swarm.memory.apply_to_board as board_apply  # noqa: E402
import swarm.memory.component_3d_assets as assets_module  # noqa: E402


def component(complex_package: bool = False) -> dict:
    method = "six_view_hunyuan" if complex_package else "parametric"
    complexity = "complex" if complex_package else "simple"
    return {
        "schema": "design-studio.component/2",
        "component": {"manufacturer": "Fixture", "mpn": "DS-3D-1",
                      "category": "connector" if complex_package else "transistor"},
        "symbol": {"ref_des_prefix": "U", "pins": [
            {"number": "1", "name": "A", "electrical_type": "passive"},
            {"number": "2", "name": "B", "electrical_type": "passive"}]},
        "footprint": {"name": "DS_3D_FIXTURE", "mount": "smd",
                      "body": {"width_mm": 3.0, "length_mm": 4.0, "height_mm": 1.2},
                      "pads": [
                          {"number": "1", "x_mm": -1.5, "y_mm": 0,
                           "width_mm": 1.0, "height_mm": 1.4},
                          {"number": "2", "x_mm": 1.5, "y_mm": 0,
                           "width_mm": 1.0, "height_mm": 1.4}]},
        "package_3d": {"height_mm": 1.2, "standoff_mm": 0.1, "shape": "box",
                       "construction": {
                           "author": "gpt-5.6-luna-xhigh", "method": method,
                           "complexity": complexity,
                           "complexity_reasons": ["irregular shell"] if complex_package else [],
                           "datasheet_pages": [4], "assumptions": [],
                           "hunyuan_prompt": "Preserve the connector shell and two contacts.",
                           "primitives": [
                               {"id": "body", "role": "body", "shape": "box",
                                "center_mm": [0, 0, 0.7], "size_mm": [3, 4, 1.2],
                                "rotation_deg_xyz": [0, 0, 0],
                                "color_rgba": [0.05, 0.07, 0.09, 1]},
                               {"id": "contact-1", "role": "lead", "shape": "box",
                                "center_mm": [-1.5, 0, 0.1], "size_mm": [1, 1.4, 0.2],
                                "rotation_deg_xyz": [0, 0, 0],
                                "color_rgba": [0.7, 0.72, 0.75, 1]},
                               {"id": "contact-2", "role": "lead", "shape": "box",
                                "center_mm": [1.5, 0, 0.1], "size_mm": [1, 1.4, 0.2],
                                "rotation_deg_xyz": [0, 0, 0],
                                "color_rgba": [0.7, 0.72, 0.75, 1]}]}}
    }


def test_simple_luna_asset() -> None:
    with tempfile.TemporaryDirectory(prefix="ds-component-3d-") as raw:
        item = component()
        asset = materialize_component_asset(item, asset_root=Path(raw))
        assert asset["status"] == "ready"
        assert asset["generator"] == "gpt-5.6-luna-xhigh-parametric"
        assert Path(asset["asset_uri"]).read_bytes()[:4] == b"glTF"
        assert asset["dimensions_mm"] == {"width": 4.0, "depth": 4.0, "height": 1.3}
        meshes = read_glb(asset["asset_uri"])
        assert len(meshes) == 3 and sum(len(mesh["triangles"]) for mesh in meshes) == 36
        validate_or_raise(item)


def test_complex_six_view_rocm_fallback() -> None:
    calls = []

    def fake_submitter(**kwargs):
        calls.append(kwargs)
        proxy = Path(kwargs["views"][0]["image_uri"]).parent.parent / "datasheet-parametric.glb"
        return proxy.read_bytes(), {"job_id": "test-job", "model": "Hunyuan3D-Omni",
                                    "runtime": {"accelerator": "MI300X", "rocm": "6.x"}}

    with tempfile.TemporaryDirectory(prefix="ds-component-3d-") as raw:
        item = component(complex_package=True)
        asset = materialize_component_asset(
            item, asset_root=Path(raw), cloud_submitter=fake_submitter)
        assert asset["status"] == "ready"
        assert asset["generator"] == "hunyuan3d-omni-2.1-amd-rocm"
        assert len(calls) == 1
        assert tuple(view["view"] for view in calls[0]["views"]) == CARDINAL_VIEWS
        assert len(asset["six_views"]) == 6
        assert all(Path(view["image_uri"]).is_file() and Path(view["mask_uri"]).is_file()
                   for view in asset["six_views"])
        validate_or_raise(item)


def test_complex_offline_keeps_visible_proxy() -> None:
    old_url = os.environ.pop("DESIGNSTUDIO_HUNYUAN_OMNI_URL", None)
    old_token = os.environ.pop("DESIGNSTUDIO_HUNYUAN_OMNI_TOKEN", None)
    try:
        with tempfile.TemporaryDirectory(prefix="ds-component-3d-") as raw:
            asset = materialize_component_asset(
                component(complex_package=True), asset_root=Path(raw))
            assert asset["status"] == "cloud_pending"
            assert Path(asset["asset_uri"]).is_file() and len(asset["six_views"]) == 6
    finally:
        if old_url is not None: os.environ["DESIGNSTUDIO_HUNYUAN_OMNI_URL"] = old_url
        if old_token is not None: os.environ["DESIGNSTUDIO_HUNYUAN_OMNI_TOKEN"] = old_token


def test_desktop_client_embeds_six_images_and_downloads_glb() -> None:
    with tempfile.TemporaryDirectory(prefix="ds-component-3d-client-") as raw:
        root = Path(raw)
        meshes = meshes_from_component(component(complex_package=True))
        glb_path = root / "proxy.glb"
        dimensions = write_glb(meshes, glb_path, generator="client-test")
        views = render_six_views(meshes, root / "views", resolution=128)
        glb = glb_path.read_bytes()

        submitted = []

        def fake_http(url, *, token, payload=None, timeout=30):
            assert token == "client-token" and timeout > 0
            if payload is not None:
                submitted.append(payload)
                references = payload["input_package"]["reference_images"]
                assert {reference["view"] for reference in references} == set(CARDINAL_VIEWS)
                assert all(reference.get("image_base64") and reference.get("mask_base64")
                           and reference.get("image_sha256") and reference.get("mask_sha256")
                           and "image_url" not in reference for reference in references)
                return {"job_id": "11111111-1111-4111-8111-111111111111",
                        "status": "queued", "input_digest": "a" * 64,
                        "created_utc": "2026-01-01T00:00:00Z"}
            if url.endswith("/v1/jobs/11111111-1111-4111-8111-111111111111"):
                return {"job_id": "11111111-1111-4111-8111-111111111111",
                        "status": "succeeded", "input_digest": "a" * 64,
                        "artifact_manifest_url": "/manifest.json"}
            assert url.endswith("/manifest.json")
            return {"model": "Tencent-Hunyuan/Hunyuan3D-Omni",
                    "runtime": {"accelerator": "MI300X", "rocm": "6.x"},
                    "candidates": [{"artifact_url": "/candidate.glb",
                                    "sha256": hashlib.sha256(glb).hexdigest()}]}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return glb

        old_http, old_urlopen = assets_module._http_json, assets_module.urlopen
        assets_module._http_json = fake_http
        assets_module.urlopen = lambda request, timeout=60: Response()
        try:
            downloaded, evidence = submit_hunyuan_omni(
                views=views, dimensions=dimensions, prompt="Exact complex package",
                endpoint="https://rocm.example.test", token="client-token",
                timeout=5)
        finally:
            assets_module._http_json, assets_module.urlopen = old_http, old_urlopen
        assert downloaded == glb
        assert evidence["job_id"] == "11111111-1111-4111-8111-111111111111"
        assert submitted[0]["candidate_count"] == 1


def test_top_bottom_instance_transforms() -> None:
    frame = {"origin_mm": [10, 20, 30], "x_axis": [1, 0, 0],
             "y_axis": [0, 1, 0], "z_axis": [0, 0, 1]}
    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    top = component_world_transform(frame, 5, 7, 0, identity,
                                    side=0, board_thickness_mm=1.6)
    bottom = component_world_transform(frame, 5, 7, 0, identity,
                                       side=1, board_thickness_mm=1.6)
    assert (top[3], top[7], top[11]) == (15, 27, 31.6)
    assert (bottom[3], bottom[7], bottom[11]) == (15, 27, 30)
    assert top[10] == 1 and bottom[10] == -1

    def mapped(matrix, point):
        x, y, z = point
        return tuple(round(matrix[row * 4] * x + matrix[row * 4 + 1] * y
                           + matrix[row * 4 + 2] * z + matrix[row * 4 + 3], 6)
                     for row in range(3))

    rotated_top = component_world_transform(frame, 5, 7, 90, identity,
                                            side=0, board_thickness_mm=1.6)
    rotated_bottom = component_world_transform(frame, 5, 7, 90, identity,
                                               side=1, board_thickness_mm=1.6)
    assert mapped(rotated_top, (2, 1, 0)) == (14, 29, 31.6)
    assert mapped(rotated_bottom, (2, 1, 0)) == (16, 29, 30)


def test_asset_follows_placed_footprint_into_project() -> None:
    with tempfile.TemporaryDirectory(prefix="ds-component-3d-") as raw:
        item = component()
        asset = materialize_component_asset(item, asset_root=Path(raw))
        old_component = board_apply.resolve_component
        old_binding = board_apply.resolve_bound_component
        board_apply.resolve_component = lambda mpn: item if mpn == "DS-3D-1" else None
        board_apply.resolve_bound_component = lambda _mpn: None
        try:
            project = board_apply.build_dsproj(
                [{"ref": "U1", "mpn": "DS-3D-1"}],
                [{"ref": "U1", "pin": "1", "net": "A"},
                 {"ref": "U1", "pin": "2", "net": "B"}])
        finally:
            board_apply.resolve_component = old_component
            board_apply.resolve_bound_component = old_binding
        placed = project["footprints"][0]
        assert placed["asset_3d"]["sha256"] == asset["sha256"]
        assert placed["x_mm"] >= 0 and placed["y_mm"] >= 0
        assert project["board_width_mm"] >= 4 and project["board_height_mm"] >= 4


if __name__ == "__main__":
    test_simple_luna_asset()
    test_complex_six_view_rocm_fallback()
    test_complex_offline_keeps_visible_proxy()
    test_desktop_client_embeds_six_images_and_downloads_glb()
    test_top_bottom_instance_transforms()
    test_asset_follows_placed_footprint_into_project()
    print("Component 3D asset tests passed")

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile


MODULE = (
    Path(__file__).resolve().parents[1]
    / "DesignStudio"
    / "reference_form.py"
)
SPEC = importlib.util.spec_from_file_location("reference_form", MODULE)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def image(path: Path, marker: bytes):
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + marker)


def store_fixture(directory: Path):
    workspace = directory / "keyboard.dsworkspace"
    (workspace / "mechanical").mkdir(parents=True)
    (workspace / "manifest.json").write_text("{}", encoding="utf-8")
    return module.ReferenceFormStore(workspace, {
        "document_id": "keyboard",
        "revision": 4,
        "sha256": "2" * 64,
    })


def add_reference(store, source: Path, viewpoint: str):
    return store.import_reference(
        source,
        viewpoint=viewpoint,
        source="synthetic car-inspired keyboard fixture",
        license_status="approved_for_project",
        projection="orthographic",
        known_axis="x",
        known_value_mm=330.0,
        target_bounds_mm=(330.0, 145.0, 38.0),
        symmetry_planes=["yz"],
    )


def test_digest_assets_revisions_and_undo_redo():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = store_fixture(root)
        front = root / "front.png"
        rear = root / "rear.png"
        image(front, b"front")
        image(rear, b"rear")
        first = add_reference(store, front, "front")
        assert first["schema"] == "design-studio.reference-form/1"
        assert first["revision"] == 1
        assert first["known_dimensions_mm"][0]["source"] == "user_measurement"
        store.verify_assets()

        duplicate = store.add_asset(front)
        assert duplicate == first["assets"][0]
        second = add_reference(store, rear, "rear")
        assert second["revision"] == 2
        assert {view["viewpoint"] for view in second["views"]} == {"front", "rear"}
        assert store.undo()["revision"] == 1
        assert store.redo()["revision"] == 2


def test_stale_preview_and_asset_tamper_fail_closed():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = store_fixture(root)
        front = root / "front.png"
        image(front, b"front")
        active = add_reference(store, front, "front")
        preview = store.create_preview(
            base_revision=active["revision"],
            operation="ai_depth_preview",
            replacement=active,
        )
        replacement = dict(active)
        replacement["provenance"] = dict(active["provenance"])
        replacement["provenance"]["user_intent_text"] = "new accepted operation"
        competing = store.create_preview(
            base_revision=active["revision"],
            operation="competing_preview",
            replacement=replacement,
        )
        store.accept_preview(competing["preview_id"])
        try:
            store.accept_preview(preview["preview_id"])
            raise AssertionError("stale preview was accepted")
        except module.ReferenceFormError:
            pass

        current = store.active()
        asset_path = store.workspace_root / current["assets"][0]["path"]
        asset_path.write_bytes(asset_path.read_bytes() + b"tampered")
        try:
            store.verify_assets()
            raise AssertionError("tampered asset was accepted")
        except module.ReferenceFormError:
            pass


def test_six_approved_silhouettes_build_revision_bound_visual_hull():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = store_fixture(root)
        for viewpoint in module.VIEWPOINTS:
            source = root / f"{viewpoint}.png"
            mask = root / f"{viewpoint}-mask.png"
            silhouette = root / f"{viewpoint}-silhouette.png"
            image(source, viewpoint.encode())
            image(mask, b"mask-" + viewpoint.encode())
            image(silhouette, b"silhouette-" + viewpoint.encode())
            active = add_reference(store, source, viewpoint)
            view_id = next(
                view["view_id"] for view in active["views"]
                if view["viewpoint"] == viewpoint
            )
            preview = store.attach_corrected_mask(
                view_id, mask, silhouette, [],
                base_revision=active["revision"],
            )
            store.accept_preview(preview["preview_id"])
        control = store.visual_hull_control()
        assert control["schema"] == "design-studio.visual-hull-control/1"
        assert set(control["views"]) == set(module.VIEWPOINTS)
        assert control["reference_form_revision"] == store.active()["revision"]
        material = dict(control)
        claimed = material.pop("sha256")
        assert claimed == hashlib.sha256(canonical(material)).hexdigest()


def test_candidate_acceptance_requires_iou_and_explicit_preview_acceptance():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = store_fixture(root)
        front = root / "front.png"
        mask = root / "front-mask.png"
        silhouette = root / "front-silhouette.png"
        image(front, b"front")
        image(mask, b"mask")
        image(silhouette, b"silhouette")
        active = add_reference(store, front, "front")
        preview = store.attach_corrected_mask(
            active["views"][0]["view_id"], mask, silhouette, [],
            base_revision=active["revision"],
        )
        active = store.accept_preview(preview["preview_id"])

        mesh = root / "candidate.stl"
        mesh.write_bytes(b"candidate-mesh")
        mesh_sha = hashlib.sha256(mesh.read_bytes()).hexdigest()
        result = {
            "schema": "design-studio.reference-form-result/1",
            "reference_form_id": active["reference_form_id"],
            "reference_form_revision": active["revision"],
            "candidates": [{
                "index": 0,
                "sha256": mesh_sha,
                "bounding_box_error_mm": [0.0, 0.0, 0.0],
                "scores": {"mesh_valid": True, "silhouette_iou": {"front": 0.89}},
            }],
        }
        result["result_sha256"] = hashlib.sha256(canonical(result)).hexdigest()
        try:
            store.candidate_acceptance_preview(result, 0, mesh)
            raise AssertionError("below-threshold silhouette was accepted")
        except module.ReferenceFormError:
            pass

        result_without_digest = dict(result)
        result_without_digest.pop("result_sha256")
        result_without_digest["candidates"][0]["scores"]["silhouette_iou"]["front"] = 0.95
        result = result_without_digest
        result["result_sha256"] = hashlib.sha256(canonical(result_without_digest)).hexdigest()
        selection = store.candidate_acceptance_preview(result, 0, mesh)
        assert store.active()["candidate_selection"]["accepted"] is False
        accepted = store.accept_preview(selection["preview_id"])
        assert accepted["candidate_selection"]["accepted"] is True
        assert accepted["candidate_selection"]["selected_candidate_sha256"] == mesh_sha


def test_approved_mask_extracts_bounded_editable_silhouette_curve():
    try:
        from PIL import Image, ImageDraw
    except ModuleNotFoundError:
        # The dependency-light contract interpreter may not include Pillow.
        # FreeCAD's packaged runtime and the gateway image do; the remaining
        # store/revision tests intentionally stay standard-library only.
        return

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "silhouette.png"
        image_value = Image.new("L", (160, 100), 0)
        draw = ImageDraw.Draw(image_value)
        draw.rounded_rectangle((20, 15, 140, 85), radius=18, fill=255)
        image_value.save(path)
        curves = module.extract_silhouette_curve(
            path, "view.front", maximum_points=128
        )
        curve = curves[0]
        assert curve["label"] == "silhouette"
        assert curve["approved"] is True
        assert 4 <= len(curve["points"]) <= 129
        assert curve["points"][0] == curve["points"][-1]


if __name__ == "__main__":
    test_digest_assets_revisions_and_undo_redo()
    test_stale_preview_and_asset_tamper_fail_closed()
    test_six_approved_silhouettes_build_revision_bound_visual_hull()
    test_candidate_acceptance_requires_iou_and_explicit_preview_acceptance()
    test_approved_mask_extracts_bounded_editable_silhouette_curve()
    print("REFERENCE_FORM_OK")

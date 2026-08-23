#!/usr/bin/env python3
"""Render deterministic six-view silhouettes from the seed-100 bootstrap GLB."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


VIEWS = ("front", "rear", "left", "right", "top", "bottom")


def worker_args() -> argparse.Namespace:
    raw = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--mesh", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=1024)
    parser.add_argument(
        "--vehicle-axis-layout",
        choices=("bootstrap", "glb-y-up"),
        default="bootstrap",
    )
    return parser.parse_args(raw)


def blender_worker() -> None:
    args = worker_args()
    import bpy
    from mathutils import Matrix, Vector

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.mesh.resolve()))
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = args.resolution
    scene.render.resolution_y = args.resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "SINGLE"
    scene.display.shading.single_color = (0.32, 0.38, 0.42)
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.display.shading.cavity_type = "WORLD"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "Medium High Contrast"

    mesh_objects = [obj for obj in scene.objects if obj.type == "MESH"]
    if not mesh_objects:
        raise RuntimeError("GLB contains no mesh objects")
    corners = []
    face_count = 0
    vertex_count = 0
    for obj in mesh_objects:
        corners.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
        face_count += len(obj.data.polygons)
        vertex_count += len(obj.data.vertices)
    minimum = Vector(tuple(min(point[i] for point in corners) for i in range(3)))
    maximum = Vector(tuple(max(point[i] for point in corners) for i in range(3)))
    centre = (minimum + maximum) / 2
    extents = maximum - minimum

    camera_data = bpy.data.cameras.new("Bootstrap orthographic camera")
    camera_data.type = "ORTHO"
    camera_data.clip_start = max(min(extents) * 0.001, 0.000001)
    camera_data.clip_end = max(extents) * 10
    camera = bpy.data.objects.new("Bootstrap orthographic camera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera

    # Bootstrap and trimesh-exported GLBs arrive in Blender with different
    # up-axis conversions.  Express both in the same vehicle view contract.
    if args.vehicle_axis_layout == "bootstrap":
        width, length, height = 0, 1, 2
    else:
        width, length, height = 0, 2, 1

    if args.vehicle_axis_layout == "bootstrap":
        bases = {
            "front": ((0, 1, 0), (1, 0, 0), (0, 0, 1), (0, 2)),
            "rear": ((0, -1, 0), (-1, 0, 0), (0, 0, 1), (0, 2)),
            "left": ((1, 0, 0), (0, -1, 0), (0, 0, 1), (1, 2)),
            "right": ((-1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 2)),
            "top": ((0, 0, -1), (0, 1, 0), (-1, 0, 0), (1, 0)),
            "bottom": ((0, 0, 1), (0, -1, 0), (-1, 0, 0), (1, 0)),
        }
    else:
        # trimesh exports glTF as Y-up. Blender imports the source
        # X=width/Y=depth/Z=height coordinates as X=width/Y=height/Z=-depth.
        # These right-handed bases retain the approved image orientation.
        bases = {
            "front": ((0, 0, -1), (1, 0, 0), (0, 1, 0), (0, 1)),
            "rear": ((0, 0, 1), (-1, 0, 0), (0, 1, 0), (0, 1)),
            "left": ((-1, 0, 0), (0, 0, -1), (0, 1, 0), (2, 1)),
            "right": ((1, 0, 0), (0, 0, 1), (0, 1, 0), (2, 1)),
            "top": ((0, -1, 0), (0, 0, -1), (-1, 0, 0), (2, 0)),
            "bottom": ((0, 1, 0), (0, 0, 1), (-1, 0, 0), (2, 0)),
        }
    args.output.mkdir(parents=True, exist_ok=True)
    distance = max(extents) * 3
    for view, (look_tuple, right_tuple, up_tuple, axes) in bases.items():
        look = Vector(look_tuple)
        right = Vector(right_tuple)
        up = Vector(up_tuple)
        camera.location = centre - look * distance
        camera.rotation_mode = "QUATERNION"
        camera.rotation_quaternion = Matrix((right, up, -look)).transposed().to_quaternion()
        camera_data.ortho_scale = max(extents[axes[0]], extents[axes[1]]) * 1.08
        scene.render.filepath = str((args.output / f"{view}.png").resolve())
        bpy.ops.render.render(write_still=True)

    diagnostics = {
        "mesh_objects": len(mesh_objects),
        "vertices": vertex_count,
        "faces": face_count,
        "bounds_m": {
            "minimum": [round(value, 9) for value in minimum],
            "maximum": [round(value, 9) for value in maximum],
            "extents": [round(value, 9) for value in extents],
            "vehicle_length_width_height": [
                round(extents[length], 9),
                round(extents[width], 9),
                round(extents[height], 9),
            ],
        },
        "vehicle_axis_layout": args.vehicle_axis_layout,
    }
    (args.output / "render-diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mesh", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=1024)
    parser.add_argument("--blender", default="blender")
    parser.add_argument(
        "--vehicle-axis-layout",
        choices=("bootstrap", "glb-y-up"),
        default="bootstrap",
    )
    args = parser.parse_args()
    if not args.mesh.is_file():
        parser.error(f"missing mesh: {args.mesh}")
    subprocess.run(
        [
            args.blender, "--background", "--python", str(Path(__file__).resolve()),
            "--", "--worker", "--mesh", str(args.mesh), "--output", str(args.output),
            "--resolution", str(args.resolution),
            "--vehicle-axis-layout", args.vehicle_axis_layout,
        ],
        check=True,
    )

    from PIL import Image

    masks = args.output / "masks"
    masks.mkdir(parents=True, exist_ok=True)
    assets = []
    for view in VIEWS:
        image_path = args.output / f"{view}.png"
        image = Image.open(image_path).convert("RGBA")
        alpha = image.getchannel("A")
        mask = alpha.point(lambda value: 255 if value >= 8 else 0)
        coverage = mask.histogram()[255] / (args.resolution * args.resolution)
        if coverage <= 0.001 or coverage >= 0.95:
            raise RuntimeError(f"invalid {view} silhouette coverage: {coverage:.6f}")
        mask_path = masks / f"{view}.png"
        mask.save(mask_path, format="PNG", optimize=True)
        assets.append(
            {
                "view": view,
                "image": image_path.name,
                "image_sha256": sha256(image_path),
                "mask": f"masks/{view}.png",
                "mask_sha256": sha256(mask_path),
                "coverage": round(coverage, 6),
                "status": "provisional_review_required",
            }
        )
    diagnostics_path = args.output / "render-diagnostics.json"
    manifest = {
        "schema": "design-studio.bootstrap-silhouettes/1",
        "source_mesh": str(args.mesh),
        "source_mesh_sha256": sha256(args.mesh),
        "projection": "orthographic",
        "resolution_px": args.resolution,
        "views": assets,
        "mesh_diagnostics": json.loads(diagnostics_path.read_text(encoding="utf-8")),
        "approval": None,
    }
    (args.output / "silhouette-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if "--worker" in sys.argv:
    blender_worker()
elif __name__ == "__main__":
    raise SystemExit(main())

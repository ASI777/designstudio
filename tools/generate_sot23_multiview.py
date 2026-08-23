#!/usr/bin/env python3
"""Generate a dimensioned 2N7002NXAK SOT23 model and six Omni views.

Run normally with Python.  The driver invokes Blender headlessly for geometry
and clean RGBA rendering, then creates binary masks, dimension overlays and the
Omni request template with Pillow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
from typing import Any


VIEWS = ("front", "rear", "left", "right", "top", "bottom")
ROOT = Path(__file__).resolve().parents[1]
DIMENSIONS_MM = {"width": 3.0, "depth": 2.5, "height": 1.1}
PACKAGE_DIMENSIONS_MM = {
    "A": {"min": 0.9, "nom": None, "max": 1.1, "selected": 1.1},
    "A1": {"min": 0.0, "nom": None, "max": 0.1, "selected": 0.1},
    "bp": {"min": 0.38, "nom": None, "max": 0.48, "selected": 0.48},
    "c": {"min": 0.09, "nom": None, "max": 0.15, "selected": 0.15},
    "D": {"min": 2.8, "nom": None, "max": 3.0, "selected": 3.0},
    "E": {"min": 1.2, "nom": None, "max": 1.4, "selected": 1.4},
    "e": {"min": None, "nom": 1.9, "max": None, "selected": 1.9},
    "e1": {"min": None, "nom": 0.95, "max": None, "selected": 0.95},
    "HE": {"min": 2.1, "nom": None, "max": 2.5, "selected": 2.5},
    "Lp": {"min": 0.15, "nom": None, "max": 0.45, "selected": 0.45},
    "Q": {"min": 0.45, "nom": None, "max": 0.55, "selected": 0.55},
    "v": {"min": None, "nom": 0.2, "max": None, "selected": 0.2},
    "w": {"min": None, "nom": 0.1, "max": None, "selected": 0.1},
}


def _sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _worker_arguments() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender-worker", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=1024)
    return parser.parse_args(arguments)


def _blender_worker() -> None:
    args = _worker_arguments()
    import bpy
    from mathutils import Matrix, Vector

    output = args.output.resolve()
    clean = output / "clean"
    clean.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = args.resolution
    scene.render.resolution_y = args.resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.render.image_settings.color_depth = "8"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.render.resolution_percentage = 100
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "MILLIMETERS"

    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.04, 0.04, 0.04, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.35
    scene.world = world

    black = bpy.data.materials.new("Moulded black epoxy")
    black.diffuse_color = (0.018, 0.022, 0.025, 1.0)
    black.use_nodes = True
    black.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.012, 0.016, 0.02, 1)
    black.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.34

    tin = bpy.data.materials.new("Tin plated leads")
    tin.diffuse_color = (0.62, 0.66, 0.70, 1.0)
    tin.use_nodes = True
    tin.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.76, 0.80, 0.84, 1)
    tin.node_tree.nodes["Principled BSDF"].inputs["Metallic"].default_value = 0.12
    tin.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.3
    tin.node_tree.nodes["Principled BSDF"].inputs["Emission Color"].default_value = (0.32, 0.35, 0.38, 1)
    tin.node_tree.nodes["Principled BSDF"].inputs["Emission Strength"].default_value = 0.65

    mm = 0.001

    def mesh_object(name: str, vertices: list[tuple[float, float, float]],
                    faces: list[tuple[int, ...]], material: Any) -> Any:
        mesh = bpy.data.meshes.new(name + "Mesh")
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        scene.collection.objects.link(obj)
        obj.data.materials.append(material)
        for polygon in mesh.polygons:
            polygon.use_smooth = False
        return obj

    # Body: the bottom reaches the maximum D/E outline; the moulded top tapers.
    lower_x, lower_y = 1.5 * mm, 0.7 * mm
    upper_x, upper_y = 1.4 * mm, 0.6 * mm
    z0, z1 = 0.15 * mm, 1.1 * mm
    body_vertices = [
        (-lower_x, -lower_y, z0), (lower_x, -lower_y, z0),
        (lower_x, lower_y, z0), (-lower_x, lower_y, z0),
        (-upper_x, -upper_y, z1), (upper_x, -upper_y, z1),
        (upper_x, upper_y, z1), (-upper_x, upper_y, z1),
    ]
    body_faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                  (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    body = mesh_object("SOT23 body", body_vertices, body_faces, black)
    bevel = body.modifiers.new("Mould edge radius", "BEVEL")
    bevel.width = 0.025 * mm
    bevel.segments = 2

    def lead_prism(name: str, centre_x_mm: float, profile_mm: list[tuple[float, float]],
                   width_mm: float = 0.48) -> None:
        half = width_mm * mm / 2.0
        profile = [(y * mm, z * mm) for y, z in profile_mm]
        vertices = [(centre_x_mm * mm - half, y, z) for y, z in profile]
        vertices += [(centre_x_mm * mm + half, y, z) for y, z in profile]
        count = len(profile)
        faces: list[tuple[int, ...]] = []
        faces.append(tuple(range(count - 1, -1, -1)))
        faces.append(tuple(range(count, 2 * count)))
        for index in range(count):
            next_index = (index + 1) % count
            faces.append((index, next_index, count + next_index, count + index))
        obj = mesh_object(name, vertices, faces, tin)
        lead_bevel = obj.modifiers.new("Lead edge radius", "BEVEL")
        lead_bevel.width = 0.015 * mm
        lead_bevel.segments = 2

    # Continuous side profiles form the inner contact, down-bend and flat foot.
    negative_profile = [(-0.62, 0.15), (-0.62, 0.30), (-0.82, 0.30),
                        (-1.04, 0.15), (-1.25, 0.15), (-1.25, 0.00),
                        (-0.98, 0.00), (-0.74, 0.15)]
    positive_profile = [(0.62, 0.15), (0.74, 0.15), (0.98, 0.00),
                        (1.25, 0.00), (1.25, 0.15), (1.04, 0.15),
                        (0.82, 0.30), (0.62, 0.30)]
    lead_prism("Pin 1 gate", -0.95, negative_profile)
    lead_prism("Pin 2 source", 0.95, negative_profile)
    lead_prism("Pin 3 drain", 0.0, positive_profile)

    # Soft studio illumination without a floor or cast shadow.
    for name, location, energy, size in (
        ("Key", (-0.006, -0.006, 0.008), 55.0, 0.006),
        ("Fill", (0.007, -0.003, 0.004), 35.0, 0.005),
        ("Rim", (0.0, 0.007, 0.006), 42.0, 0.004),
    ):
        light_data = bpy.data.lights.new(name, type="AREA")
        light_data.energy = energy
        light_data.shape = "DISK"
        light_data.size = size
        light = bpy.data.objects.new(name, light_data)
        scene.collection.objects.link(light)
        light.location = location
        direction = Vector((0.0, 0.0, 0.00055)) - light.location
        light.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    camera_data = bpy.data.cameras.new("Orthographic camera")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = 0.0042
    camera_data.lens = 70
    camera_data.clip_start = 0.0001
    camera_data.clip_end = 0.1
    camera = bpy.data.objects.new("Orthographic camera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera

    bases = {
        "front": ((0, 1, 0), (1, 0, 0), (0, 0, 1)),
        "rear": ((0, -1, 0), (-1, 0, 0), (0, 0, 1)),
        "left": ((1, 0, 0), (0, -1, 0), (0, 0, 1)),
        "right": ((-1, 0, 0), (0, 1, 0), (0, 0, 1)),
        "top": ((0, 0, -1), (1, 0, 0), (0, 1, 0)),
        "bottom": ((0, 0, 1), (-1, 0, 0), (0, 1, 0)),
    }
    centre = Vector((0.0, 0.0, 0.00055))
    distance = 0.012
    for view, (look_tuple, right_tuple, up_tuple) in bases.items():
        look = Vector(look_tuple)
        right = Vector(right_tuple)
        up = Vector(up_tuple)
        back = -look
        camera.location = centre - look * distance
        camera.rotation_mode = "QUATERNION"
        camera.rotation_quaternion = Matrix((right, up, back)).transposed().to_quaternion()
        scene.render.filepath = str(clean / f"{view}.png")
        bpy.ops.render.render(write_still=True)

    # Export in metres, the native glTF unit.  Keep materials and separate parts.
    scene.render.filepath = ""
    bpy.ops.export_scene.gltf(filepath=str(output / "2N7002NXAK_SOT23.glb"),
                              export_format="GLB", export_yup=False,
                              export_apply=True, export_cameras=False,
                              export_lights=False)


def _arrow(draw: Any, start: tuple[int, int], end: tuple[int, int], fill: str,
           width: int = 3) -> None:
    from math import atan2, cos, sin, pi
    draw.line((start, end), fill=fill, width=width)
    angle = atan2(end[1] - start[1], end[0] - start[0])
    for point, direction in ((start, angle), (end, angle + pi)):
        length = 13
        spread = 0.55
        tips = [(point[0] + length * cos(direction + spread),
                 point[1] + length * sin(direction + spread)),
                (point[0] + length * cos(direction - spread),
                 point[1] + length * sin(direction - spread))]
        draw.polygon((point, tips[0], tips[1]), fill=fill)


def _glb_extent_mm(path: Path) -> list[float]:
    data = path.read_bytes()
    magic, version, total = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or total != len(data):
        raise RuntimeError("invalid GLB header")
    json_length, json_type = struct.unpack_from("<I4s", data, 12)
    if json_type != b"JSON":
        raise RuntimeError("GLB does not start with a JSON chunk")
    gltf = json.loads(data[20:20 + json_length])
    minima, maxima = [], []
    for mesh in gltf.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            accessor = gltf["accessors"][primitive["attributes"]["POSITION"]]
            minima.append(accessor["min"])
            maxima.append(accessor["max"])
    if not minima:
        raise RuntimeError("GLB contains no position accessors")
    minimum = [min(value[index] for value in minima) for index in range(3)]
    maximum = [max(value[index] for value in maxima) for index in range(3)]
    return [(maximum[index] - minimum[index]) * 1000.0 for index in range(3)]


def _postprocess(output: Path, resolution: int, datasheet: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    masks_dir = output / "masks"
    annotated_dir = output / "annotated"
    masks_dir.mkdir(parents=True, exist_ok=True)
    annotated_dir.mkdir(parents=True, exist_ok=True)
    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    bold_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    font = ImageFont.truetype(str(font_path), max(18, resolution // 42))
    bold = ImageFont.truetype(str(bold_path), max(22, resolution // 34))
    small = ImageFont.truetype(str(font_path), max(15, resolution // 54))
    pixels_per_mm = resolution / 4.2
    annotations = {
        "front": ("D (width)", 3.0, "A (height)", 1.1),
        "rear": ("D (width)", 3.0, "A (height)", 1.1),
        "left": ("HE (depth)", 2.5, "A (height)", 1.1),
        "right": ("HE (depth)", 2.5, "A (height)", 1.1),
        "top": ("D (width)", 3.0, "HE (depth)", 2.5),
        "bottom": ("D (width)", 3.0, "HE (depth)", 2.5),
    }

    assets = []
    silhouette_measurements = {}
    for view in VIEWS:
        clean_path = output / "clean" / f"{view}.png"
        image = Image.open(clean_path).convert("RGBA")
        alpha = image.getchannel("A")
        mask = alpha.point(lambda value: 255 if value >= 16 else 0, mode="1").convert("L")
        foreground_pixels = mask.histogram()[255]
        coverage = foreground_pixels / (resolution * resolution)
        if coverage <= 0.001 or coverage >= 0.9:
            raise RuntimeError(f"invalid {view} foreground coverage: {coverage:.6f}")
        mask_path = masks_dir / f"{view}-mask.png"
        mask.save(mask_path, format="PNG", optimize=True)
        bounds = mask.getbbox()
        if bounds is None:
            raise RuntimeError(f"empty mask for {view}")

        canvas = Image.new("RGBA", image.size, "white")
        canvas.alpha_composite(image)
        draw = ImageDraw.Draw(canvas)
        horizontal_name, horizontal_mm, vertical_name, vertical_mm = annotations[view]
        measured_horizontal_mm = (bounds[2] - bounds[0]) / pixels_per_mm
        measured_vertical_mm = (bounds[3] - bounds[1]) / pixels_per_mm
        silhouette_measurements[view] = {
            "expected_horizontal_mm": horizontal_mm,
            "expected_vertical_mm": vertical_mm,
            "measured_horizontal_mm": round(measured_horizontal_mm, 5),
            "measured_vertical_mm": round(measured_vertical_mm, 5),
            "horizontal_error_mm": round(measured_horizontal_mm - horizontal_mm, 5),
            "vertical_error_mm": round(measured_vertical_mm - vertical_mm, 5),
            "foreground_coverage": round(coverage, 6),
        }
        if abs(measured_horizontal_mm - horizontal_mm) > 0.02 \
                or abs(measured_vertical_mm - vertical_mm) > 0.02:
            raise RuntimeError(f"{view} silhouette exceeds 0.02 mm projection tolerance")
        cx, cy = resolution // 2, resolution // 2
        half_width = int(horizontal_mm * pixels_per_mm / 2)
        half_height = int(vertical_mm * pixels_per_mm / 2)
        line_y = min(resolution - 82, cy + half_height + 68)
        line_x = min(resolution - 76, cx + half_width + 68)
        color = "#006b78"
        _arrow(draw, (cx - half_width, line_y), (cx + half_width, line_y), color)
        _arrow(draw, (line_x, cy - half_height), (line_x, cy + half_height), color)
        horizontal_text = f"{horizontal_name}: {horizontal_mm:.1f} mm max"
        vertical_text = f"{vertical_name}: {vertical_mm:.1f} mm max"
        hbox = draw.textbbox((0, 0), horizontal_text, font=font)
        draw.text((cx - (hbox[2] - hbox[0]) / 2, line_y + 12), horizontal_text,
                  fill=color, font=font)
        vbox = draw.textbbox((0, 0), vertical_text, font=font)
        vlabel = Image.new("RGBA", (vbox[2] - vbox[0] + 12, vbox[3] - vbox[1] + 12), (0, 0, 0, 0))
        ImageDraw.Draw(vlabel).text((6, 6), vertical_text, fill=color, font=font)
        vlabel = vlabel.rotate(90, expand=True)
        canvas.alpha_composite(vlabel, (line_x + 12, cy - vlabel.height // 2))
        draw.text((28, 24), f"2N7002NXAK · SOT23 · {view.upper()}", fill="#111827", font=bold)
        draw.text((28, 58), "Conservative maximum package envelope", fill="#374151", font=small)
        annotated_path = annotated_dir / f"{view}-dimensioned.png"
        canvas.convert("RGB").save(annotated_path, format="PNG", optimize=True)
        assets.append({
            "view": view,
            "image_path": f"clean/{view}.png",
            "image_sha256": _sha256(clean_path),
            "mask_path": f"masks/{view}-mask.png",
            "mask_sha256": _sha256(mask_path),
        })

    glb_extent = _glb_extent_mm(output / "2N7002NXAK_SOT23.glb")
    expected_extent = [DIMENSIONS_MM[axis] for axis in ("width", "depth", "height")]
    glb_error = [glb_extent[index] - expected_extent[index] for index in range(3)]
    if any(abs(value) > 0.01 for value in glb_error):
        raise RuntimeError(f"GLB bounding box exceeds 0.01 mm tolerance: {glb_extent}")

    import sys as _sys
    multiview_path = Path(__file__).resolve().parents[1] / "cloud" / "hunyuan-omni-amd"
    _sys.path.insert(0, str(multiview_path))
    from multiview import read_silhouette, visual_hull_surface
    masks = {view: read_silhouette(output / "clean" / f"{view}.png",
                                   masks_dir / f"{view}-mask.png") for view in VIEWS}
    hull = visual_hull_surface(masks, resolution=48)

    dimension_manifest = {
        "schema": "design-studio.datasheet-package-geometry/1",
        "component": "2N7002NXAK",
        "package": {"name": "TO-236AB", "version": "SOT23", "lead_count": 3},
        "source": {"path": str(datasheet), "page": 10, "figure": 18,
                   "document_release": "2019-07-01"},
        "axis_order": "width_depth_height",
        "axis_mapping": {"width": "D", "depth": "HE", "height": "A"},
        "selected_envelope_mm": DIMENSIONS_MM,
        "selection_policy": "conservative maximum package envelope including leads",
        "warning": "Selected independent maxima may not occur simultaneously; use as a clearance envelope.",
        "datasheet_dimensions_mm": PACKAGE_DIMENSIONS_MM,
        "assets": assets,
        "validation": {
            "glb_extent_mm_xyz": [round(value, 6) for value in glb_extent],
            "glb_extent_error_mm": [round(value, 6) for value in glb_error],
            "glb_tolerance_mm": 0.01,
            "silhouette_tolerance_mm": 0.02,
            "silhouette_measurements": silhouette_measurements,
            "visual_hull_resolution": 48,
            "visual_hull_surface_points": int(len(hull)),
        },
    }
    (output / "dimensions.json").write_text(
        json.dumps(dimension_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    request = {
        "configuration_id": "00000000-0000-0000-0000-000000000000",
        "slot_id": "slot.component.2N7002NXAK",
        "candidate_count": 1,
        "seeds": [1234],
        "input_package": {
            "prompt": "SOT23 three-lead N-channel MOSFET, matte black moulded package and tin-plated gull-wing leads; preserve the supplied engineering silhouette.",
            "dimensions_mm": DIMENSIONS_MM,
            "reference_images": [
                {"view": asset["view"],
                 "image_url": f"https://INPUT_HOST/2N7002NXAK/{asset['image_path']}",
                 "mask_url": f"https://INPUT_HOST/2N7002NXAK/{asset['mask_path']}"}
                for asset in assets
            ],
            "point_controls": [], "voxel_keepouts": [], "symmetry": "none",
            "protected_regions": [],
        },
        "transport_note": "Replace INPUT_HOST with signed HTTPS URLs, or map these assets through the planned SSH-staged input bundle resolver.",
    }
    (output / "omni-request-template.json").write_text(
        json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _driver() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=Path("references/3d_asset/2N7002NXAK"))
    parser.add_argument(
        "--datasheet",
        type=Path,
        default=ROOT / "downloaded_pdfs" / "part_3" / "digikey_fast_2N7002NXAKR.pdf",
    )
    parser.add_argument("--resolution", type=int, default=1024)
    parser.add_argument("--blender", default="blender")
    args = parser.parse_args()
    if not args.datasheet.is_file():
        raise SystemExit(f"datasheet not found: {args.datasheet}")
    if args.resolution < 512 or args.resolution > 4096:
        raise SystemExit("resolution must be in [512, 4096]")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        args.blender, "--background", "--python", str(Path(__file__).resolve()), "--",
        "--blender-worker", "--output", str(output), "--resolution", str(args.resolution),
    ], check=True)
    missing = [str(output / "clean" / f"{view}.png") for view in VIEWS
               if not (output / "clean" / f"{view}.png").is_file()]
    if missing:
        raise SystemExit("Blender did not produce required views: " + ", ".join(missing))
    _postprocess(output, args.resolution, args.datasheet.resolve())
    print(f"SOT23_MULTIVIEW_OK {output}")


if __name__ == "__main__":
    if "--blender-worker" in sys.argv:
        _blender_worker()
    else:
        _driver()

#!/usr/bin/env python3
"""Render deterministic, non-authoritative camera candidate review views in Blender."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import bpy
import bmesh
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "freecad/DesignStudioWorkbench"))
from DesignStudio.camera_benchmark import bind_candidate_review_artifacts  # noqa: E402


def look_at(obj, target: Vector) -> None:
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()


def reset_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def import_stl(path: Path):
    bpy.ops.object.select_all(action="DESELECT")
    if hasattr(bpy.ops.wm, "stl_import"):
        bpy.ops.wm.stl_import(filepath=str(path))
    else:
        bpy.ops.import_mesh.stl(filepath=str(path))
    imported = bpy.context.active_object
    if imported is None or imported.type != "MESH":
        raise RuntimeError(f"STL import did not yield one active mesh: {path}")
    mesh = bmesh.new()
    mesh.from_mesh(imported.data)
    bmesh.ops.recalc_face_normals(mesh, faces=list(mesh.faces))
    mesh.to_mesh(imported.data)
    mesh.free()
    imported.data.update()
    return imported


def review_color(material_intent: str) -> tuple[float, float, float, float]:
    value = material_intent.lower()
    if "display" in value:
        return (0.025, 0.035, 0.055, 1.0)
    if "optical" in value or "glass" in value:
        return (0.04, 0.10, 0.18, 1.0)
    if "elastomer" in value:
        return (0.05, 0.07, 0.08, 1.0)
    if "polymer" in value or "cover" in value:
        return (0.88, 0.92, 0.95, 1.0)
    if "aluminum" in value or "steel" in value:
        return (0.48, 0.67, 0.76, 1.0)
    if "texture" in value:
        return (0.30, 0.56, 0.67, 1.0)
    return (0.33, 0.62, 0.78, 1.0)


def configure_material(material, material_intent: str) -> None:
    color = review_color(material_intent)
    material.diffuse_color = color
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    if shader is not None:
        shader.inputs["Base Color"].default_value = color
        shader.inputs["Roughness"].default_value = 0.48
        shader.inputs["Metallic"].default_value = (
            0.35 if "aluminum" in material_intent.lower() else 0.0)


def import_review_assembly(stl: Path) -> list:
    manifest_path = stl.parent / "review-parts-manifest.json"
    if not manifest_path.exists():
        body = import_stl(stl)
        body.name = "Concept B-Rep review mesh"
        material = bpy.data.materials.new("Original camera concept")
        configure_material(material, "polymer")
        body.data.materials.append(material)
        body.color = material.diffuse_color
        return [body]
    result = []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("cad_authority") is not False:
        raise RuntimeError("review mesh manifest must explicitly deny CAD authority")
    for record in manifest["parts"]:
        path = Path(record["path"])
        part = import_stl(path)
        part.name = record["semantic_id"]
        material = bpy.data.materials.new(record["material_intent"])
        configure_material(material, record["material_intent"])
        part.data.materials.append(material)
        part.color = material.diffuse_color
        result.append(part)
    return result


def render(stl: Path, output: Path, azimuth: float, elevation: float = 16.0) -> None:
    reset_scene()
    import_review_assembly(stl)
    world = bpy.context.scene.world
    world.color = (0.12, 0.13, 0.15)
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = (0.035, 0.04, 0.05, 1.0)
        background.inputs["Strength"].default_value = 1.4
    center = Vector((72.5, 5.0, 47.5))
    radius = 235.0
    angle = math.radians(azimuth)
    z = 47.5 + radius * math.sin(math.radians(elevation))
    camera_data = bpy.data.cameras.new("Camera")
    camera = bpy.data.objects.new("Camera", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = (center.x + radius * math.cos(angle),
                       center.y + radius * math.sin(angle), z)
    look_at(camera, center)
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = 205
    bpy.context.scene.camera = camera
    for energy, location, size in ((110000, (-120, -100, 190), 110),
                                   (80000, (190, -70, 130), 80),
                                   (50000, (60, 180, 210), 65)):
        data = bpy.data.lights.new("Area", "AREA")
        data.energy, data.shape, data.size = energy, "DISK", size
        light = bpy.data.objects.new("Area", data)
        light.location = location
        bpy.context.collection.objects.link(light)
        look_at(light, center)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    # Flat material lighting keeps front/rear faces legible at every azimuth;
    # cavity edges still communicate seams and local form.  These images are
    # review evidence only and never replace the B-Rep authority.
    scene.display.shading.light = "FLAT"
    scene.display.shading.color_type = "OBJECT"
    scene.display.shading.show_shadows = False
    scene.display.shading.show_cavity = True
    scene.display.shading.cavity_type = "WORLD"
    scene.display.shading.curvature_ridge_factor = 1.5
    scene.display.shading.curvature_valley_factor = 1.2
    scene.render.resolution_x = 720
    scene.render.resolution_y = 540
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.render.filepath = str(output)
    scene.view_settings.look = "AgX - Medium Low Contrast"
    bpy.ops.render.render(write_still=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    for label in ("precision", "grip", "serviceable"):
        stl = args.input_root / "candidates" / label / f"ai-camera-{label}.stl"
        for azimuth in (270, 315, 0, 45, 90, 135, 180, 225):
            path = args.output / f"{label}-{azimuth:03d}.png"
            render(stl, path, azimuth)
            records.append({"candidate": label, "azimuth_deg": azimuth, "path": str(path),
                            "authority": "review-render-only"})
    manifest_path = args.output / "render-manifest.json"
    manifest_path.write_text(json.dumps({
        "schema": "design-studio.camera-review-renders/1", "renders": records,
        "cad_authority": False, "human_review_required": True
    }, indent=2) + "\n")
    review_path = args.input_root / "candidate-review.json"
    if review_path.is_file():
        bind_candidate_review_artifacts(review_path, {
            label: [Path(item["path"]) for item in records if item["candidate"] == label]
                   + [manifest_path]
            for label in ("precision", "grip", "serviceable")
        })


if __name__ == "__main__":
    main()

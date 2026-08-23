#!/usr/bin/env python3
"""Render the semantic robot meshes with an industrial-design presentation.

Run with Blender in background mode.  Input and output are supplied through
``DESIGNSTUDIO_CAD_MATERIALIZATION`` and ``DESIGNSTUDIO_RENDER_OUTPUT``.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import bpy
from mathutils import Vector


def sha256_file(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def import_stl(path: Path):
    before = set(bpy.data.objects)
    if hasattr(bpy.ops.wm, "stl_import"):
        bpy.ops.wm.stl_import(filepath=str(path))
    else:
        bpy.ops.import_mesh.stl(filepath=str(path))
    imported = [obj for obj in bpy.data.objects if obj not in before]
    if not imported:
        raise RuntimeError(f"Blender did not import {path}")
    return imported


def material(name: str, base_color: tuple[float, float, float, float],
             metallic: float, roughness: float):
    result = bpy.data.materials.new(name)
    result.use_nodes = True
    principled = result.node_tree.nodes.get("Principled BSDF")
    principled.inputs["Base Color"].default_value = base_color
    principled.inputs["Metallic"].default_value = metallic
    principled.inputs["Roughness"].default_value = roughness
    return result


def look_at(obj, target: Vector) -> None:
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()


def add_area(name: str, location: tuple[float, float, float], energy: float,
             size: float, target: Vector, color: tuple[float, float, float]):
    data = bpy.data.lights.new(name, "AREA")
    data.energy = energy
    data.shape = "DISK"
    data.size = size
    data.color = color
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    look_at(obj, target)
    return obj


def main() -> None:
    materialization_value = os.environ.get("DESIGNSTUDIO_CAD_MATERIALIZATION", "")
    output_value = os.environ.get("DESIGNSTUDIO_RENDER_OUTPUT", "")
    if not materialization_value or not output_value:
        raise RuntimeError(
            "DESIGNSTUDIO_CAD_MATERIALIZATION and DESIGNSTUDIO_RENDER_OUTPUT are required")
    materialization_path = Path(materialization_value).expanduser().resolve()
    materialization = json.loads(materialization_path.read_text(encoding="utf-8"))
    root = materialization_path.parent
    output = Path(output_value).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    view_mode = os.environ.get(
        "DESIGNSTUDIO_RENDER_VIEW", str(materialization.get("default_view", "exterior"))
    ).strip().lower()
    supported_views = {"exterior", "service", "electronics_service", "structure"}
    if view_mode not in supported_views:
        raise RuntimeError(
            "DESIGNSTUDIO_RENDER_VIEW must be exterior, service, "
            "electronics_service or structure")

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.meshes, bpy.data.curves, bpy.data.materials,
                       bpy.data.cameras, bpy.data.lights):
        for datablock in list(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)

    materials = {
        "cosmetic_shell": material("Warm white low gloss", (0.32, 0.36, 0.34, 1.0), 0.08, 0.34),
        "joint_cover": material("Graphite collar", (0.025, 0.032, 0.042, 1.0), 0.55, 0.2),
        "tool_flange": material("Tool flange", (0.11, 0.13, 0.15, 1.0), 0.72, 0.18),
        "internal_structure": material("Anodized structural aluminum", (0.22, 0.25, 0.29, 1.0), 0.82, 0.22),
        "internal_fastener": material("Black oxide fastener", (0.015, 0.017, 0.02, 1.0), 0.72, 0.18),
        "pcb": material("PCB solder mask", (0.025, 0.25, 0.055, 1.0), 0.12, 0.42),
        "harness": material("Harness insulation", (0.75, 0.09, 0.018, 1.0), 0.02, 0.38),
    }
    if view_mode == "exterior":
        visible_roles = ("cosmetic_shell", "joint_cover", "tool_flange")
        hidden_roles = ("internal_structure", "internal_fastener", "pcb", "harness",
                        "keepout", "thermal_volume")
    elif view_mode == "service":
        visible_roles = (
            "internal_structure", "internal_fastener", "pcb", "harness", "tool_flange")
        hidden_roles = ("cosmetic_shell", "joint_cover", "keepout", "thermal_volume")
    elif view_mode == "electronics_service":
        visible_roles = ("internal_fastener", "pcb", "harness", "tool_flange")
        hidden_roles = (
            "cosmetic_shell", "joint_cover", "internal_structure", "keepout",
            "thermal_volume")
    else:  # structure
        visible_roles = ("internal_structure", "harness", "tool_flange")
        hidden_roles = ("cosmetic_shell", "joint_cover", "internal_fastener", "pcb", "keepout",
                        "thermal_volume")
    imported_objects = []
    for role in visible_roles:
        mesh_path = root / "meshes" / f"{role}.stl"
        if not mesh_path.is_file():
            raise RuntimeError(f"missing semantic render mesh: {mesh_path}")
        for obj in import_stl(mesh_path):
            obj.name = f"{role}-{obj.name}"
            obj.scale = (0.001, 0.001, 0.001)
            obj.data.materials.clear()
            obj.data.materials.append(materials[role])
            for polygon in obj.data.polygons:
                polygon.use_smooth = True
            imported_objects.append(obj)

    bpy.context.view_layer.update()
    world_corners = []
    for obj in imported_objects:
        world_corners.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    minimum = Vector((min(point.x for point in world_corners),
                      min(point.y for point in world_corners),
                      min(point.z for point in world_corners)))
    maximum = Vector((max(point.x for point in world_corners),
                      max(point.y for point in world_corners),
                      max(point.z for point in world_corners)))
    center = (minimum + maximum) * 0.5
    span = max((maximum - minimum).x, (maximum - minimum).y, (maximum - minimum).z)

    floor_material = material("Studio floor", (0.035, 0.042, 0.052, 1.0), 0.0, 0.48)
    bpy.ops.mesh.primitive_plane_add(size=span * 5.0,
                                     location=(center.x, center.y, minimum.z - span * 0.025))
    floor = bpy.context.object
    floor.name = "Studio floor"
    floor.data.materials.append(floor_material)

    camera_data = bpy.data.cameras.new("Industrial review camera")
    camera = bpy.data.objects.new("Industrial review camera", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = center + Vector((span * 1.35, -span * 1.75, span * 1.05))
    camera_data.lens = 58.0
    look_at(camera, center + Vector((0.0, 0.0, span * 0.05)))
    bpy.context.scene.camera = camera

    add_area("Key", tuple(center + Vector((span * 0.4, -span * 0.7, span * 1.6))),
             260.0, span * 1.2, center, (1.0, 0.91, 0.82))
    add_area("Fill", tuple(center + Vector((-span * 1.0, -span * 0.1, span * 0.75))),
             140.0, span * 1.0, center, (0.66, 0.78, 1.0))
    add_area("Rim", tuple(center + Vector((span * 0.5, span * 1.0, span * 1.1))),
             220.0, span * 0.8, center, (0.76, 0.86, 1.0))

    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 1600
    scene.render.resolution_y = 1000
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.render.filepath = str(output)
    scene.render.image_settings.color_mode = "RGBA"
    scene.world.color = (0.012, 0.016, 0.024)
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = -1.35
    scene.render.image_settings.color_depth = "8"
    scene.camera.data.dof.use_dof = True
    scene.camera.data.dof.focus_object = imported_objects[0]
    scene.camera.data.dof.aperture_fstop = 7.1
    bpy.ops.render.render(write_still=True)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("Blender did not produce a render")

    receipt_path = output.with_suffix(".render.json")
    receipt = {
        "schema": "design-studio.product-render/1",
        "cad_materialization_sha256": sha256_file(materialization_path),
        "program_digest": materialization["program_digest"],
        "visible_roles": list(visible_roles),
        "view_mode": view_mode,
        "hidden_roles": list(hidden_roles),
        "externally_visible_fastener_count": 0,
        "renderer": f"Blender {bpy.app.version_string} Eevee Next",
        "image_path": output.name,
        "image_sha256": sha256_file(output),
        "resolution_px": [scene.render.resolution_x, scene.render.resolution_y],
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

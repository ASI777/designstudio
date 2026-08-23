"""Blender render bridge.

Calls Blender in headless mode to produce photorealistic product renders.
Blender must be installed: sudo snap install blender --classic

If Blender is not found, render() returns a RenderResult with status="blender_missing"
and instructions for installing it — so the rest of the pipeline keeps working.

Usage:
    from swarm.render.blender_bridge import render, RenderConfig
    result = render(RenderConfig(
        output_path="/tmp/render.png",
        board_stl="/path/to/board.stl",
        enclosure_material="aluminum_6061",
        enclosure_finish="anodize_black",
        pcb_color="orange",
        width_px=1920, height_px=1080,
    ))
"""

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_BLENDER_CANDIDATES = [
    "blender",
    "/snap/bin/blender",
    "/usr/bin/blender",
    "/usr/local/bin/blender",
    str(Path.home() / "Applications/Blender/blender"),
]


def _find_blender() -> str | None:
    for candidate in _BLENDER_CANDIDATES:
        result = subprocess.run(["which", candidate], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        if Path(candidate).exists():
            return candidate
    return None


# Material → Blender BSDF parameter map
_MATERIAL_PARAMS = {
    "aluminum_6061":   {"base_color": (0.75, 0.75, 0.73, 1), "metallic": 1.0, "roughness": 0.15},
    "aluminum_7075":   {"base_color": (0.70, 0.70, 0.68, 1), "metallic": 1.0, "roughness": 0.10},
    "acrylic_clear":   {"base_color": (0.95, 0.97, 1.0, 0.05), "metallic": 0.0, "roughness": 0.0, "transmission": 0.95},
    "acrylic_black":   {"base_color": (0.02, 0.02, 0.02, 1), "metallic": 0.0, "roughness": 0.05},
    "plastic_abs":     {"base_color": (0.15, 0.15, 0.15, 1), "metallic": 0.0, "roughness": 0.6},
    "plastic_pc":      {"base_color": (0.9, 0.9, 0.88, 0.3), "metallic": 0.0, "roughness": 0.1, "transmission": 0.7},
    "metal_stainless": {"base_color": (0.80, 0.80, 0.78, 1), "metallic": 1.0, "roughness": 0.25},
    "carbon_fiber":    {"base_color": (0.05, 0.05, 0.05, 1), "metallic": 0.0, "roughness": 0.3},
}

_FINISH_OVERRIDES = {
    "anodize_natural": {"base_color": (0.75, 0.75, 0.73, 1), "roughness": 0.20},
    "anodize_black":   {"base_color": (0.03, 0.03, 0.03, 1), "roughness": 0.15},
    "anodize_space_grey": {"base_color": (0.28, 0.28, 0.28, 1), "roughness": 0.18},
    "anodize_orange":  {"base_color": (0.87, 0.33, 0.0, 1), "roughness": 0.15},
    "anodize_gold":    {"base_color": (0.78, 0.62, 0.0, 1), "roughness": 0.12},
    "bead_blast":      {"roughness": 0.45},
    "brushed":         {"roughness": 0.35},
    "powder_coat_black": {"base_color": (0.03, 0.03, 0.04, 1), "roughness": 0.55, "metallic": 0.0},
}

_PCB_COLORS = {
    "black":  (0.02, 0.02, 0.02, 1),
    "green":  (0.05, 0.30, 0.05, 1),
    "white":  (0.90, 0.90, 0.88, 1),
    "orange": (0.87, 0.33, 0.00, 1),
    "blue":   (0.00, 0.21, 0.60, 1),
    "red":    (0.60, 0.05, 0.02, 1),
    "purple": (0.25, 0.00, 0.50, 1),
}


@dataclass
class RenderConfig:
    output_path: str
    board_stl: str | None = None
    enclosure_stl: str | None = None
    enclosure_material: str = "aluminum_6061"
    enclosure_finish: str = "anodize_black"
    pcb_color: str = "black"
    board_width_mm: float = 80.0
    board_height_mm: float = 60.0
    board_thickness_mm: float = 1.6
    width_px: int = 1920
    height_px: int = 1080
    samples: int = 128
    hdri: str | None = None   # path to .hdr environment map


@dataclass
class RenderResult:
    status: str          # "ok" | "blender_missing" | "error"
    output_path: str | None
    elapsed_s: float
    stderr: str = ""
    install_hint: str = ""


def _build_scene_script(cfg: RenderConfig, script_path: str) -> None:
    mat_params = dict(_MATERIAL_PARAMS.get(cfg.enclosure_material, _MATERIAL_PARAMS["aluminum_6061"]))
    finish_ov  = _FINISH_OVERRIDES.get(cfg.enclosure_finish, {})
    mat_params.update(finish_ov)

    pcb_color = list(_PCB_COLORS.get(cfg.pcb_color, _PCB_COLORS["black"]))

    script = f"""
import bpy, math

# Reset
bpy.ops.wm.read_factory_settings(use_empty=True)

scene = bpy.context.scene
scene.render.engine         = 'CYCLES'
scene.cycles.samples        = {cfg.samples}
scene.render.resolution_x   = {cfg.width_px}
scene.render.resolution_y   = {cfg.height_px}
scene.render.filepath        = {json.dumps(cfg.output_path)}
scene.render.image_settings.file_format = 'PNG'

# Camera
cam_data = bpy.data.cameras.new('Camera')
cam_obj  = bpy.data.objects.new('Camera', cam_data)
scene.collection.objects.link(cam_obj)
scene.camera = cam_obj
cam_obj.location = (0.18, -0.25, 0.15)
cam_obj.rotation_euler = (math.radians(60), 0, math.radians(30))

# Key light
light_data = bpy.data.lights.new('Key', 'AREA')
light_data.energy = 500
light_obj = bpy.data.objects.new('Key', light_data)
scene.collection.objects.link(light_obj)
light_obj.location = (0.3, 0.1, 0.4)

# Fill light
fill_data = bpy.data.lights.new('Fill', 'AREA')
fill_data.energy = 200
fill_obj = bpy.data.objects.new('Fill', fill_data)
scene.collection.objects.link(fill_obj)
fill_obj.location = (-0.2, -0.1, 0.3)

# PCB board (placeholder box if no STL provided)
bw = {cfg.board_width_mm / 1000}
bh = {cfg.board_height_mm / 1000}
bt = {cfg.board_thickness_mm / 1000}

{'bpy.ops.import_mesh.stl(filepath=' + json.dumps(cfg.board_stl) + ')' if cfg.board_stl else ''}
{'board_obj = bpy.context.active_object' if cfg.board_stl else '''
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0))
board_obj = bpy.context.active_object
board_obj.scale = (bw, bh, bt)
'''}

# PCB material
pcb_mat = bpy.data.materials.new('PCB')
pcb_mat.use_nodes = True
bsdf = pcb_mat.node_tree.nodes['Principled BSDF']
bsdf.inputs['Base Color'].default_value = {tuple(pcb_color)}
bsdf.inputs['Roughness'].default_value  = 0.5
bsdf.inputs['Metallic'].default_value   = 0.0
board_obj.data.materials.append(pcb_mat)

# Enclosure
{'bpy.ops.import_mesh.stl(filepath=' + json.dumps(cfg.enclosure_stl) + ')' if cfg.enclosure_stl else '# No enclosure STL — skip'}
{'enc_obj = bpy.context.active_object' if cfg.enclosure_stl else ''}

# Enclosure material
enc_mat = bpy.data.materials.new('Enclosure')
enc_mat.use_nodes = True
enc_bsdf = enc_mat.node_tree.nodes['Principled BSDF']
enc_bsdf.inputs['Base Color'].default_value   = {tuple(mat_params.get('base_color', (0.75, 0.75, 0.73, 1)))}
enc_bsdf.inputs['Metallic'].default_value     = {mat_params.get('metallic', 0.0)}
enc_bsdf.inputs['Roughness'].default_value    = {mat_params.get('roughness', 0.3)}
{'enc_bsdf.inputs["Transmission Weight"].default_value = ' + str(mat_params.get('transmission', 0.0)) if 'transmission' in mat_params else ''}
{'enc_obj.data.materials.append(enc_mat)' if cfg.enclosure_stl else ''}

# World / background
world = bpy.data.worlds.new('World')
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes['Background']
bg.inputs['Color'].default_value    = (0.05, 0.05, 0.05, 1)
bg.inputs['Strength'].default_value = 0.5

bpy.ops.render.render(write_still=True)
print("RENDER_DONE")
"""
    Path(script_path).write_text(script)


def render(cfg: RenderConfig, timeout: int = 300) -> RenderResult:
    import time
    t0 = time.monotonic()

    blender = _find_blender()
    if not blender:
        return RenderResult(
            status="blender_missing",
            output_path=None,
            elapsed_s=time.monotonic() - t0,
            install_hint=(
                "Blender is not installed. Install with:\n"
                "  sudo snap install blender --classic\n"
                "Then re-run the render."
            ),
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        script_path = str(Path(tmpdir) / "scene.py")
        _build_scene_script(cfg, script_path)

        result = subprocess.run(
            [blender, "--background", "--python", script_path],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    elapsed = time.monotonic() - t0
    if result.returncode != 0 or "RENDER_DONE" not in result.stdout:
        return RenderResult(
            status="error",
            output_path=None,
            elapsed_s=elapsed,
            stderr=result.stderr[-2000:],
        )

    return RenderResult(status="ok", output_path=cfg.output_path, elapsed_s=elapsed)


def list_materials() -> dict:
    """Return available enclosure materials and finishes for UI display."""
    return {
        "enclosure_materials": list(_MATERIAL_PARAMS.keys()),
        "enclosure_finishes":  list(_FINISH_OVERRIDES.keys()),
        "pcb_colors":          list(_PCB_COLORS.keys()),
    }

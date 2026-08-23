"""Datasheet-grounded component GLB generation and Hunyuan3D fallback.

Luna/xhigh authors ``package_3d.construction`` while it can see the package
drawings.  This module executes that bounded construction plan.  Simple plans
become deterministic parametric GLBs.  Complex plans are rendered from six
cardinal directions and submitted to the pinned Hunyuan3D-Omni 2.1 ROCm
service.  The parametric mesh remains a visible proxy until the cloud result is
available; generated visualization assets never masquerade as verified STEP.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import time
from typing import Any, Callable
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid5

from swarm.runtime_paths import component_asset_library_dir


CARDINAL_VIEWS = ("front", "rear", "left", "right", "top", "bottom")
IDENTITY_4X4 = [1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0,
                0.0, 0.0, 0.0, 1.0]
_MAX_EMBEDDED_IMAGE_BYTES = 16 * 1024 * 1024


class Component3DAssetError(RuntimeError):
    pass


def _safe(value: str) -> str:
    return "".join(character if character.isalnum() or character in "-_."
                   else "_" for character in value) or "component"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _number(value: Any, default: float) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) and result > 0 else default
    except (TypeError, ValueError):
        return default


def _package_dimensions(component: dict[str, Any]) -> tuple[float, float, float, float, float, float]:
    footprint = component.get("footprint") or {}
    body = footprint.get("body") or {}
    pads = footprint.get("pads") or []
    xs: list[float] = []
    ys: list[float] = []
    for pad in pads:
        x = float(pad.get("x_mm", 0.0)); y = float(pad.get("y_mm", 0.0))
        width = _number(pad.get("width_mm"), 0.5)
        depth = _number(pad.get("height_mm"), width)
        xs.extend((x - width / 2.0, x + width / 2.0))
        ys.extend((y - depth / 2.0, y + depth / 2.0))
    pad_width = max(xs) - min(xs) if xs else 2.0
    pad_depth = max(ys) - min(ys) if ys else 2.0
    width = _number(body.get("width_mm"), pad_width)
    depth = _number(body.get("length_mm"), pad_depth)
    package = component.get("package_3d") or {}
    height = _number(package.get("height_mm"), _number(body.get("height_mm"), 1.0))
    try:
        standoff = max(0.0, float(package.get("standoff_mm", 0.0)))
    except (TypeError, ValueError):
        standoff = 0.0
    centre_x = (max(xs) + min(xs)) / 2.0 if xs else 0.0
    centre_y = (max(ys) + min(ys)) / 2.0 if ys else 0.0
    return width, depth, height, standoff, centre_x, centre_y


def _default_primitives(component: dict[str, Any]) -> list[dict[str, Any]]:
    """Conservative proxy for old records that predate Luna construction plans."""
    width, depth, height, standoff, centre_x, centre_y = _package_dimensions(component)
    package = component.get("package_3d") or {}
    shape = package.get("shape", "box")
    body_shape = shape if shape in ("box", "cylinder", "dome") else "box"
    result = [{
        "id": "body", "role": "body", "shape": body_shape,
        "center_mm": [centre_x, centre_y, standoff + height / 2.0],
        "size_mm": [width, depth, height],
        "rotation_deg_xyz": [0.0, 0.0, 0.0],
        "color_rgba": [0.055, 0.07, 0.08, 1.0],
    }]
    footprint = component.get("footprint") or {}
    through_hole = str(footprint.get("mount", "smd")).lower() in {
        "through", "through_hole", "through-hole", "tht", "thru"}
    lead_height = min(max(height * 0.12, 0.08), 0.35)
    for index, pad in enumerate(footprint.get("pads") or []):
        pad_width = _number(pad.get("width_mm"), 0.5)
        pad_depth = _number(pad.get("height_mm"), pad_width)
        if through_hole:
            diameter = _number(pad.get("drill_mm"), min(pad_width, pad_depth) * 0.55)
            size = [diameter, diameter, max(height * 0.45, 1.0)]
            z = size[2] / 2.0
            lead_shape = "cylinder"
        else:
            size = [pad_width, pad_depth, lead_height]
            z = lead_height / 2.0
            lead_shape = "box"
        result.append({
            "id": f"lead-{index + 1}", "role": "lead", "shape": lead_shape,
            "center_mm": [float(pad.get("x_mm", 0.0)), float(pad.get("y_mm", 0.0)), z],
            "size_mm": size, "rotation_deg_xyz": [0.0, 0.0, 0.0],
            "color_rgba": [0.68, 0.72, 0.76, 1.0],
        })
    return result


def construction_primitives(component: dict[str, Any]) -> list[dict[str, Any]]:
    construction = (component.get("package_3d") or {}).get("construction") or {}
    primitives = construction.get("primitives") or []
    valid = []
    for index, primitive in enumerate(primitives):
        if primitive.get("shape") not in ("box", "cylinder", "dome"):
            continue
        centre = primitive.get("center_mm")
        size = primitive.get("size_mm")
        if not (isinstance(centre, list) and len(centre) == 3
                and isinstance(size, list) and len(size) == 3):
            continue
        try:
            item = dict(primitive)
            item["id"] = str(item.get("id") or f"primitive-{index + 1}")
            item["center_mm"] = [float(value) for value in centre]
            item["size_mm"] = [max(0.001, float(value)) for value in size]
            item["rotation_deg_xyz"] = [float(value) for value in
                                          item.get("rotation_deg_xyz", [0, 0, 0])]
            color = item.get("color_rgba", [0.08, 0.09, 0.10, 1.0])
            item["color_rgba"] = [min(1.0, max(0.0, float(value))) for value in color]
            valid.append(item)
        except (TypeError, ValueError):
            continue
    return valid or _default_primitives(component)


def _rotate(point: tuple[float, float, float], degrees: list[float]) -> tuple[float, float, float]:
    x, y, z = point
    rx, ry, rz = (math.radians(value) for value in degrees)
    cy, sy = math.cos(rx), math.sin(rx)
    y, z = y * cy - z * sy, y * sy + z * cy
    cx, sx = math.cos(ry), math.sin(ry)
    x, z = x * cx + z * sx, -x * sx + z * cx
    cz, sz = math.cos(rz), math.sin(rz)
    return x * cz - y * sz, x * sz + y * cz, z


def _box(size: list[float]) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    x, y, z = (value / 2.0 for value in size)
    vertices = [(-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z),
                (-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
             (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
             (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return vertices, faces


def _cylinder(size: list[float], segments: int = 24) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    radius_x, radius_y, half_z = size[0] / 2.0, size[1] / 2.0, size[2] / 2.0
    vertices = [(radius_x * math.cos(2 * math.pi * index / segments),
                 radius_y * math.sin(2 * math.pi * index / segments), -half_z)
                for index in range(segments)]
    vertices += [(x, y, half_z) for x, y, _ in vertices]
    vertices += [(0.0, 0.0, -half_z), (0.0, 0.0, half_z)]
    faces: list[tuple[int, int, int]] = []
    for index in range(segments):
        nxt = (index + 1) % segments
        faces.extend(((index, nxt, segments + nxt),
                      (index, segments + nxt, segments + index),
                      (2 * segments, nxt, index),
                      (2 * segments + 1, segments + index, segments + nxt)))
    return vertices, faces


def _dome(size: list[float], segments: int = 24, rings: int = 8) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    vertices: list[tuple[float, float, float]] = []
    for ring in range(rings + 1):
        phi = (math.pi / 2.0) * ring / rings
        for segment in range(segments):
            theta = 2 * math.pi * segment / segments
            vertices.append((size[0] / 2 * math.cos(phi) * math.cos(theta),
                             size[1] / 2 * math.cos(phi) * math.sin(theta),
                             -size[2] / 2 + size[2] * math.sin(phi)))
    faces: list[tuple[int, int, int]] = []
    for ring in range(rings):
        for segment in range(segments):
            nxt = (segment + 1) % segments
            a = ring * segments + segment; b = ring * segments + nxt
            c = (ring + 1) * segments + segment; d = (ring + 1) * segments + nxt
            faces.extend(((a, b, d), (a, d, c)))
    bottom = len(vertices); vertices.append((0.0, 0.0, -size[2] / 2))
    for segment in range(segments):
        faces.append((bottom, (segment + 1) % segments, segment))
    return vertices, faces


def meshes_from_component(component: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for primitive in construction_primitives(component):
        builder = {"box": _box, "cylinder": _cylinder, "dome": _dome}[primitive["shape"]]
        vertices, faces = builder(primitive["size_mm"])
        rotation = primitive.get("rotation_deg_xyz", [0.0, 0.0, 0.0])
        centre = primitive["center_mm"]
        transformed = []
        for vertex in vertices:
            x, y, z = _rotate(vertex, rotation)
            transformed.append((x + centre[0], y + centre[1], z + centre[2]))
        result.append({"vertices": transformed, "faces": faces,
                       "color": primitive["color_rgba"], "id": primitive["id"],
                       "role": primitive.get("role", "detail")})
    return result


def _align(value: bytes, byte: bytes = b"\0") -> bytes:
    return value + byte * ((4 - len(value) % 4) % 4)


def write_glb(meshes: list[dict[str, Any]], path: Path, *, generator: str) -> dict[str, float]:
    if not meshes:
        raise Component3DAssetError("component construction contains no geometry")
    binary = bytearray()
    buffer_views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []
    primitives: list[dict[str, Any]] = []
    materials: list[dict[str, Any]] = []
    all_vertices: list[tuple[float, float, float]] = []
    for mesh in meshes:
        vertices_m = [(x / 1000.0, y / 1000.0, z / 1000.0)
                      for x, y, z in mesh["vertices"]]
        all_vertices.extend(mesh["vertices"])
        position_offset = len(binary)
        for vertex in vertices_m:
            binary.extend(struct.pack("<3f", *vertex))
        buffer_views.append({"buffer": 0, "byteOffset": position_offset,
                             "byteLength": len(vertices_m) * 12, "target": 34962})
        minima = [min(vertex[axis] for vertex in vertices_m) for axis in range(3)]
        maxima = [max(vertex[axis] for vertex in vertices_m) for axis in range(3)]
        position_accessor = len(accessors)
        accessors.append({"bufferView": len(buffer_views) - 1, "componentType": 5126,
                          "count": len(vertices_m), "type": "VEC3",
                          "min": minima, "max": maxima})
        index_offset = len(binary)
        indices = [value for face in mesh["faces"] for value in face]
        for index in indices:
            binary.extend(struct.pack("<I", index))
        buffer_views.append({"buffer": 0, "byteOffset": index_offset,
                             "byteLength": len(indices) * 4, "target": 34963})
        index_accessor = len(accessors)
        accessors.append({"bufferView": len(buffer_views) - 1, "componentType": 5125,
                          "count": len(indices), "type": "SCALAR"})
        color = mesh.get("color", [0.1, 0.1, 0.1, 1.0])
        materials.append({"name": str(mesh.get("role", "detail")),
                          "pbrMetallicRoughness": {"baseColorFactor": color,
                                                   "metallicFactor": 0.35 if mesh.get("role") == "lead" else 0.0,
                                                   "roughnessFactor": 0.48}})
        primitives.append({"attributes": {"POSITION": position_accessor},
                           "indices": index_accessor, "material": len(materials) - 1,
                           "mode": 4})
    binary_bytes = _align(bytes(binary))
    gltf = {"asset": {"version": "2.0", "generator": generator},
            "buffers": [{"byteLength": len(binary_bytes)}],
            "bufferViews": buffer_views, "accessors": accessors,
            "materials": materials, "meshes": [{"primitives": primitives}],
            "nodes": [{"mesh": 0}], "scenes": [{"nodes": [0]}], "scene": 0}
    json_bytes = _align(json.dumps(gltf, sort_keys=True, separators=(",", ":")).encode(), b" ")
    total = 12 + 8 + len(json_bytes) + 8 + len(binary_bytes)
    payload = (struct.pack("<4sII", b"glTF", 2, total)
               + struct.pack("<I4s", len(json_bytes), b"JSON") + json_bytes
               + struct.pack("<I4s", len(binary_bytes), b"BIN\0") + binary_bytes)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    minimum = [min(vertex[axis] for vertex in all_vertices) for axis in range(3)]
    maximum = [max(vertex[axis] for vertex in all_vertices) for axis in range(3)]
    return {"width": maximum[0] - minimum[0], "depth": maximum[1] - minimum[1],
            "height": maximum[2] - minimum[2]}


def render_six_views(meshes: list[dict[str, Any]], output: Path,
                     resolution: int = 512) -> list[dict[str, Any]]:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise Component3DAssetError("Pillow is required for six-view component rendering") from exc
    output.mkdir(parents=True, exist_ok=True)
    mappings = {
        "front": ((0, 1), (2, 1), (1, 1)), "rear": ((0, -1), (2, 1), (1, -1)),
        "left": ((1, -1), (2, 1), (0, -1)), "right": ((1, 1), (2, 1), (0, 1)),
        "top": ((0, 1), (1, 1), (2, 1)), "bottom": ((0, -1), (1, 1), (2, -1)),
    }
    vertices = [vertex for mesh in meshes for vertex in mesh["vertices"]]
    records = []
    for view in CARDINAL_VIEWS:
        right, up, depth = mappings[view]
        projected_all = [(vertex[right[0]] * right[1], vertex[up[0]] * up[1])
                         for vertex in vertices]
        min_x = min(point[0] for point in projected_all); max_x = max(point[0] for point in projected_all)
        min_y = min(point[1] for point in projected_all); max_y = max(point[1] for point in projected_all)
        span = max(max_x - min_x, max_y - min_y, 0.001) * 1.18
        centre_x = (min_x + max_x) / 2; centre_y = (min_y + max_y) / 2
        scale = (resolution - 2) / span
        image = Image.new("RGBA", (resolution, resolution), (246, 248, 250, 0))
        mask = Image.new("L", (resolution, resolution), 0)
        painter = ImageDraw.Draw(image, "RGBA"); mask_painter = ImageDraw.Draw(mask)
        triangles = []
        for mesh in meshes:
            for face in mesh["faces"]:
                triangle = [mesh["vertices"][index] for index in face]
                z = sum(point[depth[0]] * depth[1] for point in triangle) / 3.0
                points = [((point[right[0]] * right[1] - centre_x) * scale + resolution / 2,
                           resolution / 2 - (point[up[0]] * up[1] - centre_y) * scale)
                          for point in triangle]
                triangles.append((z, points, mesh["color"]))
        for _, points, color in sorted(triangles, key=lambda value: value[0]):
            rgba = tuple(round(255 * value) for value in color)
            # Do not expose triangulation diagonals to Omni as appearance
            # features; the binary mask carries the authoritative silhouette.
            painter.polygon(points, fill=rgba)
            mask_painter.polygon(points, fill=255)
        image_path = output / f"{view}.png"; mask_path = output / f"{view}-mask.png"
        image.save(image_path, format="PNG"); mask.save(mask_path, format="PNG")
        records.append({"view": view, "image_uri": str(image_path.resolve()),
                        "image_sha256": _sha256_path(image_path),
                        "mask_uri": str(mask_path.resolve()),
                        "mask_sha256": _sha256_path(mask_path)})
    return records


def _http_json(url: str, *, token: str, payload: dict[str, Any] | None = None,
               timeout: float = 30.0) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    request = Request(url, data=data, headers={"Authorization": f"Bearer {token}",
                                               "Content-Type": "application/json",
                                               "User-Agent": "DesignStudio-Component3D/1"},
                      method="POST" if data is not None else "GET")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def submit_hunyuan_omni(*, views: list[dict[str, Any]], dimensions: dict[str, float],
                        prompt: str, endpoint: str, token: str,
                        timeout: float = 900.0) -> tuple[bytes, dict[str, Any]]:
    """Send six embedded PNGs and return the first pinned GLB candidate."""
    references = []
    for view in views:
        image = Path(view["image_uri"]).read_bytes(); mask = Path(view["mask_uri"]).read_bytes()
        if len(image) > _MAX_EMBEDDED_IMAGE_BYTES or len(mask) > _MAX_EMBEDDED_IMAGE_BYTES:
            raise Component3DAssetError("six-view image exceeds the 16 MiB service limit")
        references.append({"view": view["view"],
                           "image_base64": base64.b64encode(image).decode("ascii"),
                           "image_sha256": _sha256_bytes(image),
                           "mask_base64": base64.b64encode(mask).decode("ascii"),
                           "mask_sha256": _sha256_bytes(mask)})
    identity = str(uuid5(NAMESPACE_URL, prompt + json.dumps(dimensions, sort_keys=True)))
    payload = {"configuration_id": identity, "slot_id": "slot.component_body",
               "candidate_count": 1, "seeds": [int(identity.replace("-", "")[:8], 16)],
               "input_package": {"prompt": prompt[:2000], "reference_images": references,
                                  "dimensions_mm": dimensions, "point_controls": [],
                                  "voxel_keepouts": [], "symmetry": "none",
                                  "protected_regions": []}}
    root = endpoint.rstrip("/") + "/"
    submitted = _http_json(urljoin(root, "v1/jobs"), token=token, payload=payload)
    job_id = submitted["job_id"]; deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = _http_json(urljoin(root, f"v1/jobs/{job_id}"), token=token)
        if job["status"] == "succeeded":
            manifest_url = urljoin(root, job["artifact_manifest_url"].lstrip("/"))
            manifest = _http_json(manifest_url, token=token)
            candidate = manifest["candidates"][0]
            artifact_url = urljoin(root, candidate["artifact_url"].lstrip("/"))
            request = Request(artifact_url, headers={"Authorization": f"Bearer {token}",
                                                     "User-Agent": "DesignStudio-Component3D/1"})
            with urlopen(request, timeout=60) as response:
                glb = response.read()
            if not glb.startswith(b"glTF") or _sha256_bytes(glb) != candidate["sha256"]:
                raise Component3DAssetError("Hunyuan result failed GLB integrity validation")
            return glb, {"job_id": job_id, "input_digest": job["input_digest"],
                         "manifest_url": manifest_url, "model": manifest.get("model", ""),
                         "runtime": manifest.get("runtime", {})}
        if job["status"] in ("failed", "cancelled"):
            raise Component3DAssetError(
                f"Hunyuan job {job['status']}: {(job.get('error') or {}).get('message', '')}")
        time.sleep(0.25)
    raise Component3DAssetError("Hunyuan component generation timed out")


def materialize_component_asset(component: dict[str, Any], *,
                                asset_root: Path | None = None,
                                cloud_submitter: Callable[..., tuple[bytes, dict[str, Any]]] | None = None,
                                force: bool = False) -> dict[str, Any]:
    """Attach and return package_3d.asset; no human approval transaction is used."""
    package = component.setdefault("package_3d", {})
    current = package.get("asset") or {}
    current_path = Path(str(current.get("asset_uri", ""))).expanduser()
    if not force and current.get("status") in ("ready", "proxy_ready", "cloud_pending", "cloud_failed") \
            and current_path.is_file() and _sha256_path(current_path) == current.get("sha256"):
        endpoint_available = bool(os.environ.get("DESIGNSTUDIO_HUNYUAN_OMNI_URL", "").strip()
                                  and os.environ.get("DESIGNSTUDIO_HUNYUAN_OMNI_TOKEN", "").strip())
        if current.get("status") != "cloud_pending" or not endpoint_available:
            return current
    mpn = str((component.get("component") or {}).get("mpn") or "component")
    construction = package.get("construction") or {}
    luna_authored = construction.get("author") == "gpt-5.6-luna-xhigh"
    complexity = construction.get("complexity", "simple")
    method = construction.get("method", "parametric")
    complex_package = complexity == "complex" or method == "six_view_hunyuan"
    root = (asset_root or component_asset_library_dir()) / _safe(mpn)
    root.mkdir(parents=True, exist_ok=True)
    proxy_path = root / "datasheet-parametric.glb"
    meshes = meshes_from_component(component)
    dimensions = write_glb(
        meshes, proxy_path,
        generator=("gpt-5.6-luna-xhigh datasheet parametric" if luna_authored
                   else "DesignStudio legacy datasheet-parametric migration"))
    asset_path = proxy_path
    status = "ready" if not complex_package else "proxy_ready"
    generator = ("gpt-5.6-luna-xhigh-parametric" if luna_authored
                 else "designstudio-datasheet-parametric-migration")
    warnings: list[str] = ([] if luna_authored else [
        "legacy component record has no Luna construction plan; showing a footprint-scaled migration proxy"])
    six_views: list[dict[str, Any]] = []
    cloud: dict[str, Any] = {}
    if complex_package:
        six_views = render_six_views(meshes, root / "six-view")
        endpoint = os.environ.get("DESIGNSTUDIO_HUNYUAN_OMNI_URL", "").strip()
        token = os.environ.get("DESIGNSTUDIO_HUNYUAN_OMNI_TOKEN", "").strip()
        submitter = cloud_submitter or submit_hunyuan_omni
        if endpoint and token or cloud_submitter is not None:
            try:
                prompt = construction.get("hunyuan_prompt") or (
                    f"Exact electronic component package for {mpn}; preserve the supplied six "
                    "orthographic silhouettes and package dimensions. No PCB or background.")
                glb, cloud = submitter(views=six_views, dimensions=dimensions,
                                       prompt=prompt, endpoint=endpoint, token=token)
                generated_path = root / "hunyuan3d-omni-2.1.glb"
                generated_path.write_bytes(glb)
                asset_path = generated_path; status = "ready"
                generator = "hunyuan3d-omni-2.1-amd-rocm"
            except Exception as exc:
                status = "cloud_failed"
                warnings.append(f"Hunyuan3D cloud fallback failed; showing Luna proxy: {exc}")
        else:
            status = "cloud_pending"
            warnings.append("six views are ready; configure the Hunyuan3D Omni ROCm endpoint")
    asset = {"schema": "design-studio.component-3d-asset/1", "status": status,
             "format": "glb", "asset_uri": str(asset_path.resolve()),
             "sha256": _sha256_path(asset_path), "generator": generator,
             "dimensions_mm": {key: round(value, 6) for key, value in dimensions.items()},
             "model_to_footprint": list(IDENTITY_4X4), "complexity": complexity,
             "source": "datasheet_diagrams", "six_views": six_views,
             "cloud": cloud, "warnings": warnings}
    package["asset"] = asset
    return asset


def ensure_component_asset(component: dict[str, Any], **kwargs: Any) -> tuple[dict[str, Any], bool]:
    before = json.dumps((component.get("package_3d") or {}).get("asset"), sort_keys=True)
    materialize_component_asset(component, **kwargs)
    after = json.dumps((component.get("package_3d") or {}).get("asset"), sort_keys=True)
    return component, before != after

"""Dependency-free GLB triangle reader for FreeCAD component visualization."""
from __future__ import annotations

import json
import math
from pathlib import Path
import struct
from typing import Any


_COMPONENTS = {
    5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2),
    5125: ("I", 4), 5126: ("f", 4),
}
_TYPE_SIZE = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4,
              "MAT2": 4, "MAT3": 9, "MAT4": 16}


def _multiply(a: list[float], b: list[float]) -> list[float]:
    return [sum(a[row * 4 + k] * b[k * 4 + column] for k in range(4))
            for row in range(4) for column in range(4)]


def _node_matrix(node: dict[str, Any]) -> list[float]:
    if "matrix" in node:
        source = [float(value) for value in node["matrix"]]
        return [source[column * 4 + row] for row in range(4) for column in range(4)]
    translation = [float(value) for value in node.get("translation", [0, 0, 0])]
    scale = [float(value) for value in node.get("scale", [1, 1, 1])]
    x, y, z, w = [float(value) for value in node.get("rotation", [0, 0, 0, 1])]
    length = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / length, y / length, z / length, w / length
    rotation = [
        1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0,
        2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0,
        2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0,
        0, 0, 0, 1,
    ]
    scaling = [scale[0], 0, 0, 0, 0, scale[1], 0, 0,
               0, 0, scale[2], 0, 0, 0, 0, 1]
    result = _multiply(rotation, scaling)
    result[3], result[7], result[11] = translation
    return result


def _transform(matrix: list[float], value: tuple[float, ...]) -> tuple[float, float, float]:
    x, y, z = value[:3]
    return (matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3],
            matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7],
            matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11])


def read_glb(path: str | Path) -> list[dict[str, Any]]:
    payload = Path(path).read_bytes()
    if len(payload) < 20:
        raise ValueError("GLB is truncated")
    magic, version, total = struct.unpack_from("<4sII", payload, 0)
    if magic != b"glTF" or version != 2 or total != len(payload):
        raise ValueError("asset is not a valid GLB 2.0 file")
    offset = 12; document = None; binary = b""
    while offset + 8 <= len(payload):
        length, kind = struct.unpack_from("<I4s", payload, offset); offset += 8
        chunk = payload[offset:offset + length]; offset += length
        if kind == b"JSON": document = json.loads(chunk.rstrip(b" \0\t\r\n"))
        elif kind == b"BIN\0": binary = chunk
    if not isinstance(document, dict) or not binary:
        raise ValueError("GLB needs JSON and BIN chunks")

    def accessor(index: int) -> list[tuple[float, ...]]:
        spec = document["accessors"][index]
        if spec.get("sparse"):
            raise ValueError("sparse GLB accessors are not supported")
        view = document["bufferViews"][spec["bufferView"]]
        component_type = int(spec["componentType"])
        if component_type not in _COMPONENTS:
            raise ValueError("unsupported GLB component type")
        fmt, component_size = _COMPONENTS[component_type]
        width = _TYPE_SIZE[spec["type"]]
        packed = component_size * width
        stride = int(view.get("byteStride", packed))
        start = int(view.get("byteOffset", 0)) + int(spec.get("byteOffset", 0))
        values = []
        for item in range(int(spec["count"])):
            values.append(struct.unpack_from("<" + fmt * width, binary, start + item * stride))
        return values

    identity = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    instances: list[tuple[int, list[float]]] = []

    def visit(node_index: int, parent: list[float]) -> None:
        node = document["nodes"][node_index]
        world = _multiply(parent, _node_matrix(node))
        if "mesh" in node:
            instances.append((int(node["mesh"]), world))
        for child in node.get("children", []):
            visit(int(child), world)

    scenes = document.get("scenes") or []
    roots = scenes[int(document.get("scene", 0))].get("nodes", []) if scenes else range(len(document.get("nodes", [])))
    for root in roots:
        visit(int(root), identity)
    if not instances:
        instances = [(index, identity) for index in range(len(document.get("meshes", [])))]

    result = []
    materials = document.get("materials") or []
    for mesh_index, matrix in instances:
        for primitive in document["meshes"][mesh_index].get("primitives", []):
            if int(primitive.get("mode", 4)) != 4 or "POSITION" not in primitive.get("attributes", {}):
                continue
            positions = [_transform(matrix, value) for value in accessor(
                int(primitive["attributes"]["POSITION"]))]
            if "indices" in primitive:
                indices = [int(value[0]) for value in accessor(int(primitive["indices"]))]
            else:
                indices = list(range(len(positions)))
            if len(indices) % 3:
                raise ValueError("GLB triangle index count is not divisible by three")
            triangles = [tuple(indices[index:index + 3]) for index in range(0, len(indices), 3)]
            color = [0.12, 0.14, 0.16, 1.0]
            material_index = primitive.get("material")
            if isinstance(material_index, int) and 0 <= material_index < len(materials):
                color = (materials[material_index].get("pbrMetallicRoughness") or {}).get(
                    "baseColorFactor", color)
            # glTF units are metres; FreeCAD's default geometry unit is mm.
            result.append({"vertices_mm": [(x * 1000.0, y * 1000.0, z * 1000.0)
                                            for x, y, z in positions],
                           "triangles": triangles, "color": color})
    if not result:
        raise ValueError("GLB contains no uncompressed triangle primitives")
    return result

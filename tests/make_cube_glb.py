"""Write a 1 m cube as a GLB (glTF 2.0 binary), from plain JSON and one buffer.

The only model committed to this repository is the one this script makes: the
line view's tests need a real model file to load, and no artwork of anyone
else's belongs here. Standard library only, so it runs wherever the tests do.

    python tests/make_cube_glb.py out/filler.glb

The cube stands on the floor - its base at y = 0, centred on x and z - which is
where the line view expects a machine model's origin to be.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

GLB_MAGIC, GLB_VERSION = 0x46546C67, 2  # "glTF"
CHUNK_JSON, CHUNK_BIN = 0x4E4F534A, 0x004E4942  # "JSON", "BIN\0"


def _cube() -> tuple[list[float], list[int]]:
    """Positions (x, y, z per corner) and triangle indices, faces outward."""
    corners = [(x, y, z) for y in (0.0, 1.0) for z in (-0.5, 0.5) for x in (-0.5, 0.5)]
    # Corners: 0..3 the floor, 4..7 the top; within each, (x-,z-),(x+,z-),(x-,z+),(x+,z+).
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    indices = []
    for a, b, c, d in faces:
        indices += [a, b, c, a, c, d]
    return [v for corner in corners for v in corner], indices


def cube_glb() -> bytes:
    positions, indices = _cube()
    vertices = struct.pack(f"<{len(positions)}f", *positions)
    triangles = struct.pack(f"<{len(indices)}H", *indices)
    binary = vertices + triangles
    binary += b"\0" * (-len(binary) % 4)
    gltf = {
        "asset": {"version": "2.0", "generator": "fsmes tests/make_cube_glb.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "test-cube", "mesh": 0}],
        "meshes": [{"name": "test-cube", "primitives": [
            {"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "materials": [{"name": "grey", "doubleSided": True, "pbrMetallicRoughness": {
            "baseColorFactor": [0.6, 0.62, 0.66, 1.0], "metallicFactor": 0.2, "roughnessFactor": 0.7}}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(vertices), "target": 34962},
            {"buffer": 0, "byteOffset": len(vertices), "byteLength": len(indices) * 2, "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(positions) // 3, "type": "VEC3",
             "min": [-0.5, 0.0, -0.5], "max": [0.5, 1.0, 0.5]},
            {"bufferView": 1, "componentType": 5123, "count": len(indices), "type": "SCALAR"},
        ],
    }
    text = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    text += b" " * (-len(text) % 4)
    chunks = struct.pack("<II", len(text), CHUNK_JSON) + text
    chunks += struct.pack("<II", len(binary), CHUNK_BIN) + binary
    return struct.pack("<III", GLB_MAGIC, GLB_VERSION, 12 + len(chunks)) + chunks


def write_cube(path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(cube_glb())
    return path


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python tests/make_cube_glb.py <out.glb>")
    print(write_cube(Path(sys.argv[1])))

"""CPU/mock contract for the ROCm support optimizer."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

SERVICE = Path(__file__).resolve().parent
ROOT = SERVICE.parents[1]
sys.path.insert(0, str(ROOT / "services" / "ai-gateway"))
sys.path.insert(0, str(SERVICE))

from app import _deduplicate_path, app  # noqa: E402


def _support_spec():
    """Local immutable fixture; avoid importing gateway ``app`` under the same module name."""
    document = {
        "document_id": "agent-keyboard-enclosure",
        "revision": 7,
        "sha256": "0123456789abcdef" * 4,
    }
    return {
        "schema": "design-studio.support-generation/1",
        "document": document,
        "placements": [{
            "schema": "design-studio.component-placement/1",
            "placement_id": "12345678-1234-4234-8234-123456789abc",
            "component_id": "component.encoder",
            "component_sha256": "abcdef0123456789" * 4,
            "document": dict(document),
            "transform": {
                "translation_mm": [30.0, 25.0, 12.0],
                "rotation_xyzw": [0.0, 0.0, 0.0, 1.0],
            },
            "coordinate_system": "enclosure_local",
            "snap": {"grid": True, "surface": True, "axis": True,
                     "symmetry": False, "clearance": True},
            "locks": {"position": True, "orientation": True,
                      "surface_anchor": False},
            "clearance_mm": 1.0,
        }],
        "design_volume_mm": {
            "min": [0.0, 0.0, 0.0],
            "max": [120.0, 70.0, 25.0],
        },
        "anchors": [{"id": "encoder-mount", "position_mm": [30.0, 25.0, 8.0],
                     "kind": "mount"}],
        "forbidden_bounds_mm": [{"min": [25.0, 20.0, 6.0],
                                  "max": [35.0, 30.0, 18.0]}],
        "load_cases": [{"anchor_id": "encoder-mount",
                         "force_n": [0.0, 0.0, -12.0]}],
        "manufacturing": {"process": "injection_molding",
                           "minimum_wall_mm": 1.5,
                           "minimum_rib_mm": 1.0,
                           "clearance_mm": 0.5},
        "seed": 19,
    }


def request(method: str, path: str, **kwargs):
    async def call():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(call())


def main() -> int:
    assert _deduplicate_path([
        [1.0, 2.0, 3.0],
        [1.0, 2.0, 3.0],
        [4.0, 5.0, 6.0],
        [4.0, 5.0, 6.0],
    ]) == [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    health = request("GET", "/v1/health")
    assert health.status_code == 200 and health.json()["mock"] is True
    response = request("POST", "/v1/optimize-support", json={"spec": _support_spec()})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["schema"] == "design-studio.support-result/1"
    assert result["generator"]["authoritative_geometry"] is False
    assert result["required_host_checks"]
    stale = _support_spec()
    stale["placements"][0]["document"]["revision"] += 1
    assert request("POST", "/v1/optimize-support", json={"spec": stale}).status_code == 422
    print("SUPPORT_OPTIMIZER_CONTRACT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

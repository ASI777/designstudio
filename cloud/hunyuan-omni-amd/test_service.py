#!/usr/bin/env python3
"""CPU contract tests for jobs, provenance, determinism and signed artifacts."""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import importlib
import os
from pathlib import Path
import tempfile

import httpx


temporary = tempfile.TemporaryDirectory(prefix="hunyuan-omni-test-")
os.environ.update({
    "DS_HUNYUAN_STORE": temporary.name,
    "DS_HUNYUAN_API_TOKEN": "test-token",
    "DS_HUNYUAN_SIGNING_KEY": "test-signing-key",
    "DS_HUNYUAN_MOCK": "1",
    "DS_HUNYUAN_MOCK_DELAY_SECONDS": "0.03",
})
service = importlib.import_module("app")

HEADERS = {"Authorization": "Bearer test-token"}
REQUEST = {
    "configuration_id": "992bb75c-cfe8-4f5a-8bcd-91cdcebe3b44",
    "slot_id": "slot.upper_shell",
    "candidate_count": 2,
    "seeds": [101, 202],
    "input_package": {
        "prompt": "Controlled compact exterior with a continuous upper surface.",
        "reference_images": [
            {"view": view, "image_url": f"https://assets.example.test/{view}.png",
             "mask_url": f"https://assets.example.test/{view}-mask.png"}
            for view in ("front", "rear", "left", "right", "top", "bottom")
        ],
        "dimensions_mm": {"width": 120.0, "depth": 80.0, "height": 42.0},
        "point_controls": [
            {"position_mm": [0.0, 0.0, 18.0], "label": "surface"},
        ],
        "voxel_keepouts": [
            {"minimum_mm": [-30.0, -20.0, -15.0],
             "maximum_mm": [30.0, 20.0, 15.0]},
        ],
        "symmetry": "z",
        "protected_regions": [{"view": "front", "mask_digest": "0" * 64}],
    },
}


async def wait(client: httpx.AsyncClient, job_id: str) -> dict:
    for _ in range(200):
        response = await client.get(f"/v1/jobs/{job_id}", headers=HEADERS)
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("job did not reach a terminal state")


async def run() -> None:
    transport = httpx.ASGITransport(app=service.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unauthorized = await client.get("/v1/health")
        assert unauthorized.status_code == 401
        health = await client.get("/v1/health", headers=HEADERS)
        assert health.status_code == 200 and health.json()["mock"] is True

        invalid = dict(REQUEST)
        invalid["input_package"] = dict(REQUEST["input_package"])
        invalid["input_package"]["reference_images"] = list(REQUEST["input_package"]["reference_images"])
        invalid["input_package"]["reference_images"][0] = {
            "view": "front", "image_url": "http://unsigned/image.png",
            "mask_url": "https://assets.example.test/front-mask.png"}
        rejected = await client.post("/v1/jobs", headers=HEADERS, json=invalid)
        assert rejected.status_code == 422

        missing_view = dict(REQUEST)
        missing_view["input_package"] = dict(REQUEST["input_package"])
        missing_view["input_package"]["reference_images"] = REQUEST["input_package"]["reference_images"][:-1]
        rejected = await client.post("/v1/jobs", headers=HEADERS, json=missing_view)
        assert rejected.status_code == 422

        # Desktop component generation sends six local POV renders directly;
        # no public image host or signed upload round trip is required.
        png = b"\x89PNG\r\n\x1a\n" + b"designstudio-test-png"
        encoded = base64.b64encode(png).decode("ascii")
        embedded = copy.deepcopy(REQUEST)
        embedded["candidate_count"] = 1
        embedded["seeds"] = [303]
        embedded["input_package"]["reference_images"] = [
            {"view": view, "image_base64": encoded,
             "image_sha256": hashlib.sha256(png).hexdigest(),
             "mask_base64": encoded, "mask_sha256": hashlib.sha256(png).hexdigest()}
            for view in ("front", "rear", "left", "right", "top", "bottom")]
        embedded_response = await client.post("/v1/jobs", headers=HEADERS, json=embedded)
        assert embedded_response.status_code == 202, embedded_response.text
        embedded_job = await wait(client, embedded_response.json()["job_id"])
        assert embedded_job["status"] == "succeeded"
        assert (await client.delete(
            f"/v1/jobs/{embedded_job['job_id']}", headers=HEADERS)).status_code == 204

        cancellable = await client.post("/v1/jobs", headers=HEADERS, json=REQUEST)
        assert cancellable.status_code == 202
        cancelled = await client.post(
            f"/v1/jobs/{cancellable.json()['job_id']}/cancel", headers=HEADERS)
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        assert (await wait(client, cancellable.json()["job_id"]))["status"] == "cancelled"
        assert (await client.delete(
            f"/v1/jobs/{cancellable.json()['job_id']}", headers=HEADERS)).status_code == 204

        service.MAX_JOB_SECONDS = 0
        timed = await client.post("/v1/jobs", headers=HEADERS, json=REQUEST)
        timed_job = await wait(client, timed.json()["job_id"])
        assert timed_job["status"] == "failed"
        assert "exceeded" in timed_job["error"]["message"]
        assert (await client.delete(
            f"/v1/jobs/{timed.json()['job_id']}", headers=HEADERS)).status_code == 204
        service.MAX_JOB_SECONDS = 3600

        submissions = []
        for _ in range(2):
            response = await client.post("/v1/jobs", headers=HEADERS, json=REQUEST)
            assert response.status_code == 202, response.text
            submissions.append(response.json())
        assert submissions[0]["input_digest"] == submissions[1]["input_digest"]

        jobs = [await wait(client, item["job_id"]) for item in submissions]
        assert all(job["status"] == "succeeded" for job in jobs), jobs
        manifests = []
        candidate_bytes = []
        for job in jobs:
            response = await client.get(job["artifact_manifest_url"])
            assert response.status_code == 200, response.text
            manifest = response.json()
            manifests.append(manifest)
            assert manifest["schema"] == "design-studio.generation-artifacts/1"
            assert manifest["input_digest"] == job["input_digest"]
            assert manifest["parameters"]["seeds"] == [101, 202]
            assert manifest["parameters"]["dimension_order"] == "width_depth_height"
            assert manifest["parameters"]["conditioning"]["geometry"] == "six-view-visual-hull-v1"
            assert all(candidate["mesh_diagnostics"]["watertight"]
                       and candidate["bounding_box_error_mm"] == [0.0, 0.0, 0.0]
                       and candidate["dimensions_mm"] == {
                           "width": 120.0, "depth": 80.0, "height": 42.0}
                       and candidate["control_adherence"]["text_prompt"]
                       == "not_conditioned_by_pinned_checkpoint"
                       and not candidate["release_eligible"]
                       for candidate in manifest["candidates"])
            mesh = await client.get(manifest["candidates"][0]["artifact_url"])
            assert mesh.status_code == 200 and mesh.content[:4] == b"glTF"
            assert hashlib.sha256(mesh.content).hexdigest() == manifest["candidates"][0]["sha256"]
            candidate_bytes.append(mesh.content)
        assert candidate_bytes[0] == candidate_bytes[1]

        signed = jobs[0]["artifact_manifest_url"]
        tampered = signed[:-1] + ("0" if signed[-1] != "0" else "1")
        assert (await client.get(tampered)).status_code == 403

        for job in jobs:
            deleted = await client.delete(f"/v1/jobs/{job['job_id']}", headers=HEADERS)
            assert deleted.status_code == 204

        orphan_id = "4e47cb5c-2504-41f4-a632-4ef746dfdd8a"
        orphan = service.job_dir(orphan_id)
        orphan.mkdir()
        service.atomic_json(orphan / "job.json", {
            "schema": "design-studio.generation-service-job/1", "job_id": orphan_id,
            "status": "running", "created_utc": service.utc(), "progress": 0.3,
            "input_digest": "a" * 64, "request": REQUEST,
            "artifact_manifest_url": None, "error": None})
        assert service.recover_orphaned_jobs() == 1
        recovered = service.read_job(orphan_id)
        assert recovered["status"] == "failed" and recovered["error"]["code"] == "WORKER_LOST"


if __name__ == "__main__":
    asyncio.run(run())
    temporary.cleanup()
    print("HUNYUAN_OMNI_AMD_SERVICE_OK")

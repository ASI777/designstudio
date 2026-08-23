#!/usr/bin/env python3
"""Serve the live batch status, source images, generated GLBs and CAD previews."""
from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse


mimetypes.add_type("model/gltf-binary", ".glb")


class Handler(SimpleHTTPRequestHandler):
    server_version = "DesignStudioLive/1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/catalog":
            return self.catalog()
        return super().do_GET()

    def translate_path(self, path: str) -> str:
        parsed = unquote(urlparse(path).path)
        if parsed.startswith("/inputs/"):
            return str(self.server.inputs / parsed.removeprefix("/inputs/"))
        if parsed.startswith("/runs/"):
            return str(self.server.runs / parsed.removeprefix("/runs/"))
        relative = parsed.lstrip("/") or "index.html"
        return str(self.server.viewer / relative)

    def catalog(self) -> None:
        manifest = json.loads(self.server.manifest.read_text(encoding="utf-8"))
        products = []
        for group in manifest["groups"]:
            directory = self.server.runs / group["slug"]
            generated = directory / "omni.glb"
            if not generated.is_file():
                generated = directory / "bootstrap.glb"
            cad = directory / "cad-preview.glb"
            fit_path = directory / "fit-result.json"
            fit = json.loads(fit_path.read_text()) if fit_path.is_file() else {}
            shape_path = directory / "bootstrap-result.json"
            shape = json.loads(shape_path.read_text()) if shape_path.is_file() else {}
            omni_path = directory / "omni-result.json"
            omni = json.loads(omni_path.read_text()) if omni_path.is_file() else {}
            validation_path = directory / "host-validation.json"
            validation = (
                json.loads(validation_path.read_text())
                if validation_path.is_file() else {}
            )
            failure = directory / "FAILED"
            status = "failed" if failure.exists() else (
                (
                    "ready" if validation.get("release_eligible") is True
                    else "review"
                ) if generated.is_file() and cad.is_file()
                else "running" if directory.exists() else "queued"
            )
            products.append({
                "slug": group["slug"], "label": group["label"],
                "seed": group["seed"], "image_count": len(group["images"]),
                "images": [f"/inputs/{image['path']}" for image in group["images"]],
                "status": status,
                "generated_url": (
                    f"/runs/{group['slug']}/{generated.name}"
                    if generated.is_file() else None
                ),
                "cad_url": (
                    f"/runs/{group['slug']}/cad-preview.glb"
                    if cad.is_file() else None
                ),
                "vertices": omni.get("vertices", shape.get("vertices")),
                "faces": omni.get("faces", shape.get("faces")),
                "authority_status": omni.get(
                    "authority_status", shape.get("authority_status")
                ),
                "omni_generation_succeeded": omni.get(
                    "omni_generation_succeeded"
                ),
                "deviation": fit.get("deviation_metrics"),
                "ap242_roundtrip_valid": validation.get(
                    "ap242_roundtrip_valid"
                ),
                "valid_outer_shell": validation.get("valid_outer_shell"),
                "continuity_verified": validation.get("continuity_verified"),
                "release_eligible": validation.get("release_eligible"),
            })
        payload = json.dumps({
            "schema": "design-studio.live-product-catalog/1",
            "products": products,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--viewer", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    server.viewer, server.inputs = args.viewer.resolve(), args.inputs.resolve()
    server.runs, server.manifest = args.runs.resolve(), args.manifest.resolve()
    print(f"VIEWER_READY http://{args.bind}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

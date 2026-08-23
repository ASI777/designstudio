#!/usr/bin/env python3
"""Ask the pinned copilot to prioritize CAD fidelity work, without CAD authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def parse_json_object(content: str) -> dict:
    """Accept one JSON object and reject prose before or after it."""
    stripped = content.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1]).strip()
    value, end = json.JSONDecoder().raw_decode(stripped)
    if stripped[end:].strip() or not isinstance(value, dict):
        raise ValueError("model review is not one JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18001")
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text())
    inventory_sha = inventory["inventory_sha256"]
    system = (
        "You review component CAD fidelity for a hardware-design demo. "
        "You have no geometry or release authority. Never invent dimensions, "
        "supplier assets, approvals, or fidelity. Return only a JSON object with "
        "keys hero_components, systemic_findings, demo_recommendation. "
        "hero_components must contain at most five objects with exact mpn, rank, "
        "reason, required_feature_operations, and evidence_needed. Rank parts "
        "whose visible geometry most affects a PCB 3D demo, not tiny passives."
    )
    user = json.dumps(inventory, sort_keys=True, separators=(",", ":"))
    payload = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": 2048,
        "stream": False,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
        "cache_salt": hashlib.sha256(
            f"component-fidelity-review/1:{inventory_sha}".encode()
        ).hexdigest(),
    }
    request_bytes = json.dumps(payload, separators=(",", ":")).encode()
    request = urllib.request.Request(
        f"{args.base_url.rstrip('/')}/v1/chat/completions",
        data=request_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        completion = json.loads(response.read())
    content = completion["choices"][0]["message"]["content"]
    review = parse_json_object(content)
    if set(review) != {
            "hero_components", "systemic_findings", "demo_recommendation"}:
        raise ValueError("model review fields are invalid")
    heroes = review["hero_components"]
    if not isinstance(heroes, list) or len(heroes) > 5:
        raise ValueError("model selected more than five hero components")
    known = {item["mpn"] for item in inventory["components"]}
    for hero in heroes:
        if set(hero) != {
                "mpn", "rank", "reason", "required_feature_operations",
                "evidence_needed"} or hero["mpn"] not in known:
            raise ValueError("model review contains an unknown or malformed component")
    artifact = {
        "schema": "design-studio.component-cad-fidelity-review/1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "model_revision": args.model_revision,
        "inventory_sha256": inventory_sha,
        "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
        "review": review,
        "geometry_authority": False,
        "publication_authorized": False,
    }
    material = json.dumps(
        artifact, sort_keys=True, separators=(",", ":")
    ).encode()
    artifact["review_sha256"] = hashlib.sha256(material).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

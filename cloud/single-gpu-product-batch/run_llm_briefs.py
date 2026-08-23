#!/usr/bin/env python3
"""Create digest-bound engineering briefs from every supplied product image.

The script talks to an OpenAI-compatible multimodal endpoint.  It deliberately
labels inferred measurements as hypotheses: the LLM may organize evidence and
propose geometry, but it never promotes generated dimensions to CAD authority.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image


SCHEMA = "design-studio.multiview-engineering-brief/1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def image_data_url(path: Path, max_edge: int) -> str:
    """Encode a bounded review copy while retaining original digests in output."""
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=88, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:].lstrip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model response does not contain a JSON object")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model response root must be an object")
    return value


def request_json(endpoint: str, payload: dict, timeout: int) -> dict:
    request = Request(
        endpoint.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    content = result["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    brief = extract_json(content)
    return {"brief": brief, "usage": result.get("usage", {})}


def prompt_for(group: dict) -> str:
    dimensions = group["dimensions_mm"]
    return f"""
You are the geometry evidence router for a deterministic CAD reconstruction
pipeline. Analyze ALL attached images as views or design evidence for one
product: {group['label']}.

The provisional envelope is {dimensions}. It is a hypothesis only. Images may
contain browser chrome, captions, hands, rooms, unrelated backgrounds, or
concept variants. Reconcile repeated geometry, identify contradictions, and
produce a concise JSON engineering brief with exactly these top-level keys:

product_summary, image_assessments, canonical_features, symmetry_and_axes,
silhouette_constraints, openings_and_interfaces, assembly_breakdown,
material_and_finish_hypotheses, dimensional_hypothesis_mm,
uncertainties, omni_geometry_prompt, cad_reconstruction_plan,
verification_landmarks.

image_assessments must contain one entry per attached image in attachment order
with index, inferred_view, usable_geometry, occlusions, and notable_evidence.
verification_landmarks must be an array of measurable feature descriptions.
dimensional_hypothesis_mm must include width, height, depth, provenance, and
confidence. The Omni prompt must request one coherent watertight exterior and
must exclude backgrounds and browser UI.

Do not claim supplier, STEP, B-Rep, dimensional, or manufacturing authority.
Do not invent hidden mechanisms as fact. Return JSON only.
""".strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:18011")
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--max-edge", type=int, default=1024)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    args.runs.mkdir(parents=True, exist_ok=True)
    summary = []
    for group in manifest["groups"]:
        directory = args.runs / group["slug"]
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / "llm-brief.json"
        if output.is_file():
            existing = json.loads(output.read_text(encoding="utf-8"))
            if (
                existing.get("group_sha256") == group["group_sha256"]
                and existing.get("model_revision") == args.model_revision
            ):
                summary.append({"slug": group["slug"], "status": "resumed"})
                print(f"LLM_RESUME {group['slug']}", flush=True)
                continue

        content: list[dict] = [{"type": "text", "text": prompt_for(group)}]
        evidence = []
        for ordinal, item in enumerate(group["images"]):
            path = args.inputs / item["path"]
            actual = sha256(path)
            if actual != item["sha256"]:
                raise RuntimeError(f"digest mismatch for {path}")
            content.append(
                {
                    "type": "text",
                    "text": f"Attachment {ordinal}; source index {item['index']}.",
                }
            )
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_data_url(path, args.max_edge),
                        "detail": "high",
                    },
                }
            )
            evidence.append(
                {
                    "attachment_index": ordinal,
                    "source_index": item["index"],
                    "path": item["path"],
                    "sha256": actual,
                }
            )

        payload = {
            "model": args.model,
            "temperature": 0.1,
            "max_tokens": 5000,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You organize multimodal product evidence for CAD. "
                        "Generated claims remain review-only until deterministic "
                        "geometry and metrology checks pass."
                    ),
                },
                {"role": "user", "content": content},
            ],
        }
        last_error = None
        for attempt in range(args.retries + 1):
            try:
                response = request_json(args.endpoint, payload, args.timeout)
                break
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as error:
                last_error = error
                if attempt == args.retries:
                    raise
                print(
                    f"LLM_RETRY {group['slug']} {attempt + 1} {error}",
                    flush=True,
                )
                time.sleep(5 * (attempt + 1))
        else:
            raise RuntimeError(str(last_error))

        record = {
            "schema": SCHEMA,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "authority": "review_only_llm_evidence",
            "group_slug": group["slug"],
            "group_sha256": group["group_sha256"],
            "model": args.model,
            "model_revision": args.model_revision,
            "temperature": payload["temperature"],
            "input_evidence": evidence,
            "brief": response["brief"],
            "usage": response["usage"],
        }
        temporary = output.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(output)
        summary.append({"slug": group["slug"], "status": "completed"})
        print(f"LLM_COMPLETE {group['slug']}", flush=True)

    summary_path = args.runs / "llm-summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "schema": "design-studio.llm-batch-summary/1",
                "model": args.model,
                "model_revision": args.model_revision,
                "groups": summary,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"LLM_BATCH_COMPLETE {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

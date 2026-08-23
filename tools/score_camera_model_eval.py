#!/usr/bin/env python3
"""Score identical camera-evidence model runs without treating prose as authority.

The scorer deliberately validates the raw response before awarding semantic
coverage.  A model can describe every visible feature and still fail the
DesignStudio contract; such a response remains useful for comparison but is
not eligible to enter canonical product state.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Any


MODEL_CONFIG = {
    "sol-high": {"model": "gpt-5.6-sol", "effort": "high", "input": 5.0, "cached": 0.5, "output": 30.0},
    "terra-high": {"model": "gpt-5.6-terra", "effort": "high", "input": 2.0, "cached": 0.2, "output": 12.0},
    "luna-max": {"model": "gpt-5.6-luna", "effort": "max", "input": 0.2, "cached": 0.02, "output": 1.2},
}

FEATURES = {
    "body_silhouette": ("silhouette", "rectangular body", "rounded body"),
    "lens_interface": ("lens", "optical"),
    "rear_display": ("rear display", "screen", "display opening"),
    "top_controls": ("top control", "dial", "shutter"),
    "side_opening": ("usb", "side opening", "slot"),
    "rib_texture": ("rib", "groove", "fluted"),
    "seam": ("seam", "split line", "panel boundary"),
    "appearance": ("appearance", "finish", "silver", "dark"),
}
UNKNOWNS = {
    "scale": ("scale", "dimension", "millimet"),
    "hidden_geometry": ("hidden", "internal", "concealed"),
    "materials": ("material", "substrate"),
    "mechanism": ("mechanism", "actuation", "motion"),
    "manufacturing": ("manufacturing", "production process"),
    "release_status": ("release", "concept", "prototype"),
}


def _usage(events_path: Path) -> dict[str, int]:
    usage: dict[str, int] = {}
    for line in events_path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed":
            usage = {key: int(value) for key, value in event.get("usage", {}).items()}
    return usage


def _schema_errors(value: Any, schema: dict[str, Any]) -> list[str]:
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return ["jsonschema unavailable"]
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path))
    return [f"{'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}" for error in errors]


def _keyword_coverage(text: str, rubric: dict[str, tuple[str, ...]]) -> dict[str, bool]:
    lowered = text.lower()
    return {name: any(term in lowered for term in terms) for name, terms in rubric.items()}


def _estimated_cost(usage: dict[str, int], prices: dict[str, Any]) -> float:
    total_input = usage.get("input_tokens", 0)
    cached = usage.get("cached_input_tokens", 0)
    uncached = max(0, total_input - cached)
    input_multiplier = 2.0 if total_input > 272_000 else 1.0
    output_multiplier = 1.5 if total_input > 272_000 else 1.0
    return round((uncached * prices["input"] + cached * prices["cached"]) * input_multiplier / 1_000_000
                 + usage.get("output_tokens", 0) * prices["output"] * output_multiplier / 1_000_000, 6)


def _observed_latency(path: Path) -> int | None:
    """Return filesystem-observed wall time when the platform exposes birth time."""
    try:
        completed = subprocess.run(
            ["stat", "-c", "%W %Y", str(path)], check=True,
            capture_output=True, text=True)
        born, modified = (int(value) for value in completed.stdout.split())
        if born > 0 and modified >= born:
            return modified - born
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def score_run(name: str, directory: Path, schema: dict[str, Any]) -> dict[str, Any]:
    response_path = directory / f"{name}.json"
    events_path = directory / f"{name}-events.jsonl"
    value = json.loads(response_path.read_text(encoding="utf-8"))
    rendered = json.dumps(value, sort_keys=True)
    feature_coverage = _keyword_coverage(rendered, FEATURES)
    unknown_coverage = _keyword_coverage(rendered, UNKNOWNS)
    errors = _schema_errors(value, schema)
    usage = _usage(events_path)
    latency = _observed_latency(events_path)
    prices = MODEL_CONFIG[name]
    return {
        "configuration": {"model": prices["model"], "reasoning_effort": prices["effort"]},
        "raw_json_valid": True,
        "production_schema_valid": not errors,
        "schema_error_count": len(errors),
        "first_schema_errors": errors[:5],
        "grounded_feature_recall": round(sum(feature_coverage.values()) / len(feature_coverage), 3),
        "feature_coverage": feature_coverage,
        "unknown_handling_recall": round(sum(unknown_coverage.values()) / len(unknown_coverage), 3),
        "unknown_coverage": unknown_coverage,
        "browser_chrome_excluded": "browser" in rendered.lower() and "exclud" in rendered.lower(),
        "tool_success": bool(usage),
        "latency_seconds": latency,
        "usage": usage,
        "estimated_api_cost_usd": _estimated_cost(usage, prices),
        "eligible_for_canonical_state": not errors,
    }


def build_report(directory: Path, schema_path: Path) -> dict[str, Any]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    runs = {name: score_run(name, directory, schema) for name in MODEL_CONFIG}
    return {
        "schema": "design-studio.model-comparison/1",
        "task": "ai-compact-camera-visual-evidence",
        "identical_inputs": True,
        "runs": runs,
        "decision": {
            "default": "sol-high",
            "reason": "No raw run satisfied the production schema. Sol High remains the supervised default; deterministic validation and normalization are mandatory.",
            "luna_bulk_promotion": False,
            "canonical_state_written": False,
        },
        "pricing_sources": {
            "sol": "https://developers.openai.com/api/docs/models/gpt-5.6-sol",
            "terra": "https://developers.openai.com/api/docs/models/gpt-5.6-terra",
            "luna": "https://developers.openai.com/api/docs/models/gpt-5.6-luna",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("schema", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report(args.directory.resolve(), args.schema.resolve())
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

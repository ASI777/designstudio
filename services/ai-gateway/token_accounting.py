"""Provider-neutral token accounting without a hard inference budget.

The software records provider-reported token counts and cache reuse.  It does
not estimate missing counts or stop engineering work at an arbitrary ceiling.
Optimization advice is evidence-based and never changes the requested task.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


SCHEMA = "design-studio.inference-usage/1"


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def unavailable_usage(*, provider: str, model: str, purpose: str,
                      reason: str) -> dict[str, Any]:
    record = {
        "schema": SCHEMA,
        "status": "unavailable",
        "provider": provider,
        "model": model,
        "purpose": purpose,
        "measurement": "provider_reported_only",
        "budget_policy": "observe_only_no_hard_cap",
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "cached_input_tokens": None,
        "uncached_input_tokens": None,
        "cache_reuse_ratio": None,
        "reason": reason,
        "optimizations": ["Require the inference provider to return an exact usage object."],
    }
    record["usage_digest"] = _canonical_digest(record)
    return record


def normalize_usage(raw: Any, *, provider: str, model: str, purpose: str,
                    cache_requested: bool) -> dict[str, Any]:
    """Normalize OpenAI-compatible/vLLM usage without inventing token counts."""
    if not isinstance(raw, dict):
        return unavailable_usage(
            provider=provider, model=model, purpose=purpose,
            reason="inference provider omitted usage",
        )
    input_tokens = _count(raw.get("prompt_tokens"), "usage.prompt_tokens")
    output_tokens = _count(raw.get("completion_tokens"), "usage.completion_tokens")
    total_tokens = _count(raw.get("total_tokens"), "usage.total_tokens")
    if total_tokens != input_tokens + output_tokens:
        raise ValueError("usage.total_tokens must equal prompt_tokens + completion_tokens")
    details = raw.get("prompt_tokens_details") or {}
    if not isinstance(details, dict):
        raise ValueError("usage.prompt_tokens_details must be an object")
    cached_tokens = details.get("cached_tokens", raw.get("cached_tokens", 0))
    cached_tokens = _count(cached_tokens, "usage.cached_tokens")
    if cached_tokens > input_tokens:
        raise ValueError("cached input tokens cannot exceed prompt tokens")
    uncached_tokens = input_tokens - cached_tokens
    reuse = cached_tokens / input_tokens if input_tokens else 0.0
    optimizations: list[str] = []
    if cache_requested and input_tokens and reuse < 0.25:
        optimizations.append(
            "Increase the stable digest-bound prefix; requested cache reuse is below 25%."
        )
    if not cache_requested and input_tokens >= 4096:
        optimizations.append(
            "Move stable schemas, standards, and product context into a project-scoped cached prefix."
        )
    if output_tokens > max(1024, input_tokens):
        optimizations.append(
            "Use typed concise tool results for intermediate steps; retain detailed reasoning in artifacts."
        )
    if not optimizations:
        optimizations.append(
            "Current request shows no obvious token-reuse defect; preserve task fidelity."
        )
    record = {
        "schema": SCHEMA,
        "status": "measured",
        "provider": provider,
        "model": model,
        "purpose": purpose,
        "measurement": "provider_reported_only",
        "budget_policy": "observe_only_no_hard_cap",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_input_tokens": cached_tokens,
        "uncached_input_tokens": uncached_tokens,
        "cache_reuse_ratio": round(reuse, 6),
        "reason": None,
        "optimizations": optimizations,
    }
    record["usage_digest"] = _canonical_digest(record)
    return record


def aggregate_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [record for record in records if record.get("status") == "measured"]
    missing = len(records) - len(measured)
    total_input = sum(_count(record.get("input_tokens"), "input_tokens")
                      for record in measured)
    total_output = sum(_count(record.get("output_tokens"), "output_tokens")
                       for record in measured)
    total_cached = sum(_count(record.get("cached_input_tokens"), "cached_input_tokens")
                       for record in measured)
    summary = {
        "schema": "design-studio.inference-usage-summary/1",
        "budget_policy": "observe_only_no_hard_cap",
        "record_count": len(records),
        "measured_record_count": len(measured),
        "unavailable_record_count": missing,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "cached_input_tokens": total_cached,
        "uncached_input_tokens": total_input - total_cached,
        "cache_reuse_ratio": round(total_cached / total_input, 6) if total_input else 0.0,
    }
    summary["summary_digest"] = _canonical_digest(summary)
    return summary


__all__ = ["SCHEMA", "aggregate_usage", "normalize_usage", "unavailable_usage"]

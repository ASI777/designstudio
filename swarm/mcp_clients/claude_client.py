"""Anthropic Claude client for swarm agents.
   Falls back gracefully when ANTHROPIC_API_KEY is not set (local-only mode)."""

import os
import json

try:
    import anthropic
    _HAS_SDK = True
except ImportError:
    _HAS_SDK = False

import yaml
from pathlib import Path

_cfg = yaml.safe_load(open(Path(__file__).parents[2] / "config.yaml"))["claude"]

OPUS   = _cfg["opus_model"]
SONNET = _cfg["sonnet_model"]
HAIKU  = _cfg["haiku_model"]


def _client():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — Claude unavailable")
    if not _HAS_SDK:
        raise RuntimeError("anthropic SDK not installed: pip install anthropic")
    return anthropic.Anthropic(api_key=key)


def generate(prompt: str, system: str = "", model: str = SONNET, max_tokens: int = 8192) -> str:
    client = _client()
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    msg = client.messages.create(**kwargs)
    return msg.content[0].text


def opus(prompt: str, system: str = "", max_tokens: int = 8192) -> str:
    return generate(prompt, system=system, model=OPUS, max_tokens=max_tokens)


def sonnet(prompt: str, system: str = "", max_tokens: int = 8192) -> str:
    return generate(prompt, system=system, model=SONNET, max_tokens=max_tokens)


def haiku(prompt: str, system: str = "", max_tokens: int = 4096) -> str:
    return generate(prompt, system=system, model=HAIKU, max_tokens=max_tokens)


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and _HAS_SDK

"""Ollama REST client — same interface as the Claude client so the router
   can swap them transparently."""

import json
import urllib.request
import urllib.error
import yaml
from pathlib import Path

_cfg = yaml.safe_load(open(Path(__file__).parents[2] / "config.yaml"))["local_model"]

HOST  = _cfg["host"]
MODEL = _cfg["model"]
CTX   = _cfg["context_length"]
TEMP  = _cfg["temperature"]
TIMEOUT = _cfg["timeout_seconds"]


def generate(prompt: str, system: str = "", max_tokens: int = 4096) -> str:
    """Send a prompt to the local Ollama model and return the response text."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = json.dumps({
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": TEMP,
            "num_ctx": CTX,
            "num_predict": max_tokens,
        },
    }).encode()

    req = urllib.request.Request(
        f"{HOST}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read())
            return data["message"]["content"]
    except urllib.error.URLError as e:
        raise RuntimeError(f"Ollama unreachable at {HOST}: {e}") from e


def health() -> dict:
    """Return basic server health info."""
    try:
        with urllib.request.urlopen(f"{HOST}/api/tags", timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            return {"status": "ok", "models": models}
    except Exception as e:
        return {"status": "error", "error": str(e)}


if __name__ == "__main__":
    h = health()
    print("Health:", h)
    if h["status"] == "ok":
        reply = generate(
            "Write a one-line C++ function that adds two int64_t values.",
            system="You are a C++ expert. Reply with code only.",
        )
        print("Test reply:", reply)

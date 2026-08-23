"""LLM backend for the DesignStudio agents.

Prefers the local **Claude CLI** (`claude -p`), which authenticates with the
user's existing Claude login — no ANTHROPIC_API_KEY required. Falls back to the
Anthropic API only if a key is configured. This lets the in-app AI chat work for
users who have Claude Code installed but no API key.

Override with env vars:
  DS_FORCE_API=1     ignore the CLI, always use the API
  CLAUDE_CLI=/path   explicit path to the claude binary
"""
from __future__ import annotations
import os
import shutil
import subprocess


def cli_path() -> str | None:
    explicit = os.environ.get("CLAUDE_CLI")
    if explicit and os.path.exists(explicit):
        return explicit
    return shutil.which("claude")


def complete(system: str, user: str, model: str | None = None,
             max_tokens: int = 8192, timeout: int = 600) -> str:
    """Return the model's text completion for (system, user)."""
    cli = cli_path()
    if cli and not os.environ.get("DS_FORCE_API"):
        cmd = [cli, "-p", "--output-format", "text"]
        if system:
            cmd += ["--system-prompt", system]
        if model:
            cmd += ["--model", model]
        try:
            r = subprocess.run(cmd, input=user, capture_output=True,
                               text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"claude CLI timed out after {timeout}s") from e
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
        # CLI failed — fall back to the API only if a key exists, else surface why.
        if not os.environ.get("ANTHROPIC_API_KEY"):
            detail = (r.stderr or r.stdout or "no output").strip()
            raise RuntimeError(
                "Claude CLI call failed (exit "
                f"{r.returncode}): {detail[:400]}\n"
                "Make sure you are logged in: run `claude` once in a terminal."
            )

    # API fallback
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError(
            "No working Claude CLI and the anthropic SDK is not installed."
        ) from e
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=model or "claude-opus-4-8",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text


def complete_vision(system: str, user: str, image_paths: list[str] | None = None,
                    b64_images: list[dict] | None = None, model: str | None = None,
                    max_tokens: int = 8192, timeout: int = 600) -> str:
    """Vision completion. The CLI backend reads the page image files with its
    Read tool; the API backend uses base64 image blocks."""
    cli = cli_path()
    if cli and not os.environ.get("DS_FORCE_API"):
        refs = "\n".join(f"- {p}" for p in (image_paths or []))
        prompt = user
        if refs:
            prompt += ("\n\nThe datasheet pages are image files. Use the Read tool "
                       "to view each before answering:\n" + refs)
        cmd = [cli, "-p", "--output-format", "text", "--allowedTools", "Read"]
        if system:
            cmd += ["--system-prompt", system]
        if model:
            cmd += ["--model", model]
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True,
                               text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"claude CLI timed out after {timeout}s") from e
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
        if not os.environ.get("ANTHROPIC_API_KEY"):
            detail = (r.stderr or r.stdout or "no output").strip()
            raise RuntimeError(
                f"Claude CLI vision call failed (exit {r.returncode}): {detail[:400]}")

    import anthropic
    client = anthropic.Anthropic()
    content: list[dict] = list(b64_images or [])
    content.append({"type": "text", "text": user})
    resp = client.messages.create(
        model=model or "claude-opus-4-8", max_tokens=max_tokens,
        system=system, messages=[{"role": "user", "content": content}],
    )
    return resp.content[0].text


def backend_name() -> str:
    if cli_path() and not os.environ.get("DS_FORCE_API"):
        return "claude-cli"
    return "anthropic-api"

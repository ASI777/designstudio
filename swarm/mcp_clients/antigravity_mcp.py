#!/usr/bin/env python3
"""MCP bridge: exposes Google AntiGravity models to Claude Code as MCP tools.

Run as a stdio MCP server — Claude Code adds this to its mcpServers config.
Each AntiGravity model becomes a callable tool; Claude can delegate work to
Gemini 3.5 Flash, Gemini 3.1 Pro, GPT-OSS 120B, etc. without leaving the
Claude orchestration loop.
"""

import os
import re
import subprocess
import sys
import json
from pathlib import Path

_site = Path.home() / ".local/lib/python3.14/site-packages"
if str(_site) not in sys.path:
    sys.path.insert(0, str(_site))

from mcp.server.fastmcp import FastMCP

AGY = str(Path.home() / ".local/bin/agy")
REPO = str(Path(__file__).parents[2])

# Model short-names → agy --model identifiers
MODELS = {
    "gemini_flash_medium": "Gemini 3.5 Flash (Medium)",
    "gemini_flash_high":   "Gemini 3.5 Flash (High)",
    "gemini_flash_low":    "Gemini 3.5 Flash (Low)",
    "gemini_pro_high":     "Gemini 3.1 Pro (High)",
    "gemini_pro_low":      "Gemini 3.1 Pro (Low)",
    "claude_sonnet":       "Claude Sonnet 4.6 (Thinking)",
    "claude_opus":         "Claude Opus 4.6 (Thinking)",
    "gpt_oss_120b":        "GPT-OSS 120B (Medium)",
}

mcp = FastMCP(
    "antigravity-bridge",
    instructions=(
        "Bridge to Google AntiGravity models (Gemini 3.5 Flash, Gemini 3.1 Pro, "
        "Claude Sonnet/Opus via AntiGravity, GPT-OSS 120B). "
        "Use these tools to delegate tasks to a specific model. "
        "All tools accept a prompt string and return the model's response as text."
    ),
)


_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]|\r')
_SPINNER  = re.compile(r'^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏\[K]')


def _strip_ansi(text: str) -> str:
    lines = _ANSI_RE.sub('', text).splitlines()
    return '\n'.join(l for l in lines if not _SPINNER.match(l)).strip()


def _agy_env() -> dict:
    """Full env with ~/.local/bin guaranteed on PATH."""
    env = os.environ.copy()
    local_bin = str(Path.home() / ".local/bin")
    if local_bin not in env.get("PATH", ""):
        env["PATH"] = local_bin + ":" + env.get("PATH", "/usr/bin:/bin")
    return env


def _call_agy(model_id: str, prompt: str, timeout: int = 60,
               with_workspace: bool = False) -> str:
    """Call agy via script(1) so it gets a PTY — required for agy to run headless.
    with_workspace=True passes --add-dir so agy reads the repo before answering;
    use only for tasks that genuinely need codebase context (adds ~20-40 s).
    """
    agy_timeout = f"{timeout}s"
    agy_args = [AGY, "--model", model_id, "-p", prompt, "--print-timeout", agy_timeout]
    if with_workspace:
        agy_args += ["--add-dir", REPO]

    # Shell-quote the inner command for script -c
    inner = " ".join(
        "'" + a.replace("'", "'\\''") + "'" for a in agy_args
    )
    result = subprocess.run(
        ["script", "-q", "-c", inner, "/dev/null"],
        capture_output=True,
        text=True,
        timeout=timeout + 15,
        env=_agy_env(),
    )
    out = _strip_ansi(result.stdout)
    if not out and result.stderr:
        return f"[agy error] {result.stderr.strip()}"
    return out


# ── One tool per model ────────────────────────────────────────────────────────

@mcp.tool()
def agy_gemini_flash(prompt: str) -> str:
    """Send a prompt to Gemini 3.5 Flash (Medium) via AntiGravity.

    Best for: fast code generation, boilerplate, JSON schema edits,
    documentation, build log triage, test scaffolding.
    Cost: low. Speed: fastest.
    """
    return _call_agy(MODELS["gemini_flash_medium"], prompt, timeout=60)


@mcp.tool()
def agy_gemini_flash_high(prompt: str) -> str:
    """Send a prompt to Gemini 3.5 Flash (High reasoning) via AntiGravity.

    Best for: medium-complexity code review, moderate C++ implementations,
    JSON validation, first-pass DRC rule checks.
    Cost: medium. Speed: fast.
    """
    return _call_agy(MODELS["gemini_flash_high"], prompt, timeout=90)


@mcp.tool()
def agy_gemini_pro(prompt: str) -> str:
    """Send a prompt to Gemini 3.1 Pro (High reasoning) via AntiGravity.

    Best for: complex C++ algorithms, multi-file refactors, physics math
    validation, push-and-shove router design, cross-file architecture.
    Pass codebase context automatically (--add-dir).
    Cost: high. Speed: slower.
    """
    return _call_agy(MODELS["gemini_pro_high"], prompt, timeout=240, with_workspace=True)


@mcp.tool()
def agy_claude_sonnet(prompt: str) -> str:
    """Send a prompt to Claude Sonnet 4.6 (Thinking) via AntiGravity.

    Same capability as direct Claude Sonnet but routed through AntiGravity,
    useful when the Anthropic API budget cap is hit.
    Cost: medium. Speed: medium.
    """
    return _call_agy(MODELS["claude_sonnet"], prompt, timeout=120)


@mcp.tool()
def agy_claude_opus(prompt: str) -> str:
    """Send a prompt to Claude Opus 4.6 (Thinking) via AntiGravity.

    Fallback Opus path through AntiGravity when Anthropic API budget is
    exhausted. Use for novel algorithm design, physics validation.
    Cost: high. Speed: slow.
    """
    return _call_agy(MODELS["claude_opus"], prompt, timeout=240, with_workspace=True)


@mcp.tool()
def agy_gpt_oss(prompt: str) -> str:
    """Send a prompt to GPT-OSS 120B (Medium) via AntiGravity.

    Best for: independent second-opinion code review, diverse-perspective
    validation of DRC rules or physics formulas.
    Cost: medium. Speed: medium.
    """
    return _call_agy(MODELS["gpt_oss_120b"], prompt, timeout=120)


@mcp.tool()
def agy_models() -> str:
    """List all models available through the AntiGravity CLI.

    Returns the live model list from agy — useful to check availability
    before choosing a specific model tool.
    """
    result = subprocess.run(
        ["script", "-q", "-c", f"'{AGY}' models", "/dev/null"],
        capture_output=True, text=True, timeout=30, env=_agy_env(),
    )
    return _strip_ansi(result.stdout) or result.stderr.strip()


@mcp.tool()
def agy_best_for(task_type: str, prompt: str) -> str:
    """Auto-route a prompt to the best AntiGravity model for the task type.

    task_type options:
      fast        → Gemini 3.5 Flash Medium  (boilerplate, docs, triage)
      complex     → Gemini 3.1 Pro High      (algorithms, physics, architecture)
      review      → GPT-OSS 120B             (second-opinion code review)
      fallback    → Claude Sonnet via agy    (when Anthropic budget is exhausted)

    Returns the model used and its response as JSON:
      {"model": "...", "response": "..."}
    """
    routing = {
        "fast":     ("gemini_flash_medium", MODELS["gemini_flash_medium"]),
        "complex":  ("gemini_pro_high",     MODELS["gemini_pro_high"]),
        "review":   ("gpt_oss_120b",        MODELS["gpt_oss_120b"]),
        "fallback": ("claude_sonnet",       MODELS["claude_sonnet"]),
    }
    key = task_type.lower().strip()
    if key not in routing:
        key = "fast"
    name, model_id = routing[key]
    timeout = 300 if key == "complex" else 180
    response = _call_agy(model_id, prompt, timeout=timeout)
    return json.dumps({"model": model_id, "task_type": key, "response": response})


if __name__ == "__main__":
    mcp.run(transport="stdio")

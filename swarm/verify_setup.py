#!/usr/bin/env python3
"""Run after installation to confirm the swarm stack is healthy."""

import sys
import subprocess
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
OLLAMA = Path.home() / ".local/bin/ollama"
OLLAMA_HOST = "http://127.0.0.1:11434"
MODEL = "qwen2.5-coder:7b-instruct-q4_K_M"

ok = True

def check(label, passed, detail=""):
    global ok
    sym = "OK" if passed else "FAIL"
    print(f"  [{sym}] {label}" + (f": {detail}" if detail else ""))
    if not passed:
        ok = False

print("\n=== schematic_designer swarm — setup verification ===\n")

# 1. Ollama binary
check("Ollama binary", OLLAMA.exists(), str(OLLAMA))

# 2. Ollama server reachable
try:
    with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=5) as r:
        data = json.loads(r.read())
    models = [m["name"] for m in data.get("models", [])]
    check("Ollama server", True, f"listening at {OLLAMA_HOST}")
    check(f"Model {MODEL} pulled", any(MODEL in m for m in models),
          f"available: {models or 'none'}")
except Exception as e:
    check("Ollama server", False, str(e))
    check(f"Model {MODEL} pulled", False, "server unreachable")

# 3. Quick inference test
print("\n  Running inference smoke-test (may take ~10 s)...")
try:
    payload = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content":
            "Reply with exactly: int64_t add(int64_t a, int64_t b) { return a + b; }"}],
        "stream": False,
        "options": {"num_predict": 64, "temperature": 0},
    }).encode()
    req = urllib.request.Request(f"{OLLAMA_HOST}/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read())
    text = resp["message"]["content"].strip()
    check("Inference smoke-test", "int64_t" in text, text[:80])
except Exception as e:
    check("Inference smoke-test", False, str(e))

# 4. Swarm directory structure
for d in ["antigravity", "agents", "mcp_clients", "prompts"]:
    check(f"swarm/{d}/ exists", (ROOT / d).is_dir())
check("swarm/config.yaml", (ROOT / "config.yaml").exists())
check("swarm/prompts/system_base.md", (ROOT / "prompts/system_base.md").exists())

# 5. Python packages
for pkg in ["yaml", "anthropic"]:
    try:
        __import__(pkg)
        check(f"Python pkg: {pkg}", True)
    except ImportError:
        check(f"Python pkg: {pkg}", False, f"pip install {pkg}")

# 6. nvidia-smi
r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.free",
                    "--format=csv,noheader"], capture_output=True, text=True)
if r.returncode == 0:
    check("NVIDIA GPU", True, r.stdout.strip())
else:
    check("NVIDIA GPU", False, "nvidia-smi failed")

print()
if ok:
    print("All checks passed. The local model is ready for the swarm.")
else:
    print("Some checks failed — see FAIL lines above.")
    sys.exit(1)

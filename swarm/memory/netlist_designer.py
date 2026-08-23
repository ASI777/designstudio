"""Compact-KG netlist designer.

Replaces the heavy `connection_agent` path for the harness: instead of sending
full component/2 datasheets (16k+ tokens each) to the LLM as a command-line
argument — which overflows ARG_MAX on a real board — it sends the KG's compact
~300-token-per-part representation (PCBSchemaGen-style compression).

Two benefits:
  • The prompt is small (a 15-part board ≈ a few thousand tokens), so it fits in
    argv and the LLM stays focused on connectivity, not datasheet prose.
  • It uses Layer A (the Component KG) directly, closing the loop on the memory
    architecture.

The LLM call is injectable (`llm_call`) so this is unit-testable without a model;
the default tries the Anthropic API, then `agy`.
"""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .component_kg import ComponentKG


_PROMPT = """You are a PCB netlist designer. Wire the components below into a netlist.

Each component is given as  REF (MPN, category)  with pins listed as
number:name:role.  Roles: power.rail (supply in), power.gnd (ground),
power.out (regulator output), signal.* (functional signals).

RULES:
- Every power.rail pin connects to a named power net (e.g. VCC_3V3, VM, IN_12V).
- Every power.gnd pin connects to GND.
- Never put a power pin and a ground pin on the same net.
- Wire matching interfaces between components (I2C SDA↔SDA, SPI, USB DP/DM…).
- Instantiate each listed required-external as a passive (ref C*/R*) wired across
  the stated nodes.
- Use only pins that exist on the component.

Output ONE JSON object, no prose, no fences:
{{"nets": {{"<net_name>": [{{"ref":"U1","pin":"VM"}}, ...]}}}}

COMPONENTS:
{components}

DESIGN GOAL: {goal}
{feedback}"""


def build_prompt(components: list[dict], kg: ComponentKG,
                 goal: str, feedback: str = "") -> str:
    blocks = []
    for c in components:
        node = kg.get(c.get("mpn", ""))
        ref = c.get("ref", "U?")
        if node is None:
            blocks.append(f"{ref} ({c.get('mpn','?')}, unknown): pins unknown")
            continue
        cp = node.compact()
        line = [f"{ref} ({cp['mpn']}, {cp['category']}):",
                "  pins: " + ", ".join(cp["pins"])]
        if cp["rails"]:
            line.append("  rails: " + ", ".join(cp["rails"]))
        if cp["required_externals"]:
            line.append("  required: " + "; ".join(cp["required_externals"]))
        if cp["interfaces"]:
            line.append("  interfaces: " + ", ".join(map(str, cp["interfaces"])))
        blocks.append("\n".join(line))
    fb = f"\nFIX THESE ISSUES FROM THE LAST ATTEMPT:\n{feedback}" if feedback else ""
    return _PROMPT.format(components="\n".join(blocks), goal=goal, feedback=fb)


# ── default LLM invocation (Anthropic API → agy) ──────────────────────────────
def _default_llm(prompt: str, timeout: int = 300) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        try:
            import anthropic
            cl = anthropic.Anthropic(api_key=key)
            msg = cl.messages.create(model="claude-sonnet-4-6", max_tokens=4096,
                                     messages=[{"role": "user", "content": prompt}])
            return msg.content[0].text
        except Exception:
            pass
    # agy fallback — prompt is now small, so argv is safe.
    agy = str(Path.home() / ".local/bin/agy")
    args = [agy, "--model", "Claude Sonnet 4.6 (Thinking)", "-p", prompt,
            "--print-timeout", f"{timeout}s"]
    inner = " ".join("'" + a.replace("'", "'\\''") + "'" for a in args)
    env = os.environ.copy()
    env["PATH"] = str(Path.home() / ".local/bin") + ":" + env.get("PATH", "")
    out = subprocess.run(["script", "-q", "-c", inner, "/dev/null"],
                         capture_output=True, text=True, timeout=timeout + 15, env=env)
    text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]|\r", "", out.stdout)
    return text


def _parse(text: str) -> list[dict]:
    m = re.search(r"\{[\s\S]+\}", text)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except Exception:
        return []
    out = []
    for net_name, conns in (data.get("nets") or {}).items():
        for c in conns:
            ref = c.get("ref"); pin = c.get("pin")
            if ref and pin is not None:
                out.append({"ref": ref, "pin": str(pin), "net": net_name})
    return out


def design_netlist(components: list[dict], kg: ComponentKG, goal: str,
                   feedback: str = "",
                   llm_call: Callable[[str], str] | None = None) -> list[dict]:
    """Return [{ref,pin,net}] for the given components. llm_call is injectable."""
    if not components:
        return []
    prompt = build_prompt(components, kg, goal, feedback)
    call = llm_call or _default_llm
    return _parse(call(prompt))


# ── self-test (no LLM — proves prompt size + parsing) ──────────────────────────
if __name__ == "__main__":
    kg = ComponentKG().load()
    if not kg.components:
        from swarm.runtime_paths import component_fixture_dir
        kg.ingest_dir(str(component_fixture_dir())); kg.save()
    comps = [{"ref": "U1", "mpn": next(c.mpn for c in kg.components.values()
                                       if "DRV8353" in c.mpn)}]
    prompt = build_prompt(comps, kg, "BLDC motor driver, 24V")
    print(f"compact prompt size: {len(prompt)} chars "
          f"(~{len(prompt)//4} tokens) for {len(comps)} component(s)")
    print("--- prompt head ---")
    print(prompt[:600])

    # Stub LLM returns a small valid netlist → prove parsing.
    def stub(_p):
        return '{"nets": {"VM": [{"ref":"U1","pin":"VM"}], ' \
               '"GND": [{"ref":"U1","pin":"GND"}]}}'
    nl = design_netlist(comps, kg, "test", llm_call=stub)
    print("\nparsed netlist:", nl)
    assert {"ref": "U1", "pin": "VM", "net": "VM"} in nl
    print("netlist_designer OK — compact prompt, parsed netlist")

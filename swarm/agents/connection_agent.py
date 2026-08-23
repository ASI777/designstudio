"""Connection agent: list of component/2 JSONs → netlist + gap detection.

Two sub-tasks:
  design(components, intent)  → netlist (nets + connections)
  detect_gaps(netlist, components) → list of missing component specs

Model split:
  connection design  → Claude Sonnet (direct API) — best circuit reasoning
  gap detection      → GPT-OSS 120B (agy)         — independent second opinion
  verification       → Claude Opus (agy)           — final check if gaps found
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_site = Path.home() / ".local/lib/python3.14/site-packages"
if str(_site) not in sys.path:
    sys.path.insert(0, str(_site))

AGY = str(Path.home() / ".local/bin/agy")

_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]|\r')
_SPINNER  = re.compile(r'^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏\[K]')

_ADVISOR_PROMPT_PATH = (
    Path(__file__).parents[2] / "docs" / "circuit-advisor" / "ADVISOR_PROMPT.md"
)


@dataclass
class NetConnection:
    from_ref: str    # "U1"
    from_pin: str    # "VCC"
    to_ref: str      # "C1"
    to_pin: str      # "1"
    net_name: str    # "VCC_3V3"


@dataclass
class Netlist:
    nets: dict[str, list[NetConnection]]  # net_name → connections
    unconnected: list[str]                # "U1.GPIO0 unconnected"
    warnings: list[str]


@dataclass
class MissingComponent:
    role: str
    reason: str
    search_query: str
    urgency: str   # "critical" | "recommended" | "optional"
    connects_between: list[str]  # ["U1.VIN", "GND"]


def _strip_ansi(text: str) -> str:
    lines = _ANSI_RE.sub('', text).splitlines()
    return '\n'.join(l for l in lines if not _SPINNER.match(l)).strip()


def _agy_env() -> dict:
    env = os.environ.copy()
    local_bin = str(Path.home() / ".local/bin")
    if local_bin not in env.get("PATH", ""):
        env["PATH"] = local_bin + ":" + env.get("PATH", "/usr/bin:/bin")
    return env


def _call_agy(model_id: str, prompt: str, timeout: int = 180) -> str:
    agy_args = [AGY, "--model", model_id, "-p", prompt, "--print-timeout", f"{timeout}s"]
    inner = " ".join("'" + a.replace("'", "'\\''") + "'" for a in agy_args)
    result = subprocess.run(
        ["script", "-q", "-c", inner, "/dev/null"],
        capture_output=True, text=True, timeout=timeout + 15, env=_agy_env(),
    )
    return _strip_ansi(result.stdout)


def _call_claude_direct(prompt: str, timeout: int = 300) -> str:
    """Call Claude Sonnet via direct Anthropic API; falls back to agy."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text
        except Exception:
            pass
    # No API key or SDK call failed — use agy
    return _call_agy("Claude Sonnet 4.6 (Thinking)", prompt, timeout)


_CONNECTION_PROMPT = """You are a senior PCB design engineer designing a 4-layer PCB netlist.

Output ONE JSON object:
{
  "nets": {
    "<net_name>": [
      {"from_ref": "U1", "from_pin": "VCC", "to_ref": "C1", "to_pin": "1", "net_name": "<net_name>"}
    ]
  },
  "unconnected": ["list of pins intentionally left unconnected or NC"],
  "warnings": ["any electrical concerns or missing connections"]
}

STRICT RULES — violations will cause hardware failure:
1. EVERY component in the provided list MUST appear in the netlist. Do not omit any.
2. EVERY power_in pin must connect to a named power net (VCC_3V3, VCC_5V, VBAT, GND, etc.).
3. NO GPIO pin may appear in more than ONE net. I2C, I2S, SPI, UART each get exclusive pins.
4. Wire the complete power path: VBUS(5V) → regulator/charger → VCC_3V3 rail.
5. Every IC that lists required_externals must have those passives instantiated and wired.
6. Add USB ESD protection (ref: D1) between USB connector DP/DM pins and MCU USB pins.
7. Use ref designators: U=IC, C=capacitor, R=resistor, D=diode/ESD, J=connector, Y=crystal, SW=switch.
8. Name power nets by voltage: VCC_3V3, VCC_5V, VBAT, GND. Audio: AGND for analog ground.
9. I2C pull-ups (4.7kΩ) connect: one end to VCC_3V3, other end to the SDA/SCL net.
10. For RP2040: I2C uses GP4(SDA)/GP5(SCL). I2S uses GP26(DIN)/GP27(BCK)/GP28(LRCK). USB uses USB_DP/USB_DM. SPI flash uses GP10-GP13. SWDIO=GP-SWDIO, SWDCLK=SWDCLK.

DESIGN GOAL: {design_goal}

COMPONENTS (wire ALL of these — use exact MPNs as identifiers):
{components_json}"""

_GAP_PROMPT = """You are an independent electronics design reviewer. A netlist and component list have been generated.
Find MISSING components that are electrically required but absent.

Output ONE JSON object:
{
  "missing": [
    {
      "role": "string (e.g. 'decoupling_cap', 'crystal_oscillator', 'usb_esd_protection', 'power_reg')",
      "reason": "string (why it is needed — cite the specific pin/net that requires it)",
      "search_query": "string (DigiKey/Mouser search keyword)",
      "urgency": "critical | recommended | optional",
      "connects_between": ["ref.pin", "ref.pin"]
    }
  ],
  "verdict": "ok | needs_iteration"
}

Check for:
- Missing decoupling caps on IC power pins (required by nearly every IC)
- Missing crystal/oscillator for MCUs that need external clock
- USB ESD protection on data lines
- Pull-up resistors on I2C SDA/SCL
- Boot/mode resistors for microcontrollers
- Reset circuits
- UART/SWD/JTAG debug connectors
- Antenna components for wireless
- Current limiting resistors for LEDs

NETLIST:
{netlist_json}

COMPONENTS:
{components_summary}"""


def design(components: list[dict], design_goal: str) -> Netlist:
    """Generate netlist from component/2 JSONs."""
    components_json = json.dumps(components, indent=2)
    prompt = _CONNECTION_PROMPT.replace("{design_goal}", design_goal).replace(
        "{components_json}", components_json
    )

    response = _call_claude_direct(prompt, timeout=120)
    m = re.search(r'\{[\s\S]+\}', response)
    if not m:
        return Netlist(nets={}, unconnected=[], warnings=["Connection agent returned no JSON"])

    data = json.loads(m.group(0))
    nets: dict[str, list[NetConnection]] = {}
    for net_name, conns in (data.get("nets") or {}).items():
        nets[net_name] = [NetConnection(**c) for c in conns]

    return Netlist(
        nets=nets,
        unconnected=data.get("unconnected", []),
        warnings=data.get("warnings", []),
    )


def detect_gaps(netlist: Netlist, components: list[dict]) -> list[MissingComponent]:
    """Find missing components using GPT-OSS 120B for independent review."""
    netlist_json = json.dumps({
        "nets": {
            name: [{"from_ref": c.from_ref, "from_pin": c.from_pin,
                    "to_ref": c.to_ref, "to_pin": c.to_pin}
                   for c in conns]
            for name, conns in netlist.nets.items()
        },
        "warnings": netlist.warnings,
    }, indent=2)

    # Summarize components (role + MPN + power domains) to stay within context
    summary = [
        {
            "mpn": c.get("component", {}).get("mpn", "?"),
            "description": c.get("component", {}).get("description", ""),
            "required_externals": (c.get("electrical") or {}).get("required_externals", []),
            "power_domains": (c.get("electrical") or {}).get("power_domains", []),
        }
        for c in components
    ]

    prompt = (
        _GAP_PROMPT
        .replace("{netlist_json}", netlist_json)
        .replace("{components_summary}", json.dumps(summary, indent=2))
    )

    response = _call_agy("GPT-OSS 120B (Medium)", prompt, timeout=180)
    m = re.search(r'\{[\s\S]+\}', response)
    if not m:
        return []

    data = json.loads(m.group(0))
    if data.get("verdict") == "ok":
        return []

    return [
        MissingComponent(
            role=g["role"],
            reason=g["reason"],
            search_query=g["search_query"],
            urgency=g.get("urgency", "recommended"),
            connects_between=g.get("connects_between", []),
        )
        for g in data.get("missing", [])
    ]

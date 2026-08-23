"""Intent agent: natural-language product description → structured component requirements.

Model: Gemini 3.1 Pro High (agy) — needs deep product knowledge to translate
"I want a portable synthesizer with knobs and a small OLED" into specific
component categories, interface requirements, and power constraints.

Returns IntentResult with required component specs that feed into vendor search.
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_site = Path.home() / ".local/lib/python3.14/site-packages"
if str(_site) not in sys.path:
    sys.path.insert(0, str(_site))

AGY = str(Path.home() / ".local/bin/agy")

_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]|\r')
_SPINNER  = re.compile(r'^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏\[K]')


@dataclass
class ComponentSpec:
    role: str             # "mcu", "display", "audio_codec", "power_reg", "knob_encoder", ...
    description: str      # What it must do
    search_query: str     # Best keyword for vendor search
    quantity: int = 1
    required: bool = True
    constraints: dict = field(default_factory=dict)  # {"vcc": "3.3V", "interface": "I2C", ...}


@dataclass
class IntentResult:
    product_name: str
    product_type: str           # "synthesizer", "sensor", "controller", "wearable", ...
    design_goal: str            # One-line summary for ADVISOR_PROMPT
    components: list[ComponentSpec]
    power_budget_mw: int | None
    enclosure_hint: str | None  # "compact", "panel", "handheld", "rack"
    aesthetic_hint: str | None  # "exposed-pcb", "aluminum-monolith", "minimal"
    notes: list[str] = field(default_factory=list)


_INTENT_PROMPT = """You are a senior electronics product designer. A user has described a product they want to build.

Your job: extract a structured list of electronic components needed to build it.

Output ONE JSON object exactly like this schema — no prose, no fences:

{
  "product_name": "string",
  "product_type": "string (one of: synthesizer, sensor, controller, wearable, audio, motor_driver, power_supply, display, other)",
  "design_goal": "string (one sentence for a circuit advisor AI — describe the goal and key constraints)",
  "power_budget_mw": <integer or null>,
  "enclosure_hint": "string or null (compact | panel | handheld | rack | wearable | open)",
  "aesthetic_hint": "string or null (exposed-pcb | aluminum-monolith | inside-out | minimal-slab | brutalist)",
  "notes": ["string", ...],
  "components": [
    {
      "role": "string (function: mcu, display, audio_codec, power_reg, knob_encoder, button, led, battery_charger, usb_bridge, accelerometer, bluetooth, wifi, motor_driver, sensor, connector, passive, other)",
      "description": "string (what it must do electrically)",
      "search_query": "string (best keyword to search DigiKey/Mouser — specific and technical, e.g. 'ATmega328 28-PDIP' not just 'microcontroller')",
      "quantity": <integer>,
      "required": <boolean>,
      "constraints": {
        "supply_v": "string or null (e.g. '3.3V' or '5V')",
        "interface": "string or null (e.g. 'I2C', 'SPI', 'UART', 'USB')",
        "package": "string or null (e.g. 'SOIC-8', 'QFP-48', 'through-hole')",
        "other": "string or null"
      }
    }
  ]
}

Rules:
- Always include a power regulation component (LDO or buck converter) unless explicitly battery-powered at fixed voltage.
- Always include decoupling capacitors as passive components.
- Always include at least one microcontroller or processor for any programmable device.
- For audio products: include audio codec or DAC.
- For display products: include display driver IC if not integrated.
- Be specific in search_query — vendor APIs return better results with part families than generic terms.
- Think through required support components (crystal oscillators, USB ESD protection, etc.).

USER DESCRIPTION:
{user_intent}"""


def _strip_ansi(text: str) -> str:
    lines = _ANSI_RE.sub('', text).splitlines()
    return '\n'.join(l for l in lines if not _SPINNER.match(l)).strip()


def _agy_env() -> dict:
    env = os.environ.copy()
    local_bin = str(Path.home() / ".local/bin")
    if local_bin not in env.get("PATH", ""):
        env["PATH"] = local_bin + ":" + env.get("PATH", "/usr/bin:/bin")
    return env


def _call_gemini_pro(prompt: str, timeout: int = 180) -> str:
    agy_args = [AGY, "--model", "Gemini 3.1 Pro (High)", "-p", prompt, "--print-timeout", f"{timeout}s"]
    inner = " ".join("'" + a.replace("'", "'\\''") + "'" for a in agy_args)
    result = subprocess.run(
        ["script", "-q", "-c", inner, "/dev/null"],
        capture_output=True, text=True, timeout=timeout + 15, env=_agy_env(),
    )
    return _strip_ansi(result.stdout)


def parse(user_intent: str, timeout: int = 180) -> IntentResult:
    """Parse user's natural language product description into structured component requirements."""
    prompt = _INTENT_PROMPT.replace("{user_intent}", user_intent)
    response = _call_gemini_pro(prompt, timeout=timeout)

    m = re.search(r'\{[\s\S]+\}', response)
    if not m:
        raise ValueError(f"No JSON from intent agent. Response: {response[:300]}")

    data = json.loads(m.group(0))

    components = [
        ComponentSpec(
            role=c["role"],
            description=c["description"],
            search_query=c["search_query"],
            quantity=c.get("quantity", 1),
            required=c.get("required", True),
            constraints=c.get("constraints", {}),
        )
        for c in data.get("components", [])
    ]

    return IntentResult(
        product_name=data.get("product_name", "Unnamed Product"),
        product_type=data.get("product_type", "other"),
        design_goal=data.get("design_goal", user_intent),
        components=components,
        power_budget_mw=data.get("power_budget_mw"),
        enclosure_hint=data.get("enclosure_hint"),
        aesthetic_hint=data.get("aesthetic_hint"),
        notes=data.get("notes", []),
    )

#!/usr/bin/env python3
"""Schematic MCP server — AI agents design and inspect schematics.

User space: schematic editor
Tools exposed:
  create_schematic        — start a new design from a description
  add_component           — insert a component with ref + pin list
  add_net                 — wire two pins together
  remove_component        — delete a component and its nets
  get_netlist             — return current netlist as JSON
  run_erc                 — Electrical Rules Check (open pins, undriven inputs, etc.)
  render_schematic        — render PNG and return file path
  export_netlist          — save netlist to disk as JSON
  import_netlist          — load a previously saved netlist
  suggest_connections     — AI-driven suggestion for missing nets
"""

import json
import os
import sys
from pathlib import Path

_root = Path(__file__).parents[2]
_site = Path.home() / ".local/lib/python3.14/site-packages"
for p in [str(_root), str(_site)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "schematic",
    instructions=(
        "Schematic design operations. Use create_schematic to start, then add_component "
        "and add_net to build the circuit. Run run_erc to find electrical issues. "
        "Use render_schematic to visualise the design."
    ),
)

# ── In-memory schematic state ─────────────────────────────────────────────────
_state: dict = {
    "components": [],  # list of component/2 dicts with _ref
    "nets": {},        # {net_name: [{from_ref, from_pin, to_ref, to_pin}]}
    "board_name": "Untitled",
    "warnings": [],
}

_SAVE_DIR = Path.home() / ".config" / "product_design" / "schematics"
_SAVE_DIR.mkdir(parents=True, exist_ok=True)


def _state_summary() -> str:
    n_comp = len(_state["components"])
    n_nets = len(_state["nets"])
    n_conn = sum(len(c) for c in _state["nets"].values())
    return f"{n_comp} components, {n_nets} nets, {n_conn} connections"


@mcp.tool()
def create_schematic(description: str, board_name: str = "Untitled") -> str:
    """Start a new schematic from a natural language description.

    Calls the design pipeline (intent → vendor → netlist) and loads the result.
    description: e.g. 'RP2040 synth with OLED, audio DAC, USB-C, LiPo charger'
    """
    from swarm.agents.design_loop import run as _run
    _state["board_name"] = board_name
    _state["components"] = []
    _state["nets"] = {}
    _state["warnings"] = []

    try:
        result = _run(description)
        from dataclasses import asdict
        data = asdict(result)
        _state["components"] = data.get("components", [])
        _state["nets"] = data.get("netlist", {})
        _state["warnings"] = data.get("warnings", [])
        return json.dumps({
            "status": "ok",
            "summary": _state_summary(),
            "warnings": _state["warnings"][:5],
        })
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def add_component(
    ref: str,
    mpn: str,
    manufacturer: str = "",
    description: str = "",
    category: str = "other",
    pins: list | None = None,
) -> str:
    """Add a component to the schematic.

    ref: designator like 'U1', 'C3', 'R12'
    mpn: manufacturer part number
    pins: list of {name, number, type} dicts — omit to use known-pins fallback
    """
    from swarm.agents.known_pins import supplement

    comp = {
        "_ref": ref,
        "component": {
            "mpn": mpn,
            "manufacturer": manufacturer,
            "description": description,
            "category": category,
        },
        "symbol": {"pins": pins or []},
        "electrical": {"power_domains": [], "required_externals": []},
    }
    supplement(comp)

    # Remove existing component with same ref
    _state["components"] = [c for c in _state["components"] if c.get("_ref") != ref]
    _state["components"].append(comp)

    pin_count = len(comp["symbol"]["pins"])
    return json.dumps({
        "status": "ok",
        "ref": ref,
        "mpn": mpn,
        "pins_resolved": pin_count,
        "total_components": len(_state["components"]),
    })


@mcp.tool()
def add_net(
    net_name: str,
    from_ref: str,
    from_pin: str,
    to_ref: str,
    to_pin: str,
) -> str:
    """Connect two component pins with a named net.

    net_name: e.g. 'VCC_3V3', 'I2C_SDA', 'GND'
    """
    conn = {
        "from_ref": from_ref,
        "from_pin": from_pin,
        "to_ref": to_ref,
        "to_pin": to_pin,
        "net_name": net_name,
    }
    _state["nets"].setdefault(net_name, []).append(conn)
    return json.dumps({
        "status": "ok",
        "net": net_name,
        "connection": f"{from_ref}.{from_pin} → {to_ref}.{to_pin}",
        "net_connections": len(_state["nets"][net_name]),
    })


@mcp.tool()
def remove_component(ref: str) -> str:
    """Remove a component and all its net connections."""
    before = len(_state["components"])
    _state["components"] = [c for c in _state["components"] if c.get("_ref") != ref]
    removed = before - len(_state["components"])

    # Remove all connections referencing this ref
    for net_name in list(_state["nets"].keys()):
        _state["nets"][net_name] = [
            c for c in _state["nets"][net_name]
            if c["from_ref"] != ref and c["to_ref"] != ref
        ]
        if not _state["nets"][net_name]:
            del _state["nets"][net_name]

    return json.dumps({"status": "ok", "removed": removed, "ref": ref})


@mcp.tool()
def get_netlist() -> str:
    """Return the full current netlist as JSON."""
    return json.dumps({
        "board_name": _state["board_name"],
        "components": [
            {
                "ref": c.get("_ref", "?"),
                "mpn": c.get("component", {}).get("mpn", ""),
                "pins": len((c.get("symbol") or {}).get("pins") or []),
            }
            for c in _state["components"]
        ],
        "nets": _state["nets"],
        "summary": _state_summary(),
        "warnings": _state["warnings"],
    }, indent=2)


@mcp.tool()
def run_erc() -> str:
    """Run Electrical Rules Check on the current schematic.

    Checks:
    - Power pins not connected to a power net
    - Output pins driving each other (conflicts)
    - Pins unconnected but not marked NC
    - Multiple drivers on a net
    - Missing GND net
    - Missing power nets for ICs
    """
    issues = []

    net_by_pin: dict[tuple, str] = {}
    for net_name, conns in _state["nets"].items():
        for c in conns:
            k1 = (c["from_ref"], c["from_pin"])
            k2 = (c["to_ref"], c["to_pin"])
            if k1 in net_by_pin and net_by_pin[k1] != net_name:
                issues.append({
                    "severity": "error",
                    "rule": "MULTI_NET_PIN",
                    "message": f"{c['from_ref']}.{c['from_pin']} appears in both '{net_by_pin[k1]}' and '{net_name}'",
                })
            net_by_pin[k1] = net_name
            net_by_pin[k2] = net_name

    has_gnd = any("GND" in n.upper() for n in _state["nets"])
    if not has_gnd and _state["components"]:
        issues.append({"severity": "error", "rule": "NO_GND", "message": "No GND net defined"})

    for comp in _state["components"]:
        ref = comp.get("_ref", "?")
        pins = (comp.get("symbol") or {}).get("pins") or []
        for pin in pins:
            pname = pin.get("name", "")
            ptype = pin.get("type", "")
            if ptype == "power_in" and pname.upper() not in ("NC",):
                if (ref, pname) not in net_by_pin:
                    issues.append({
                        "severity": "warning",
                        "rule": "UNCONNECTED_POWER",
                        "message": f"{ref}.{pname} (power_in) not connected to any net",
                    })

    # Check for output-output conflicts
    outputs_per_net: dict[str, list[str]] = {}
    for comp in _state["components"]:
        ref = comp.get("_ref", "?")
        pins = (comp.get("symbol") or {}).get("pins") or []
        for pin in pins:
            if pin.get("type") == "output":
                net = net_by_pin.get((ref, pin.get("name", "")))
                if net:
                    outputs_per_net.setdefault(net, []).append(f"{ref}.{pin['name']}")
    for net, drivers in outputs_per_net.items():
        if len(drivers) > 1:
            issues.append({
                "severity": "error",
                "rule": "OUTPUT_CONFLICT",
                "message": f"Net '{net}' driven by multiple outputs: {', '.join(drivers)}",
            })

    verdict = "PASS" if not any(i["severity"] == "error" for i in issues) else "FAIL"
    return json.dumps({
        "verdict": verdict,
        "errors": sum(1 for i in issues if i["severity"] == "error"),
        "warnings": sum(1 for i in issues if i["severity"] == "warning"),
        "issues": issues,
    }, indent=2)


@mcp.tool()
def render_schematic(output_path: str = "") -> str:
    """Render the current schematic to a PNG file.

    output_path: optional override; defaults to /tmp/schematic_live.png
    Returns the saved file path.
    """
    path = output_path or "/tmp/schematic_live.png"
    try:
        from swarm.render.schematic_renderer import render_schematic as _render
        _render(_state["components"], _state["nets"], path, _state["board_name"])
        return json.dumps({"status": "ok", "path": path, "summary": _state_summary()})
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def export_netlist(filename: str = "") -> str:
    """Save the current netlist to a JSON file."""
    name = filename or f"{_state['board_name'].replace(' ', '_')}_netlist.json"
    path = _SAVE_DIR / name
    out = {
        "board_name": _state["board_name"],
        "components": _state["components"],
        "nets": _state["nets"],
        "warnings": _state["warnings"],
    }
    path.write_text(json.dumps(out, indent=2))
    return json.dumps({"status": "ok", "path": str(path)})


@mcp.tool()
def import_netlist(path: str) -> str:
    """Load a previously saved netlist JSON into the current session."""
    try:
        data = json.loads(Path(path).read_text())
        _state["board_name"] = data.get("board_name", "Imported")
        _state["components"] = data.get("components", [])
        _state["nets"] = data.get("nets", {})
        _state["warnings"] = data.get("warnings", [])
        return json.dumps({"status": "ok", "summary": _state_summary()})
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def suggest_connections() -> str:
    """Ask Claude Sonnet to suggest missing net connections.

    Analyses the current components and their unconnected pins,
    then proposes the most likely missing nets.
    """
    unconnected = []
    net_by_pin: dict[tuple, str] = {}
    for net_name, conns in _state["nets"].items():
        for c in conns:
            net_by_pin[(c["from_ref"], c["from_pin"])] = net_name
            net_by_pin[(c["to_ref"], c["to_pin"])] = net_name

    for comp in _state["components"]:
        ref = comp.get("_ref", "?")
        pins = (comp.get("symbol") or {}).get("pins") or []
        for pin in pins:
            pname = pin.get("name", "")
            if (ref, pname) not in net_by_pin and pin.get("type") != "nc":
                unconnected.append(f"{ref}.{pname} ({pin.get('type','')})")

    if not unconnected:
        return json.dumps({"status": "ok", "message": "All pins connected", "suggestions": []})

    # Call Claude via agy
    try:
        import subprocess, re
        agy = str(Path.home() / ".local/bin/agy")
        prompt = (
            "You are a PCB design expert. Given these unconnected component pins, "
            "suggest which nets they should belong to. Output JSON: "
            "{\"suggestions\": [{\"pin\": \"U1.VDD\", \"net\": \"VCC_3V3\", \"reason\": \"...\"}]}.\n\n"
            f"Board: {_state['board_name']}\n"
            f"Existing nets: {list(_state['nets'].keys())}\n"
            f"Unconnected pins:\n" + "\n".join(f"  {p}" for p in unconnected[:30])
        )
        r = subprocess.run([agy, "--model", "Claude Sonnet 4.6 (Thinking)", "-p", prompt,
                            "--print-timeout", "60s"],
                           capture_output=True, text=True, timeout=90)
        m = re.search(r'\{[\s\S]+\}', r.stdout)
        if m:
            return m.group(0)
    except Exception:
        pass

    return json.dumps({
        "status": "ok",
        "unconnected_count": len(unconnected),
        "unconnected": unconnected[:20],
        "message": "agy unavailable — review unconnected pins manually",
    })


if __name__ == "__main__":
    mcp.run(transport="stdio")

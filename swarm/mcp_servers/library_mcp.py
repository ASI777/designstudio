#!/usr/bin/env python3
"""Component library MCP server — AI agents search, add, and manage components.

User space: component library
Tools:
  search_component        — search DigiKey by keyword
  get_component_pins      — retrieve pin list from known-pins DB or datasheet
  add_to_library          — save a component JSON to the local library
  list_library            — list all components in the local library
  get_component           — fetch a specific library entry by MPN
  delete_component        — remove a component from the library
  check_availability      — real-time stock + price check
  find_alternatives       — find pin-compatible alternatives for an MPN
  validate_component_json — validate a component/2 JSON against the schema
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
from swarm.runtime_paths import component_fixture_dir, component_library_dir

mcp = FastMCP(
    "component-library",
    instructions=(
        "Component library management. Use search_component to find parts on DigiKey. "
        "Use get_component_pins to resolve pin lists. "
        "Use add_to_library to save components for reuse. "
        "Use find_alternatives to locate drop-in replacements."
    ),
)

_LIB_DIR = component_library_dir()
_LIB_DIR.mkdir(parents=True, exist_ok=True)


@mcp.tool()
def search_component(query: str, limit: int = 5) -> str:
    """Search DigiKey production API for components.

    query: keyword, e.g. 'RP2040', 'USBLC6', '100nF 0402 ceramic'
    limit: max results to return (1–20)
    Returns list of {mpn, manufacturer, description, stock, price_inr, datasheet_url}.
    """
    try:
        from swarm.vendors.aggregator import Aggregator
        from swarm.vendors.base import PartResult
        agg = Aggregator()
        results: list[PartResult] = agg.search(query, limit=min(limit, 20))
        out = []
        for r in results:
            if not r.mpn:
                continue
            out.append({
                "mpn": r.mpn,
                "manufacturer": r.manufacturer,
                "description": r.description,
                "stock": r.stock,
                "price_inr": r.price_inr,
                "vendor": r.vendor,
                "datasheet_url": r.datasheet_url,
            })
        return json.dumps({
            "query": query,
            "results_found": len(out),
            "results": out,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def get_component_pins(mpn: str, datasheet_url: str = "") -> str:
    """Get pin list for a component from known-pins DB or datasheet extraction.

    Tries known_pins first (instant). Falls back to datasheet PDF extraction if URL given.
    """
    from swarm.agents.known_pins import lookup

    known = lookup(mpn)
    if known:
        return json.dumps({
            "source": "known_pins_db",
            "mpn": mpn,
            "pin_count": len(known),
            "pins": known,
        }, indent=2)

    if datasheet_url:
        try:
            from swarm.agents.datasheet_agent import process
            comp_json = process(datasheet_url, mpn=mpn, timeout=120)
            pins = (comp_json.get("symbol") or {}).get("pins") or []
            return json.dumps({
                "source": "datasheet_extraction",
                "mpn": mpn,
                "pin_count": len(pins),
                "pins": pins,
                "component": comp_json.get("component", {}),
            }, indent=2)
        except Exception as e:
            return json.dumps({"status": "error", "mpn": mpn, "error": str(e),
                               "note": "Datasheet extraction failed — add to known_pins.py manually"})

    return json.dumps({
        "status": "not_found",
        "mpn": mpn,
        "message": "Not in known-pins DB. Provide datasheet_url to extract.",
        "hint": "Add to swarm/agents/known_pins.py for instant lookup next time.",
    })


@mcp.tool()
def add_to_library(
    mpn: str,
    manufacturer: str,
    description: str,
    category: str,
    pins: list,
    datasheet_url: str = "",
    price_inr: float | None = None,
    stock: int | None = None,
) -> str:
    """Save a component to the local library as a component/2 JSON file."""
    comp = {
        "component": {
            "mpn": mpn,
            "manufacturer": manufacturer,
            "description": description,
            "category": category,
            "datasheet_url": datasheet_url,
        },
        "symbol": {"pins": pins},
        "electrical": {"power_domains": [], "required_externals": []},
        "_vendor": {"price_inr": price_inr, "stock": stock},
    }

    safe_name = mpn.replace("/", "_").replace(" ", "_").replace(".", "_")
    path = _LIB_DIR / f"{safe_name}.json"
    path.write_text(json.dumps(comp, indent=2))

    return json.dumps({
        "status": "ok",
        "mpn": mpn,
        "path": str(path),
        "pins": len(pins),
    })


@mcp.tool()
def list_library(category: str = "") -> str:
    """List all components in the local library.

    category: filter by category (mcu, passive, audio, etc.) — empty = list all
    """
    entries = []
    for f in sorted(_LIB_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            c = data.get("component", {})
            cat = c.get("category", "")
            if category and cat != category:
                continue
            entries.append({
                "mpn": c.get("mpn", f.stem),
                "manufacturer": c.get("manufacturer", ""),
                "description": c.get("description", "")[:60],
                "category": cat,
                "pins": len((data.get("symbol") or {}).get("pins") or []),
            })
        except Exception:
            continue

    return json.dumps({
        "library_path": str(_LIB_DIR),
        "total": len(entries),
        "filter": category or "all",
        "components": entries,
    }, indent=2)


@mcp.tool()
def get_component(mpn: str) -> str:
    """Fetch the full component/2 JSON for a specific MPN from the library."""
    safe_name = mpn.replace("/", "_").replace(" ", "_").replace(".", "_")
    candidates = [
        _LIB_DIR / f"{safe_name}.json",
        # Immutable samples are a read-only fallback, never a write target.
        component_fixture_dir() / f"{mpn}.json",
        component_fixture_dir() / f"{safe_name}.json",
    ]
    for path in candidates:
        if path.exists():
            return path.read_text()

    return json.dumps({"status": "not_found", "mpn": mpn,
                       "hint": "Use add_to_library or search_component first"})


@mcp.tool()
def delete_component(mpn: str) -> str:
    """Remove a component from the local library."""
    safe_name = mpn.replace("/", "_").replace(" ", "_").replace(".", "_")
    path = _LIB_DIR / f"{safe_name}.json"
    if path.exists():
        path.unlink()
        return json.dumps({"status": "ok", "deleted": mpn})
    return json.dumps({"status": "not_found", "mpn": mpn})


@mcp.tool()
def check_availability(mpn: str) -> str:
    """Real-time stock and price check on DigiKey for a specific MPN."""
    try:
        from swarm.vendors.aggregator import Aggregator
        agg = Aggregator()
        results = agg.search(mpn, limit=5)
        best = agg.best(results)
        if best:
            return json.dumps({
                "mpn": best.mpn,
                "manufacturer": best.manufacturer,
                "stock": best.stock,
                "price_inr": best.price_inr,
                "vendor": best.vendor,
                "datasheet_url": best.datasheet_url,
                "lifecycle": best.lifecycle,
            })
        return json.dumps({"status": "not_found", "mpn": mpn})
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def find_alternatives(mpn: str, max_results: int = 5) -> str:
    """Find pin-compatible alternatives for an MPN.

    Searches for parts with similar description and same category,
    prioritising in-stock, low-cost options.
    """
    from swarm.agents.known_pins import lookup
    pins = lookup(mpn)
    pin_names = {p["name"] for p in (pins or [])}

    # Derive a search query from the MPN
    QUERIES = {
        "RP2040": "RP2040 dual core ARM microcontroller",
        "AP2112K": "600mA LDO 3.3V SOT23-5",
        "MCP73831": "LiPo charger IC SOT23-5 1 cell",
        "USBLC6": "USB ESD protection TVS SOT-23-6",
        "PCM5102A": "I2S DAC audio stereo",
        "W25Q128": "128Mbit SPI NOR flash 8SOIC",
        "SSD1306": "0.96 OLED display I2C 128x64",
        "PEC11R": "rotary encoder 15mm shaft",
    }

    query = mpn
    for key, q in QUERIES.items():
        if mpn.upper().startswith(key):
            query = q
            break

    try:
        from swarm.vendors.aggregator import Aggregator
        agg = Aggregator()
        results = agg.search(query, limit=max_results * 2)
        alts = []
        for r in results:
            if not r.mpn or r.mpn.upper() == mpn.upper():
                continue
            # Check pin compatibility via known_pins
            alt_pins = lookup(r.mpn)
            compatible = False
            if alt_pins and pin_names:
                alt_pin_names = {p["name"] for p in alt_pins}
                overlap = len(pin_names & alt_pin_names) / max(len(pin_names), 1)
                compatible = overlap >= 0.7
            alts.append({
                "mpn": r.mpn,
                "manufacturer": r.manufacturer,
                "stock": r.stock,
                "price_inr": r.price_inr,
                "pin_compatible": compatible,
                "description": (r.description or "")[:80],
            })
            if len(alts) >= max_results:
                break

        return json.dumps({
            "original_mpn": mpn,
            "alternatives_found": len(alts),
            "alternatives": alts,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def validate_component_json(component_json: str) -> str:
    """Validate a component/2 JSON string against the schema.

    Checks: required fields, pin types, electrical domains.
    """
    errors = []
    try:
        data = json.loads(component_json)
    except json.JSONDecodeError as e:
        return json.dumps({"valid": False, "errors": [f"Invalid JSON: {e}"]})

    # component block
    c = data.get("component")
    if not c:
        errors.append("Missing 'component' block")
    else:
        for field in ("mpn", "manufacturer", "description", "category"):
            if not c.get(field):
                errors.append(f"component.{field} is missing or empty")

    # symbol block
    sym = data.get("symbol")
    if not sym:
        errors.append("Missing 'symbol' block")
    else:
        pins = sym.get("pins") or []
        if not pins:
            errors.append("symbol.pins is empty — no pin data")
        valid_types = {"power_in", "power_out", "input", "output", "io", "nc", "analog"}
        for i, pin in enumerate(pins):
            if not pin.get("name"):
                errors.append(f"pins[{i}].name missing")
            ptype = pin.get("type", "")
            if ptype and ptype not in valid_types:
                errors.append(f"pins[{i}].type '{ptype}' not in {valid_types}")

    # electrical block
    elec = data.get("electrical")
    if not elec:
        errors.append("Missing 'electrical' block (can be empty dict)")

    return json.dumps({
        "valid": len(errors) == 0,
        "error_count": len(errors),
        "errors": errors,
        "mpn": (data.get("component") or {}).get("mpn", "?"),
        "pin_count": len((data.get("symbol") or {}).get("pins") or []),
    }, indent=2)


# ── Datasheet extraction tool ─────────────────────────────────────────────────
@mcp.tool()
def extract_from_datasheet(pdf_path: str, package_variant: str = "") -> str:
    """
    Extract a component/2 JSON from a datasheet PDF using the vision LLM pipeline.

    This calls DatasheetExtractorAgent which:
      1. Converts PDF pages to images (requires pdf2image + poppler)
      2. Sends them to Claude with the full EXTRACTOR_PROMPT.md system prompt
      3. Validates the output against component.schema.json
      4. Retries on validation failures
      5. Saves the result to the local library automatically

    Args:
        pdf_path:        Path to the datasheet PDF file.
        package_variant: Optional package to extract (e.g. "SOT-223", "QFN-32").
                         If empty, the extractor picks the first SMD package.

    Returns JSON with the extracted component/2 data or an error report.
    """
    try:
        _agents = _root / "swarm" / "agents"
        if str(_agents) not in sys.path:
            sys.path.insert(0, str(_agents))
        from datasheet_extractor_agent import extract_component

        result = extract_component(pdf_path, package_variant or None)

        # Auto-save to library
        mpn = result["component"]["mpn"]
        out = _LIB_DIR / f"{mpn.replace(' ', '_').replace('/', '-')}.json"
        _LIB_DIR.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            json.dump(result, f, indent=2)

        return json.dumps({
            "status": "ok",
            "mpn": mpn,
            "saved_to": str(out),
            "pin_count": len(result.get("symbol", {}).get("pins", [])),
            "warnings": result.get("extraction", {}).get("warnings", []),
        }, indent=2)

    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


# ── Circuit advisor tool ───────────────────────────────────────────────────────
@mcp.tool()
def advise_circuit(circuit_state_path: str) -> str:
    """
    Run the full circuit advisor over an exported circuit-state JSON.

    This tool combines two advisors:
      1. Deterministic SI/PI rules (from docs/si-pi-advisor.md):
           - Via stub → backdrill recommendation
           - PDN impedance → add_decap at antinode
           - DDR hold margin → length_tune
           - DDR setup margin → set_eq
           - No-stub, low-loss closed eye → stackup/reference fix
      2. LLM advisor (Claude Opus with docs/circuit-advisor/ADVISOR_PROMPT.md):
           - Power budget verification
           - Unconnected pin classification
           - Interface completeness (USB ESD, pull-ups, terminations)
           - Electrical compatibility (VIH/VIL, drive/load, thermal)
           - DRC/ERC part-change fixes

    Returns the merged advice/1 JSON with all actions prioritized.
    """
    try:
        _agents = _root / "swarm" / "agents"
        if str(_agents) not in sys.path:
            sys.path.insert(0, str(_agents))
        from circuit_advisor_agent import analyze_circuit

        advice = analyze_circuit(circuit_state_path)
        return json.dumps(advice, indent=2)

    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


@mcp.tool()
def sipi_check(circuit_state_path: str) -> str:
    """
    Run only the deterministic SI/PI advisor (no LLM, instant result).

    Checks: channel eyes, PDN impedance, DDR timing margins, layer count.
    Returns advice/1 JSON with only SI/PI ops
    (backdrill, add_decap, move_decap, set_eq, length_tune, stackup, reference, set_layers).
    """
    try:
        _agents = _root / "swarm" / "agents"
        if str(_agents) not in sys.path:
            sys.path.insert(0, str(_agents))
        from sipi_advisor import recommend

        with open(circuit_state_path) as f:
            state = json.load(f)
        return json.dumps(recommend(state), indent=2)

    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")

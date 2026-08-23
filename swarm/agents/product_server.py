#!/usr/bin/env python3
"""Product Design MCP server — exposes the full autonomous design pipeline as MCP tools.

Runs as a stdio MCP server registered in ~/.claude/settings.json.

Tools:
  design_product       — full autonomous loop: intent → BOM + netlist
  search_components    — vendor search (DigiKey / Mouser / Nexar)
  check_availability   — single-part stock + price check
  quote_component      — exact-MPN quantity price-break evidence
  process_datasheet    — PDF URL → component/2 JSON
  render_product       — Blender photorealistic render
  list_materials       — catalog of enclosure materials + finishes
  design_status        — retrieve a previous session result
"""

import json
import sys
import os
import time
import uuid
from pathlib import Path

_site = Path.home() / ".local/lib/python3.14/site-packages"
if str(_site) not in sys.path:
    sys.path.insert(0, str(_site))

# Make swarm root importable
_swarm_root = Path(__file__).parents[2]
if str(_swarm_root) not in sys.path:
    sys.path.insert(0, str(_swarm_root))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "product-design",
    instructions=(
        "Autonomous electronic product design pipeline. "
        "Start with design_product(user_intent) for a full end-to-end design. "
        "Use individual tools for targeted searches, datasheet extraction, "
        "availability checks, and Blender renders."
    ),
)

# In-memory session store (survives the server process lifetime)
_sessions: dict[str, dict] = {}


@mcp.tool()
def design_product(user_intent: str, session_id: str | None = None) -> str:
    """Run the full autonomous product design loop.

    1. Parses user's natural language description (Gemini Pro)
    2. Searches DigiKey / Mouser / Nexar for every required component
    3. Checks availability and selects best-stocked parts
    4. Downloads and analyzes datasheets (Gemini Pro)
    5. Designs the netlist — all connections (Claude Sonnet)
    6. Detects missing components — decoupling caps, pull-ups, etc. (GPT-OSS 120B)
    7. Iterates: searches → fetches datasheets → re-designs for each gap
    8. Returns BOM, netlist, and session JSON

    user_intent: Natural language description, e.g.
      "I want a portable synthesizer with an OLED display, 4 rotary encoders,
       a headphone jack, runs on USB-C power, black anodized aluminum enclosure."

    Returns JSON with: product_name, bom, netlist, unresolved_gaps, enclosure_hint,
    aesthetic_hint, iterations, elapsed_s, log.
    """
    from swarm.agents.design_loop import run as _run

    sid = session_id or f"ds_{uuid.uuid4().hex[:8]}"

    try:
        result = _run(user_intent, session_id=sid)
        from dataclasses import asdict
        out = asdict(result)
        _sessions[sid] = out
        return json.dumps(out, indent=2)
    except Exception as e:
        err = {"session_id": sid, "status": "error", "error": str(e)}
        _sessions[sid] = err
        return json.dumps(err, indent=2)


@mcp.tool()
def search_components(query: str, limit: int = 8) -> str:
    """Search DigiKey, Mouser, and Nexar simultaneously for components.

    query:  e.g. "ATmega328P TQFP-32" or "100nF 0402 X5R 10V"
    limit:  max results per vendor (default 8)

    Returns ranked list of parts with MPN, stock, price, datasheet URL.
    """
    from swarm.vendors.aggregator import Aggregator
    agg = Aggregator()
    results = agg.search(query, limit=limit)
    parts = [
        {
            "mpn":          r.mpn,
            "manufacturer": r.manufacturer,
            "description":  r.description,
            "vendor":       r.vendor,
            "vendor_sku":   r.vendor_sku,
            "stock":        r.stock,
            "price_inr":    r.price_inr,
            "price_breaks": r.price_breaks,
            "retrieved_utc": r.retrieved_utc,
            "source_url": r.source_url,
            "datasheet_url": r.datasheet_url,
            "lifecycle":    r.lifecycle,
        }
        for r in results
    ]
    return json.dumps({
        "query": query,
        "vendors_searched": agg.vendors_online or ["none — set DIGIKEY_CLIENT_ID/SECRET, MOUSER_API_KEY, or NEXAR_CLIENT_ID/SECRET"],
        "vendor_errors": agg.last_errors,
        "results": parts,
    }, indent=2)


@mcp.tool()
def check_availability(mpn: str) -> str:
    """Check real-time stock and pricing for a specific part number.

    mpn: Manufacturer Part Number, e.g. "STM32F103C8T6" or "LM3671MFX-3.3"

    Returns: available (bool), total_stock, best_price_inr, vendors.
    """
    from swarm.vendors.aggregator import Aggregator
    agg = Aggregator()
    result = agg.check_availability(mpn)
    return json.dumps({"mpn": mpn, **result}, indent=2)


@mcp.tool()
def quote_component(mpn: str, quantity: int = 1) -> str:
    """Get an exact-MPN, quantity-aware supplier quote with source, time,
    original-currency price breaks, stock, lifecycle, and FX provenance."""
    from swarm.vendors.aggregator import Aggregator
    try:
        return json.dumps(Aggregator().quote(mpn, quantity=quantity), indent=2)
    except ValueError as exc:
        return json.dumps({"error": str(exc), "mpn": mpn, "quantity": quantity}, indent=2)


@mcp.tool()
def process_datasheet(url: str, mpn: str = "") -> str:
    """Download a datasheet PDF and extract full electrical + footprint data.

    Uses Gemini 3.1 Pro for complex ICs, Gemini Flash for simple passives.
    Returns a component/2 JSON (pin list, supply ranges, required externals,
    application circuits, footprint, package 3D info).

    url: Direct link to datasheet PDF
    mpn: Optional manufacturer part number hint for the model
    """
    from swarm.agents.datasheet_agent import process, DatasheetError
    try:
        component = process(url, mpn=mpn)
        return json.dumps(component, indent=2)
    except DatasheetError as e:
        return json.dumps({"error": str(e), "url": url}, indent=2)


@mcp.tool()
def render_product(
    output_path: str,
    enclosure_material: str = "aluminum_6061",
    enclosure_finish: str = "anodize_black",
    pcb_color: str = "black",
    board_stl: str | None = None,
    enclosure_stl: str | None = None,
    width_px: int = 1920,
    height_px: int = 1080,
    samples: int = 128,
) -> str:
    """Render a photorealistic product image using Blender (Cycles renderer).

    Requires: sudo snap install blender --classic

    output_path:        Where to save the PNG render (absolute path)
    enclosure_material: aluminum_6061 | aluminum_7075 | acrylic_clear |
                        acrylic_black | abs | polycarbonate | stainless_304 |
                        carbon_fiber
    enclosure_finish:   anodize_natural | anodize_black | anodize_space_grey |
                        anodize_orange | anodize_gold | bead_blast | brushed |
                        powder_coat_black
    pcb_color:          black | green | white | orange | blue | red | purple
    board_stl:          Optional path to PCB board STL (uses placeholder box if omitted)
    enclosure_stl:      Optional path to enclosure STL
    width_px/height_px: Render resolution (default 1920×1080)
    samples:            Cycles samples — higher = cleaner (default 128)
    """
    from swarm.render.blender_bridge import render, RenderConfig, RenderResult

    cfg = RenderConfig(
        output_path=output_path,
        board_stl=board_stl,
        enclosure_stl=enclosure_stl,
        enclosure_material=enclosure_material,
        enclosure_finish=enclosure_finish,
        pcb_color=pcb_color,
        width_px=width_px,
        height_px=height_px,
        samples=samples,
    )
    result: RenderResult = render(cfg)
    return json.dumps({
        "status":       result.status,
        "output_path":  result.output_path,
        "elapsed_s":    round(result.elapsed_s, 1),
        "install_hint": result.install_hint,
        "error":        result.stderr[:500] if result.stderr else None,
    }, indent=2)


@mcp.tool()
def list_materials() -> str:
    """List all available enclosure materials, surface finishes, and PCB colors.

    Returns the full catalog from materials/materials.yaml including:
    - PCB substrates and solder mask colors
    - Enclosure materials (aluminum, acrylic, ABS, stainless, carbon fiber)
    - Surface finishes (anodize, powder coat, brushed, bead blast)
    - Design style suggestions (exposed PCB, monolithic block, inside-out)
    """
    from swarm.render.blender_bridge import list_materials as _lm

    try:
        import yaml
        mat_path = Path(__file__).parents[2] / "swarm" / "materials" / "materials.yaml"
        if mat_path.exists():
            catalog = yaml.safe_load(mat_path.read_text())
            render_opts = _lm()
            return json.dumps({"catalog": catalog, "render_options": render_opts}, indent=2)
    except ImportError:
        pass

    return json.dumps({"render_options": _lm()}, indent=2)


@mcp.tool()
def design_status(session_id: str) -> str:
    """Retrieve the result of a previous design_product session.

    session_id: The session_id returned by design_product.
    """
    if session_id in _sessions:
        return json.dumps(_sessions[session_id], indent=2)
    return json.dumps({"error": f"Session '{session_id}' not found"}, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")

"""Autonomous product design loop.

Full pipeline:
  User intent (natural language)
    → IntentAgent   (Gemini Pro)         : understand product, list required components
    → Aggregator    (DigiKey/Mouser/Octo): search + availability check
    → DatasheetAgent (Codex Luna xhigh)  : PDF + page images → component/2 JSON
    → ConnectionAgent (Claude Sonnet)    : design netlist
    → GapDetection  (GPT-OSS 120B)      : find missing components
    → [iterate for gaps] ← back to Aggregator
    → Output: BOM + netlist + session JSON

Usage (as library):
    from swarm.agents.design_loop import run
    result = run("I want to build a portable synthesizer with OLED and knobs")

Usage (from MCP product_server.py):
    Called via ag_design_start MCP tool.
"""

import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Make swarm root importable
_swarm = Path(__file__).parents[1]
if str(_swarm) not in sys.path:
    sys.path.insert(0, str(_swarm.parent))

from swarm.vendors.aggregator import Aggregator
from swarm.agents import intent_agent, datasheet_agent, connection_agent
from swarm.agents.intent_agent import IntentResult, ComponentSpec
from swarm.agents.connection_agent import Netlist, MissingComponent
from swarm.agents.datasheet_agent import DatasheetError


MAX_ITERATIONS  = 4   # Gap-fill iterations before giving up
MAX_GAPS_PER_IT = 6   # Process at most 6 gaps per iteration (critical first)


def _datasheet_component(item: dict) -> dict | None:
    """Use the same exact-MPN API → official-first web → PDF trust boundary
    as the interactive component workflow.  This keeps the autonomous loop
    from silently having a weaker datasheet policy than the UI."""
    from swarm.memory.providers import provider_datasheet
    return provider_datasheet({"mpn": item.get("mpn", ""),
                               "manufacturer": item.get("manufacturer", ""),
                               "datasheet_url": item.get("url", ""),
                               "search_query": item.get("search_query", item.get("mpn", "")),
                               "role": item.get("role", "")})


@dataclass
class BOMLine:
    ref: str
    mpn: str
    manufacturer: str
    description: str
    quantity: int
    vendor: str
    vendor_sku: str
    stock: int | None
    price_inr: float | None
    datasheet_url: str | None


@dataclass
class DesignResult:
    session_id: str
    product_name: str
    product_type: str
    design_goal: str
    status: str              # "complete" | "partial" | "failed"
    iterations: int
    bom: list[BOMLine]
    netlist: dict            # serialized Netlist
    unresolved_gaps: list[dict]
    component_jsons: list[dict]
    enclosure_hint: str | None
    aesthetic_hint: str | None
    elapsed_s: float
    log: list[str] = field(default_factory=list)


def _log(entries: list[str], msg: str) -> None:
    entries.append(msg)
    print(f"[design_loop] {msg}", flush=True)


def _make_bom(components: list[dict], agg_results: dict[str, object]) -> list[BOMLine]:
    bom = []
    ref_counter: dict[str, int] = {}
    for comp in components:
        c   = comp.get("component", {})
        mpn = c.get("mpn", "?")
        category = c.get("category", "other")
        prefix = {
            "mcu": "U", "regulator": "U", "connector": "J",
            "passive": "C", "transistor": "Q", "module": "U",
        }.get(category, "U")
        ref_counter[prefix] = ref_counter.get(prefix, 0) + 1
        ref = f"{prefix}{ref_counter[prefix]}"

        part = agg_results.get(mpn)
        bom.append(BOMLine(
            ref=ref,
            mpn=mpn,
            manufacturer=c.get("manufacturer", ""),
            description=c.get("description", ""),
            quantity=1,
            vendor=getattr(part, "vendor", "") if part else "",
            vendor_sku=getattr(part, "vendor_sku", "") if part else "",
            stock=getattr(part, "stock", None) if part else None,
            price_inr=getattr(part, "price_inr", None) if part else None,
            datasheet_url=getattr(part, "datasheet_url", None) if part else None,
        ))
    return bom


def run(user_intent: str, session_id: str | None = None) -> DesignResult:
    t0  = time.monotonic()
    log: list[str] = []
    agg = Aggregator()

    if not session_id:
        session_id = f"ds_{int(time.time())}"

    _log(log, f"Session {session_id} started")
    _log(log, f"Vendors online: {agg.vendors_online or ['none — add API keys']}")

    # ── Phase 1: Intent parsing ────────────────────────────────────────────
    _log(log, "Phase 1: parsing intent...")
    try:
        intent: IntentResult = intent_agent.parse(user_intent)
        _log(log, f"Product: {intent.product_name} ({intent.product_type})")
        _log(log, f"Components required: {len(intent.components)}")
    except Exception as e:
        return DesignResult(
            session_id=session_id, product_name="unknown", product_type="other",
            design_goal=user_intent, status="failed", iterations=0, bom=[], netlist={},
            unresolved_gaps=[], component_jsons=[], enclosure_hint=None,
            aesthetic_hint=None, elapsed_s=time.monotonic() - t0,
            log=log + [f"Intent agent failed: {e}"],
        )

    # ── Phase 2: Component search + availability ───────────────────────────
    _log(log, "Phase 2: searching vendors...")
    best_parts: dict[str, object] = {}   # mpn → PartResult
    datasheet_queue: list[dict] = []     # [{"url": ..., "mpn": ...}]

    for spec in intent.components:
        results = agg.search(spec.search_query, limit=6)
        best    = agg.best(results)
        if best:
            best_parts[best.mpn] = best
            _log(log, f"  {spec.role}: {best.mpn} @ {best.vendor} "
                      f"(stock={best.stock}, ₹{best.price_inr})")
            if best.datasheet_url:
                datasheet_queue.append({"url": best.datasheet_url, "mpn": best.mpn,
                                        "manufacturer": best.manufacturer,
                                        "search_query": spec.search_query, "role": spec.role})
        else:
            _log(log, f"  {spec.role}: no vendor result for '{spec.search_query}'")

    # ── Phase 3: Datasheet processing ─────────────────────────────────────
    _log(log, f"Phase 3: processing {len(datasheet_queue)} datasheets...")
    component_jsons: list[dict] = []

    for item in datasheet_queue:
        _log(log, f"  Extracting {item['mpn']}...")
        try:
            cj = _datasheet_component(item)
            if not cj:
                raise DatasheetError("no API or validated internet datasheet source succeeded")
            component_jsons.append(cj)
            _log(log, f"  → {item['mpn']}: extracted "
                      f"{len((cj.get('symbol') or {}).get('pins') or [])} pins")
        except DatasheetError as e:
            _log(log, f"  → {item['mpn']}: datasheet failed ({e})")

    if not component_jsons:
        _log(log, "No components extracted — cannot design netlist")
        bom = _make_bom([], best_parts)
        return DesignResult(
            session_id=session_id, product_name=intent.product_name,
            product_type=intent.product_type, design_goal=intent.design_goal,
            status="partial", iterations=0, bom=bom, netlist={},
            unresolved_gaps=[], component_jsons=[], enclosure_hint=intent.enclosure_hint,
            aesthetic_hint=intent.aesthetic_hint, elapsed_s=time.monotonic() - t0, log=log,
        )

    # ── Phase 4 + 5+: Connection design + gap iteration ───────────────────
    netlist: Netlist | None = None
    gaps:    list[MissingComponent] = []
    iteration = 0

    while iteration <= MAX_ITERATIONS:
        _log(log, f"Phase 4 (iter {iteration}): designing connections...")
        netlist = connection_agent.design(component_jsons, intent.design_goal)
        _log(log, f"  Nets: {len(netlist.nets)}  Warnings: {len(netlist.warnings)}")
        for w in netlist.warnings:
            _log(log, f"  ⚠ {w}")

        _log(log, "Phase 5: gap detection...")
        gaps = connection_agent.detect_gaps(netlist, component_jsons)
        critical_gaps = [g for g in gaps if g.urgency == "critical"]
        _log(log, f"  Gaps: {len(gaps)} ({len(critical_gaps)} critical)")

        if not critical_gaps or iteration >= MAX_ITERATIONS:
            break

        # Fill gaps: search + fetch datasheets for critical missing components
        _log(log, f"Phase 6 (iter {iteration}): filling {min(len(critical_gaps), MAX_GAPS_PER_IT)} gaps...")
        filled = 0
        for gap in critical_gaps[:MAX_GAPS_PER_IT]:
            _log(log, f"  Searching for: {gap.role} — '{gap.search_query}'")
            results = agg.search(gap.search_query, limit=5)
            best    = agg.best(results)
            if not best:
                _log(log, f"  → not found in vendor APIs")
                continue
            best_parts[best.mpn] = best
            _log(log, f"  → found: {best.mpn} @ {best.vendor}")
            if best.datasheet_url:
                try:
                    cj = _datasheet_component({"url": best.datasheet_url, "mpn": best.mpn,
                                               "manufacturer": best.manufacturer,
                                               "search_query": gap.search_query,
                                               "role": gap.role})
                    if not cj:
                        raise DatasheetError("no API or validated internet datasheet source succeeded")
                    component_jsons.append(cj)
                    _log(log, f"  → extracted {best.mpn}")
                    filled += 1
                except DatasheetError as e:
                    _log(log, f"  → datasheet failed: {e}")

        if filled == 0:
            _log(log, "No gaps could be filled — stopping iteration")
            break

        iteration += 1

    # ── Build output ───────────────────────────────────────────────────────
    bom = _make_bom(component_jsons, best_parts)
    total_cost = sum(l.price_inr or 0 for l in bom)
    _log(log, f"BOM: {len(bom)} lines, estimated ₹{total_cost:.2f}")

    status = "complete" if not [g for g in gaps if g.urgency == "critical"] else "partial"

    netlist_dict = {}
    if netlist:
        netlist_dict = {
            "nets": {
                name: [{"from_ref": c.from_ref, "from_pin": c.from_pin,
                         "to_ref": c.to_ref, "to_pin": c.to_pin, "net_name": c.net_name}
                        for c in conns]
                for name, conns in netlist.nets.items()
            },
            "unconnected": netlist.unconnected,
            "warnings": netlist.warnings,
        }

    return DesignResult(
        session_id=session_id,
        product_name=intent.product_name,
        product_type=intent.product_type,
        design_goal=intent.design_goal,
        status=status,
        iterations=iteration,
        bom=bom,
        netlist=netlist_dict,
        unresolved_gaps=[
            {"role": g.role, "reason": g.reason, "urgency": g.urgency,
             "search_query": g.search_query}
            for g in gaps if g.urgency in ("critical", "recommended")
        ],
        component_jsons=component_jsons,
        enclosure_hint=intent.enclosure_hint,
        aesthetic_hint=intent.aesthetic_hint,
        elapsed_s=time.monotonic() - t0,
        log=log,
    )

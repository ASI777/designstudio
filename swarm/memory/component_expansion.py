"""Datasheet-driven component expansion.

Every anchor component pulls in the support circuitry its datasheet mandates:
for each `required_external` in the component/2 (a decoupling cap, pull-up,
crystal, etc.) we instantiate a real sub-component (C/R/L/Y with an IPC land
pattern) and record its connections — exactly as the datasheet's
`connect_between` specifies. So the BOM and netlist are built up from what each
part actually needs, not guessed flat by the LLM.

Returns:
  new_components : [{ref, mpn, role, search_query, derived_for}]   sub-components
  seed_netlist   : [{ref, pin, net}]   datasheet-mandated connections
"""
from __future__ import annotations
import re


def _ref_prefix(purpose: str, value: str) -> str:
    """Pick a ref-des prefix for a required external from its purpose/value."""
    t = f"{purpose} {value}".lower()
    if any(k in t for k in ("crystal", "resonator", "oscillator", "mhz", "khz")):
        return "Y"
    if any(k in t for k in ("inductor", "ferrite", "bead", "choke")) or re.search(r"\d\s*[uµn]h\b", t):
        return "L"
    if any(k in t for k in ("resistor", "pull-up", "pullup", "pull-down", "pulldown",
                            "ohm", "kω", "kohm")) or re.search(r"\d+\s*k?(?:ohm|Ω|r)\b", t):
        return "R"
    if any(k in t for k in ("diode", "tvs", "esd", "schottky", "led")):
        return "D"
    return "C"            # default: capacitor (decoupling/bypass/bulk)


def _seed_refs(components: list[dict]) -> dict[str, int]:
    """Highest existing number per ref prefix, so new refs don't collide."""
    counters: dict[str, int] = {}
    for c in components:
        ref = c.get("ref", "")
        pre = "".join(ch for ch in ref if ch.isalpha())
        digs = "".join(ch for ch in ref if ch.isdigit())
        if pre:
            counters[pre] = max(counters.get(pre, 0), int(digs or 0))
    return counters


def expand(components: list[dict], kg) -> tuple[list[dict], list[dict]]:
    """Instantiate datasheet-required sub-components + their connections."""
    counters = _seed_refs(components)
    new_components: list[dict] = []
    seed: list[dict] = []

    for c in list(components):
        node = kg.get(c.get("mpn", ""))
        if not node:
            continue
        parent = c.get("ref", "")
        for ext in getattr(node, "externals", []):
            if not ext.mandatory:
                continue
            pre = _ref_prefix(ext.purpose, ext.value)
            counters[pre] = counters.get(pre, 0) + 1
            sref = f"{pre}{counters[pre]}"
            new_components.append({
                "ref": sref,
                "mpn": ext.value or ext.purpose or "100nF 0402",
                "role": "passive" if pre in ("C", "R", "L") else "other",
                "search_query": ext.value or ext.purpose,
                "derived_for": parent,
            })
            # Connect the sub-component across the datasheet's stated nodes, and
            # tie the parent's like-named pin to the same net.
            nodes = [n for n in (ext.connect_between or []) if n][:2]
            for i, nodename in enumerate(nodes):
                net = nodename.upper()
                seed.append({"ref": sref, "pin": str(i + 1), "net": net})
                seed.append({"ref": parent, "pin": nodename, "net": net})

        # A selected, evidence-grounded manufacturer application circuit is
        # authoritative seed wiring. Multiple mutually exclusive modes are not
        # silently guessed: the configuration must name one.
        circuits = [item for item in getattr(node, "application_circuits", [])
                    if item.datasheet_pages and item.evidence_summary]
        selected_mode = str(c.get("application_mode") or "").strip().lower()
        selected = [item for item in circuits if item.mode.strip().lower() == selected_mode]
        if not selected and not selected_mode and len(circuits) == 1:
            selected = circuits
        for circuit in selected[:1]:
            pin_names = {pin.name.upper(): pin.number for pin in node.pins}
            pin_names.update({pin.number.upper(): pin.number for pin in node.pins})
            for connection in circuit.connections:
                source = str(connection.get("from_pin") or "").strip()
                target = str(connection.get("to") or "").strip()
                if not source or not target:
                    continue
                target_pin = pin_names.get(target.upper())
                net = target.upper()
                evidence = {"kind": "manufacturer_reference_circuit",
                            "mode": circuit.mode, "pages": circuit.datasheet_pages,
                            "summary": circuit.evidence_summary}
                seed.append({"ref": parent, "pin": source, "net": net,
                             "evidence": evidence})
                if target_pin:
                    seed.append({"ref": parent, "pin": target_pin, "net": net,
                                 "evidence": evidence})
    return new_components, seed


# ── self-test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from .component_kg import ComponentKG
    kg = ComponentKG().load()
    if not kg.components:
        from swarm.runtime_paths import component_fixture_dir
        kg.ingest_dir(str(component_fixture_dir())); kg.save()
    # TPS7A20 LDO has mandatory input/output caps in its datasheet.
    anchor = next((m for m in kg.components if "TPS7A20" in m),
                  next(m for m, n in kg.components.items() if n.externals))
    comps = [{"ref": "U1", "mpn": anchor}]
    new, seed = expand(comps, kg)
    print(f"anchor {anchor} → {len(new)} datasheet-required sub-components:")
    for nc in new:
        print(f"  {nc['ref']}: {nc['mpn']}  (for {nc['derived_for']})")
    print(f"\n{len(seed)} datasheet-mandated connections, e.g.:")
    for s in seed[:6]:
        print(f"  {s['ref']}.{s['pin']} → net {s['net']}")
    assert new and seed
    print("\ncomponent_expansion OK — sub-components + connections from datasheet")

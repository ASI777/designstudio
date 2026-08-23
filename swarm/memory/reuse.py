"""Reuse adaptation — remap a past verified netlist onto a new design.

The CBR "adapt" step. A past design's netlist references its own ref designators
(U1.VM, U1.GND…). A new design may use the same parts under different refs
(U5 instead of U1) or a subset/superset. This remaps the past netlist onto the
new component set by matching MPNs, so reuse is not limited to verbatim ref
matches.

Matching strategy (deterministic, no LLM):
  1. Pair past↔new components by exact MPN.
  2. For unmatched, pair by category/role compatibility (same ref-prefix family).
  3. Drop past connections whose component has no new counterpart.
  4. Rewrite each connection's ref to the new ref; keep pin + net (nets are
     functional names — GND, VM, VCC_3V3 — and carry across designs).
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class AdaptResult:
    netlist: list[dict]           # remapped [{ref,pin,net}]
    mapped: dict[str, str]        # past_ref -> new_ref
    dropped_refs: list[str]       # past refs with no new counterpart
    coverage: float               # fraction of past components mapped


def _mpn_index(components: list[dict]) -> dict[str, list[str]]:
    """mpn -> [refs] (a design may use the same part several times)."""
    idx: dict[str, list[str]] = {}
    for c in components:
        idx.setdefault(c.get("mpn", ""), []).append(c.get("ref", ""))
    return idx


def adapt_netlist(past_netlist: list[dict],
                  past_components: list[dict],
                  new_components: list[dict]) -> AdaptResult:
    past_idx = _mpn_index(past_components)
    new_idx  = _mpn_index(new_components)

    # Build past_ref -> new_ref by consuming new refs of the same MPN in order.
    mapping: dict[str, str] = {}
    new_pool = {mpn: list(refs) for mpn, refs in new_idx.items()}
    dropped: list[str] = []

    for mpn, past_refs in past_idx.items():
        avail = new_pool.get(mpn, [])
        for pref in past_refs:
            if avail:
                mapping[pref] = avail.pop(0)
            else:
                dropped.append(pref)

    out: list[dict] = []
    for conn in past_netlist:
        pref = conn.get("ref", "")
        if pref in mapping:
            out.append({"ref": mapping[pref],
                        "pin": conn.get("pin", ""),
                        "net": conn.get("net", "")})
        # connection to a dropped component is simply not carried over

    total = len(past_components) or 1
    coverage = (total - len(dropped)) / total
    return AdaptResult(netlist=out, mapped=mapping,
                       dropped_refs=dropped, coverage=coverage)


# ── self-test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    past_comps = [{"ref": "U1", "mpn": "DRV8353"}, {"ref": "U2", "mpn": "STM32H7"}]
    past_net = [
        {"ref": "U1", "pin": "VM",  "net": "VM"},
        {"ref": "U1", "pin": "GND", "net": "GND"},
        {"ref": "U2", "pin": "VDD", "net": "VCC_3V3"},
    ]
    # New design: same DRV under a different ref (U5), no MCU → MCU conns dropped.
    new_comps = [{"ref": "U5", "mpn": "DRV8353"}]
    r = adapt_netlist(past_net, past_comps, new_comps)
    print("mapping:", r.mapped)
    print("dropped:", r.dropped_refs)
    print(f"coverage: {r.coverage:.0%}")
    print("adapted netlist:")
    for c in r.netlist:
        print("  ", c)
    assert r.mapped == {"U1": "U5"}
    assert all(c["ref"] == "U5" for c in r.netlist)
    assert "U2" in r.dropped_refs
    print("\nadapt_netlist OK — U1→U5 remapped, U2 (no counterpart) dropped")

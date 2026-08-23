#!/usr/bin/env python3
"""ERC engine over design-studio netlists with datasheet-sourced correction proposals.

The engine does not merely flag problems: every finding carries a structured
proposal (add_component with value/pins/reason sourced from the component's
required_externals contract).  Applying a proposal mutates the netlist, so the
schematic circuit converges toward datasheet-correct topology under approval.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RAIL_HINTS = ("VDD", "VCC", "3V3", "5V", "VBATT", "VOUT", "1V8", "2V8")
GND_NAMES = {"GND", "AGND", "DGND", "PGND"}


def _is_ground(name: str) -> bool:
    return name.upper() in GND_NAMES


def _is_rail(name: str) -> bool:
    return any(h in name.upper() for h in RAIL_HINTS)


def load_netlist(path: Path) -> dict:
    d = json.loads(path.read_text())
    comps = {}
    for c in d.get("components", []):
        comps[c["ref"]] = c
    return {"schema": d.get("schema", "design-studio.netlist/0"),
            "nets": d.get("nets", {}),
            "components": comps,
            "raw": d}


def load_required_externals(component_dir: Path | None, mpn: str) -> list[dict]:
    """Look up the datasheet-derived required_externals for an MPN."""
    if not component_dir:
        return []
    for jf in Path(component_dir).glob("*.json"):
        try:
            d = json.loads(jf.read_text())
        except Exception:
            continue
        comp = d.get("component", {}) if isinstance(d, dict) else {}
        lib_mpn = str(comp.get("mpn", "")).strip().upper()
        target = mpn.strip().upper()
        if not lib_mpn:
            continue
        # tolerant family match: either MPN is a prefix of the other, or they
        # share a leading alnum token of >=4 chars (handles package suffixes)
        norm = lambda x: "".join(ch for ch in x if ch.isalnum())
        a, b = norm(lib_mpn), norm(target)
        if not (a.startswith(b) or b.startswith(a)
                or (len(a) >= 4 and len(b) >= 4
                    and (a[:6] == b[:6] or b.split("-")[0] == a.split("-")[0]))):
            continue
        el = d.get("electrical", {})
        ext = el.get("required_externals") or []
        out = []
        for item in ext:
            if isinstance(item, dict) and item.get("mandatory", True):
                out.append(item)
        return out
    return []


def _cap_on_net(nets: dict, cap_refs: set, *rail_names) -> list[str]:
    hits = set()
    wanted = {n.upper() for n in rail_names}
    for net_name, conns in nets.items():
        refs = {c["ref"] for c in conns}
        caps = refs & cap_refs
        if not caps:
            continue
        other = refs - caps
        for o in other:
            if o.upper() in wanted or _is_rail(net_name) or _is_ground(net_name):
                hits |= caps
    return sorted(hits)


def run_erc(netlist: dict, component_dir: Path | None = None,
            lib_index: dict | None = None) -> dict:
    nets = netlist["nets"]
    components = netlist["components"]
    findings: list[dict] = []
    next_c = 100

    def propose(ref_hint, value=None, mpn=None, connect_between=None,
                reason="", source="datasheet-analysis"):
        nonlocal next_c
        next_c += 1
        findings.append({
            "severity": "warning",
            "code": "PROPOSE_COMPONENT",
            "message": f"Propose adding {ref_hint}: "
                       f"{value or mpn} ({reason})",
            "proposal": {
                "op": "add_component",
                "ref": ref_hint,
                "value": value,
                "mpn": mpn,
                "connect_between": connect_between,
                "reason": reason,
                "source": source,
            },
        })

    # --- 1. connectivity checks -------------------------------------------
    pin_owner = {}
    for ref, comp in components.items():
        for pname, nname in (comp.get("pins") or {}).items():
            pin_owner[(ref, pname)] = nname

    for net_name, conns in sorted(nets.items()):
        if _is_ground(net_name):
            continue
        refs = [c["ref"] for c in conns]
        if len(conns) == 1:
            findings.append({"severity": "error", "code": "DANGLING_NET",
                             "message": f"net '{net_name}' has a single connection "
                                        f"({refs[0]}); mark no-connect or wire it"})
        elif len(conns) == 0:
            continue
        drivers = [c["ref"] for c in conns
                   if _is_rail(net_name) is False and False]
        has_active = any(
            (components.get(r, {}).get("role") in ("mcu","buck","charger","usb"))
            or str(components.get(r, {}).get("mpn") or "").startswith(("LMR6","MCP7"))
            for r in refs)
        if _is_rail(net_name) and len(conns) >= 2 and not has_active:
            findings.append({"severity": "error", "code": "UNDRIVEN_RAIL",
                             "message": f"rail net '{net_name}' has {len(conns)} loads "
                                        f"but no source/regulator connection"})

    # --- 2. datasheet required_externals conformance ----------------------
    for ref, comp in components.items():
        mpn = comp.get("mpn", "")
        if not mpn:
            continue
        req = load_required_externals(component_dir, mpn)
        for item in req:
            purpose = item.get("purpose", "external requirement").lower()
            value = item.get("value")
            cb = item.get("connect_between") or []
            satisfied = False
            # evidence: any existing component whose value matches, touching
            # one of the referenced function nets (by name substring)
            fn_nets = [n for n in nets if any(pn.lower() in n.lower() for pn in cb)]
            if not fn_nets and "decoupl" in purpose:
                own = [n for n in (comp.get("pins") or {}).values()
                       if n in nets and _is_rail(n)]
                fn_nets = own if own else [n for n in nets if _is_rail(n)]
            for n in fn_nets:
                for conn in nets[n]:
                    other = components.get(conn["ref"])
                    if other is None:
                        continue
                    oval = str(other.get("value", ""))
                    if value and value.replace(" ", "").lower() in oval.replace(" ", "").lower():
                        satisfied = True
            if satisfied:
                continue
            if "load capacitor" in purpose and "XTAL" in "".join(cb).upper():
                side = "XTAL1" if "XTAL1" in "".join(cb).upper() else "XTAL2"
                propose(ref_hint=f"C_XTAL_{side}", value=value,
                        connect_between=[side, "GND"],
                        reason=f"{ref} crystal oscillator requires {value} load "
                               f"on {side} (CL spec); none found on net",
                        source=f"{mpn} required_externals")
            elif "decoupl" in purpose:
                own_rails = [n for n in (comp.get("pins") or {}).values()
                             if n in nets and _is_rail(n)]
                rail_net = own_rails[0] if own_rails else                     (nets and next(iter(nets)))
                propose(ref_hint=f"C_DCP_{next_c}", value=value,
                        connect_between=[rail_net or "3V3", "GND"],
                        reason=f"{ref} ({mpn}) requires {value} decoupling "
                               f"per '{item.get('constraints','')[:60]}'; "
                               f"evidence not found in netlist",
                        source=f"{mpn} required_externals")
            elif "pull" in purpose:
                net_hit = [n for n in nets if any(pn.lower() in n.lower() for pn in cb)]
                propose(ref_hint=f"R_PULL_{next_c}",
                        value=item.get("value", "10k"),
                        connect_between=[net_hit[0] if net_hit else "?", "3V3"]
                        if "up" in purpose else [net_hit[0] if net_hit else "?", "GND"],
                        reason=f"{ref} requires {item.get('value')} pull "
                               f"({purpose}); missing",
                        source=f"{mpn} required_externals")
            else:
                propose(ref_hint=f"X_REQ_{next_c}", value=value,
                        connect_between=cb,
                        reason=f"{ref} datasheet requires: {purpose}",
                        source=f"{mpn} required_externals")

    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    return {"schema": "design-studio.erc-report/0",
            "errors": errors, "warnings": warnings,
            "proposals": [f["proposal"] for f in warnings
                          if "proposal" in f],
            "counts": {"errors": len(errors), "warnings": len(warnings),
                       "proposals": len([f for f in warnings if "proposal" in f])}}


def _resolve_endpoint(token, nets, components):
    """cb entries may be net names OR pin names of an existing part."""
    if token in nets:
        return token
    upper = str(token).upper()
    for ref, comp in components.items():
        for pname, nname in (comp.get("pins") or {}).items():
            if pname.upper() == upper:
                return nname
    return None


def apply_proposal(netlist_raw: dict, proposal: dict, new_ref: str,
                   fallback_rail: str | None = None) -> bool:
    """Mutate the raw netlist: add the proposed component + wire its pins."""
    nets = netlist_raw.setdefault("nets", {})
    components = {c.get("ref"): c for c in netlist_raw.get("components", [])}
    cb = []
    for token in (proposal.get("connect_between") or []):
        resolved = _resolve_endpoint(token, nets, components)
        if resolved is None:
            resolved = fallback_rail or (
                "GND" if str(token).upper() in GND_NAMES else None)
        if resolved is None:
            return False
        cb.append(resolved)
    value = proposal.get("value") or ""
    ref = new_ref
    raw = netlist_raw
    comps = raw.setdefault("components", [])
    if any(c.get("ref") == ref for c in comps):
        return False
    pins = {}
    if len(cb) == 2:
        pins = {"1": cb[0], "2": cb[1]}
    elif len(cb) == 1:
        pins = {"1": cb[0]}
    comps.append({"ref": ref, "mpn": proposal.get("mpn"), "value": value,
                  "pins": pins})
    for i, net in enumerate(cb):
        nets = raw.setdefault("nets", {})
        nets.setdefault(net, []).append({"ref": ref, "pin": str(i + 1)})
    adds = raw.setdefault("components_to_add", [])
    adds.append({"ref": ref, "op": "add", "mpn": proposal.get("mpn"),
                 "value": value,
                 "reason": proposal.get("reason"),
                 "source": proposal.get("source"), "priority": 1})
    return True


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("netlist")
    ap.add_argument(
        "--component-dir",
        default=str(Path(__file__).resolve().parents[1]),
    )
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    nl = load_netlist(Path(args.netlist))
    report = run_erc({"nets": nl["nets"], "components": nl["components"]},
                     component_dir=Path(args.component_dir))
    print(json.dumps(report["counts"]))
    for f in report["errors"]:
        print("ERROR:", f["code"], "-", f["message"])
    for w in report["warnings"]:
        print("WARN :", w["code"], "-", w["message"])
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))

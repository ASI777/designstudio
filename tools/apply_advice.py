#!/usr/bin/env python3
"""
apply_advice.py  -  close the advisor loop: design-studio.advice/1 -> a netlist

Today the tool only imports component/2 *footprints*; the advisor's `connections`
(pin -> to_net) and every part's power rails are re-entered by hand. This prototype
turns intent into connectivity deterministically:

  1. Power-rail bundling  - for each placed component that carries
     electrical.power_domains, create ONE net per rail and attach every member
     ball. (This is the 100+-pin job that was fully manual.)
  2. Apply advice          - every action.connections[pin -> to_net] becomes a net
     membership; each `add` gets a provisional RefDes (U2, U3, ...).
  3. Net classes           - each component's high_speed.diff_pairs become net
     classes (impedance carried for width synthesis later).

Output: a netlist.json (nets, net_classes, components_to_add) plus a readable
report. This is the artifact a real "Apply Advice" command in Design Studio would
consume to assign pad nets instead of leaving them at -1.

Usage:
  python apply_advice.py --advice FOC_servo_advice.json \
         --placed U1=MPFS025TC_FCSG325.json --out FOC_netlist.json
"""
import json, argparse, re
from collections import defaultdict

def load(p): return json.load(open(p))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--advice", required=True)
    ap.add_argument("--placed", action="append", default=[],
                    help="REF=component2.json for each already-placed part (repeatable)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    advice = load(a.advice)
    placed = {}
    for spec in a.placed:
        ref, path = spec.split("=", 1)
        placed[ref] = load(path)

    nets = defaultdict(list)          # net name -> [{ref, pin}]
    net_classes = []
    notes = []

    # 1) power-rail bundling from power_domains
    rails_made = 0
    for ref, comp in placed.items():
        pds = (comp.get("electrical") or {}).get("power_domains") or []
        for d in pds:
            netname = d["name"] if d["name"] != "VSS" else "GND"
            for pin in d["pins"]:
                nets[netname].append({"ref": ref, "pin": pin})
            rails_made += 1
        if not pds:
            notes.append(f"{ref}: no power_domains in component file - rails NOT auto-bundled "
                         f"(regenerate the component with ppat_to_component.py).")
        # 3) net classes from diff pairs
        for dp in ((comp.get("electrical") or {}).get("high_speed") or {}).get("diff_pairs", []):
            net_classes.append({"name": f"{dp['positive']}/{dp['negative']}",
                                 "impedance_ohm": dp["impedance_ohm"], "differential": True,
                                 "max_skew_mm": dp.get("max_skew_mm")})

    # 2) apply advice: connections + provisional refs for adds
    components_to_add = []
    next_ref = 2
    for act in advice.get("actions", []):
        op = act.get("op")
        ref = act.get("for_ref")
        if op == "add":
            ref = f"U{next_ref}"; next_ref += 1
            components_to_add.append({"ref": ref, "op": op, "mpn": act.get("mpn"),
                                      "manufacturer": act.get("manufacturer"),
                                      "reason": act.get("reason"), "priority": act.get("priority")})
        for c in act.get("connections") or []:
            if c.get("to_net") and c.get("pin"):
                nets[c["to_net"]].append({"ref": ref or "?", "pin": c["pin"]})

    netlist = {
        "schema": "design-studio.netlist/0 (prototype)",
        "from_advice": a.advice,
        "nets": {k: v for k, v in sorted(nets.items())},
        "net_classes": net_classes,
        "components_to_add": components_to_add,
        "notes": notes,
    }
    json.dump(netlist, open(a.out, "w"), indent=2)

    # report
    print(f"== apply_advice -> {a.out} ==")
    print(f"placed parts          : {', '.join(placed) or '(none)'}")
    print(f"power rails bundled    : {rails_made}")
    print(f"nets created           : {len(nets)}")
    biggest = sorted(nets.items(), key=lambda kv: -len(kv[1]))[:6]
    for name, mem in biggest:
        print(f"   {name:14s} {len(mem):3d} pins")
    print(f"net classes (diff)     : {len(net_classes)}")
    print(f"components to add       : {len(components_to_add)} "
          f"(prio1: {sum(1 for c in components_to_add if c['priority']==1)})")
    pin_in_nets = sum(len(v) for v in nets.values())
    print(f"total pin->net assigns : {pin_in_nets}")
    for n in notes: print("  note:", n)

if __name__ == "__main__":
    main()

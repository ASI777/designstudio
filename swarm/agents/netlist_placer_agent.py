#!/usr/bin/env python3
"""netlist_placer_agent.py — Hierarchical PCB placement from netlist topology.

Algorithm:
  1. Detect each IC's functional role from its package/library name
  2. Find exclusive IC-to-IC net connections (signal or single power net)
  3. Cluster ICs into sub-systems: first by signal nets, then by exclusive power
     coupling, finally by package role (e.g. "gate driver + MOSFET → Power Stage")
  4. Print discovered topology so the designer can see what the agent understood
  5. Floor-plan sub-systems on the board using force-directed on cluster graph
  6. Within each sub-system, place ICs so connected pads face each other —
     the hub IC goes at the cluster anchor; each satellite is placed in the
     direction of the hub's shared-net pad centroid
  7. Place every passive at the board coordinate of the IC pad it decouples,
     offset outward from the IC centre; fan multiple passives out perpendicular
  8. Move connectors to board edges, clear routes

Usage:
  python3 netlist_placer_agent.py <project.dsproj>
"""
from __future__ import annotations
import json, math, sys, time, os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

BOARD_W, BOARD_H, GRID = 100.0, 80.0, 0.5

POWER_PREFIXES = (
    'gnd','vdd','vcc','vpp','vm','vin','in_','dvdd','v1p','vdda',
    'vddq','vddaux','in_12','3v3','v1p8','vdd18','vdd25','vddi',
    'vdda2','vdda1','vdd_c','vdd_x','vdda_',
)

def is_power_net(name: str) -> bool:
    n = name.lower()
    return any(n.startswith(p) or n == p for p in POWER_PREFIXES)

def snap(v: float) -> float:
    return round(round(v / GRID) * GRID, 6)

# ── Package-based functional role ─────────────────────────────────────────────
# Derived from library/ref strings, not hardcoded per-component.

PACKAGE_ROLES: list[tuple[tuple[str,...], str]] = [
    # (lib-substrings,  role-name)
    (("fcsg", "bga325", "polarfire"),        "SoC"),
    (("bga78", "mt40a", "lpddr", "ddr"),     "Memory"),
    (("drv8", "drv8353","qfn50p600x600x80"), "Gate Driver"),
    (("pg-tdson", "tdson", "bsc09", "bsc04"),"MOSFET"),
    (("mpm3833", "mpm3610", "qfn50p250x350"),"Regulator"),
    (("x2son", "ldo", "tps"),                "LDO"),
    (("soic127","soic","flash","m25p","w25"), "Flash"),
    (("osc-smd", "sit9120", "oscillator"),   "Oscillator"),
    (("dp83822", "phy", "rhb0032"),          "Ethernet PHY"),
    (("tssop16","tssop","uart","rs422","rs485"),"Interface"),
    (("th_2pin","screw","pwr"),              "Power Connector"),
    (("th_6pin","jtag","swd","debug"),       "JTAG Header"),
]

ROLE_SUBSYSTEM: dict[str, str] = {
    "SoC":              "SoC Core",
    "Memory":           "SoC Core",   # DDR4 stays with SoC
    "Flash":            "SoC Core",   # boot flash stays with SoC
    "Oscillator":       "SoC Core",   # oscillator satellite of SoC
    "Gate Driver":      "Power Stage",
    "MOSFET":           "Power Stage",
    "Regulator":        "Power Supply",
    "LDO":              "Power Supply",
    "Ethernet PHY":     "Communications",
    "Interface":        "Communications",
    "Power Connector":  "_connector",
    "JTAG Header":      "_connector",
}

# Ideal sub-system zone anchors (x_centre, y_centre) — starting positions for
# force-directed refinement, encoding PCB layout expertise.
ZONE_ANCHORS: dict[str, tuple[float,float]] = {
    "SoC Core":       (28.0, 24.0),
    "Power Stage":    (76.0, 56.0),
    "Power Supply":   (16.0, 63.0),
    "Communications": (55.0, 14.0),
}

BODY_HALF: dict[str,float] = {
    "U1":5.8,"U5":4.8,"U2":2.0,"U3":2.5,"U4":1.0,
    "U6":2.4,"U7":2.0,"U8":3.5,"U9":3.0,"U10":3.8,
    "Q1":3.0,"Q2":3.0,
}

# ── Geometry ──────────────────────────────────────────────────────────────────

def pad_board_pos(fp:dict, pad:dict) -> tuple[float,float]:
    rad = math.radians(fp.get("rot_deg",0))
    px = fp["x_mm"] + pad["x_mm"]*math.cos(rad) - pad["y_mm"]*math.sin(rad)
    py = fp["y_mm"] + pad["x_mm"]*math.sin(rad) + pad["y_mm"]*math.cos(rad)
    return px, py

def body_half(fp:dict) -> float:
    r = fp.get("ref","")
    if r in BODY_HALF: return BODY_HALF[r]
    if not fp["pads"]: return 1.5
    xs=[abs(p["x_mm"]) for p in fp["pads"]]; ys=[abs(p["y_mm"]) for p in fp["pads"]]
    return max(max(xs,default=1), max(ys,default=1)) + 0.5

def clamp_to_board(fp:dict) -> None:
    r = body_half(fp)
    fp["x_mm"] = snap(max(r+1, min(BOARD_W-r-1, fp["x_mm"])))
    fp["y_mm"] = snap(max(r+1, min(BOARD_H-r-1, fp["y_mm"])))

def detect_role(fp: dict, net_lut: Optional[dict[int,str]] = None) -> str:
    lib = fp.get("lib","").lower()
    ref = fp.get("ref","").lower()
    combined = lib + " " + ref
    for keywords, role in PACKAGE_ROLES:
        if any(k in combined for k in keywords):
            return role
    # Net-based fallback: an IC that takes a raw input voltage rail (IN_12V, VIN)
    # on any pad is a power supply component, regardless of package name.
    if net_lut and fp.get("pads"):
        nets = {net_lut.get(p["net"],"") for p in fp["pads"] if p["net"] >= 0}
        if any(n.lower() in ("in_12v","vin","in") for n in nets):
            return "Regulator"
        # IC that only has power/GND nets assigned → power supply component
        sig_nets = [n for n in nets if n and not is_power_net(n)]
        if not sig_nets and len(nets) >= 2:
            return "Regulator"
    return "IC"

def boxes_overlap(a:dict, b:dict) -> bool:
    ma = 0.15 if a["ref"][0] not in ("U","Q","J") else 0.4
    mb = 0.15 if b["ref"][0] not in ("U","Q","J") else 0.4
    def bb(fp,m):
        if not fp["pads"]: return fp["x_mm"]-1,fp["y_mm"]-1,fp["x_mm"]+1,fp["y_mm"]+1
        xs,ys = zip(*[pad_board_pos(fp,p) for p in fp["pads"]])
        return min(xs)-m,min(ys)-m,max(xs)+m,max(ys)+m
    ax0,ay0,ax1,ay1=bb(a,ma); bx0,by0,bx1,by1=bb(b,mb)
    return not(ax1<bx0 or bx1<ax0 or ay1<by0 or by1<ay0)

def count_overlaps(fps:list[dict]) -> int:
    return sum(1 for i in range(len(fps)) for j in range(i+1,len(fps))
               if boxes_overlap(fps[i],fps[j]))

# ── Graph construction (IC-only exclusive coupling) ───────────────────────────

@dataclass
class Edge:
    weight:      int        = 0
    shared_nets: list[int]  = field(default_factory=list)

GND_NAMES = {"gnd","agnd","pgnd","dgnd"}

def build_ic_graph(fps:list[dict], net_lut:dict[int,str]
                   ) -> tuple[dict[str,dict[str,Edge]], dict[int,list[dict]]]:
    """Build weighted IC-to-IC adjacency using only IC-exclusive net coupling."""
    IC_PREFIXES = ("U","Q")

    net_pads: dict[int,list[dict]] = defaultdict(list)
    for fp in fps:
        for pad in fp["pads"]:
            if pad["net"] < 0: continue
            bx,by = pad_board_pos(fp,pad)
            net_pads[pad["net"]].append({
                "ref":fp["ref"],"bx":bx,"by":by,
                "rel_x":pad["x_mm"],"rel_y":pad["y_mm"],
            })

    adj: dict[str,dict[str,Edge]] = defaultdict(lambda: defaultdict(Edge))
    gnd_ids = {nid for nid,name in net_lut.items() if name.lower() in GND_NAMES}

    for nid, pads in net_pads.items():
        if nid in gnd_ids: continue
        name = net_lut.get(nid,"")

        # Only count ICs/transistors for exclusivity — ignore caps, connectors
        ic_refs_on_net = {p["ref"] for p in pads if p["ref"][0] in IC_PREFIXES}
        if len(ic_refs_on_net) < 2: continue

        is_sig   = not is_power_net(name)
        exclusive = len(ic_refs_on_net) == 2  # this net only connects these 2 ICs

        if is_sig:         w = 10
        elif exclusive:    w = 5
        else:              w = 1

        refs = list(ic_refs_on_net)
        for i in range(len(refs)):
            for j in range(i+1,len(refs)):
                a,b = refs[i],refs[j]
                adj[a][b].weight       += w
                adj[a][b].shared_nets.append(nid)
                adj[b][a].weight       += w
                adj[b][a].shared_nets.append(nid)

    return adj, net_pads

# ── Sub-system clustering ─────────────────────────────────────────────────────

def cluster_subsystems(ic_fps:list[dict], adj:dict[str,dict[str,Edge]],
                       net_lut:Optional[dict[int,str]]=None) -> dict[str, list[str]]:
    """Role-first clustering.

    Phase 1: Primary assignment from package role — no guessing, no net-pollution.
             Gate Driver + MOSFET → Power Stage
             SoC + Memory + Flash + Oscillator → SoC Core
             Regulator + LDO → Power Supply
             Ethernet PHY + Interface → Communications

    Phase 2: Signal net overrides — if two ICs share a signal net (weight ≥ 10)
             they belong together regardless of role (future-proof when more
             signal nets are assigned).

    Phase 3: Intra-role merging via netlist — ICs already in the same role
             cluster are ordered by connection strength (used later for
             pad-directed placement, not for changing cluster membership).
    """
    # Phase 1: role-based primary assignment
    ss_groups: dict[str, list[str]] = defaultdict(list)
    for fp in ic_fps:
        role = detect_role(fp, net_lut)
        ss_name = ROLE_SUBSYSTEM.get(role, "Other")
        if ss_name != "_connector":
            ss_groups[ss_name].append(fp["ref"])

    # Phase 2: signal-net overrides (handles future fully-assigned netlists)
    for a in (fp["ref"] for fp in ic_fps):
        for b, edge in adj[a].items():
            if b not in (fp["ref"] for fp in ic_fps):
                continue
            if edge.weight >= 10:  # signal net — must be co-located
                # Find which sub-systems a and b are in
                ss_a = next((k for k,v in ss_groups.items() if a in v), None)
                ss_b = next((k for k,v in ss_groups.items() if b in v), None)
                if ss_a and ss_b and ss_a != ss_b:
                    # Move the smaller group's member into the larger group
                    if len(ss_groups[ss_a]) >= len(ss_groups[ss_b]):
                        ss_groups[ss_a].append(b)
                        ss_groups[ss_b].remove(b)
                    else:
                        ss_groups[ss_b].append(a)
                        ss_groups[ss_a].remove(a)

    # Remove empty groups
    return {k: v for k, v in ss_groups.items() if v}

# ── Sub-system data class ─────────────────────────────────────────────────────

@dataclass
class Subsystem:
    idx:     int
    members: list[str]
    hub:     str
    anchor:  list[float]  # [x, y] — mutable for force refinement
    name:    str

def name_cluster(cluster_key: str, members:list[str], fps_by_ref:dict) -> str:
    """Return the cluster name — cluster_key is already the role-based name
    from cluster_subsystems(), so just return it directly."""
    return cluster_key if cluster_key != "Other" else "Miscellaneous"

# ── Topology printout ─────────────────────────────────────────────────────────

def print_topology(subsystems:list[Subsystem],
                   fps_by_ref:dict,
                   adj:dict[str,dict[str,Edge]],
                   net_lut:dict[int,str]) -> None:
    print("\n  ╔══ Discovered system topology ═══════════════════╗")
    for ss in subsystems:
        roles = {fps_by_ref[r]["ref"]: detect_role(fps_by_ref[r], net_lut) for r in ss.members}
        print(f"  ║  Sub-system {ss.idx}: {ss.name}")
        for ref,role in sorted(roles.items()):
            print(f"  ║    {ref:5s}  [{role}]")
        # Inter-subsystem connections
        inter: dict[int,list[str]] = defaultdict(list)
        for ref in ss.members:
            for other_ref, edge in adj[ref].items():
                other_ss = next((s for s in subsystems if other_ref in s.members), None)
                if other_ss and other_ss.idx != ss.idx:
                    for nid in edge.shared_nets:
                        inter[other_ss.idx].append(net_lut.get(nid,"?"))
        if inter:
            print(f"  ║    Connects to:")
            for oidx, nets in sorted(inter.items()):
                oname = next(s.name for s in subsystems if s.idx==oidx)
                unique = sorted(set(nets))[:4]
                print(f"  ║      → {oname}: {', '.join(unique)}")
        print("  ║")
    print("  ╚═════════════════════════════════════════════════╝")

# ── Sub-system floor-plan (force-directed on cluster graph) ───────────────────

def assign_anchors(subsystems:list[Subsystem], adj:dict[str,dict[str,Edge]]) -> None:
    # Initialise from known zone anchors, with small perturbation to avoid
    # identical starts for same-name clusters
    for i, ss in enumerate(subsystems):
        base = list(ZONE_ANCHORS.get(ss.name, [50.0, 40.0]))
        ss.anchor = [base[0] + i*0.1, base[1] + i*0.1]

    k_spring, k_repel, k_zone, step = 0.04, 100.0, 0.25, 10.0

    for it in range(80):
        step_now = step * (1 - it/80*0.85)
        forces = [[0.0,0.0] for _ in subsystems]

        for i in range(len(subsystems)):
            for j in range(i+1,len(subsystems)):
                si,sj = subsystems[i],subsystems[j]
                cross = sum(adj[a][b].weight
                            for a in si.members for b in sj.members if b in adj[a])
                dx = sj.anchor[0]-si.anchor[0]; dy = sj.anchor[1]-si.anchor[1]
                dist = math.hypot(dx,dy)+0.1
                if cross > 0:
                    f = k_spring*cross*dist
                    fx,fy = f*dx/dist, f*dy/dist
                    forces[i][0]+=fx; forces[i][1]+=fy
                    forces[j][0]-=fx; forces[j][1]-=fy
                if dist < 22.0:
                    f = k_repel/(dist**2)
                    forces[i][0]-=f*dx/dist; forces[i][1]-=f*dy/dist
                    forces[j][0]+=f*dx/dist; forces[j][1]+=f*dy/dist

        # Zone gravity
        for i,ss in enumerate(subsystems):
            za = ZONE_ANCHORS.get(ss.name,[50,40])
            forces[i][0] += k_zone*(za[0]-ss.anchor[0])
            forces[i][1] += k_zone*(za[1]-ss.anchor[1])

        for i,ss in enumerate(subsystems):
            mag = math.hypot(*forces[i])+0.01
            s = min(step_now,mag)/mag
            ss.anchor[0] = max(8, min(92, ss.anchor[0]+forces[i][0]*s))
            ss.anchor[1] = max(8, min(72, ss.anchor[1]+forces[i][1]*s))

# ── IC placement within sub-system ───────────────────────────────────────────

def place_ics_in_subsystem(ss:Subsystem, fps_by_ref:dict,
                            adj:dict[str,dict[str,Edge]]) -> None:
    hub_fp = fps_by_ref[ss.hub]
    hub_fp["x_mm"] = snap(ss.anchor[0])
    hub_fp["y_mm"] = snap(ss.anchor[1])
    clamp_to_board(hub_fp)

    placed = {ss.hub}
    queue  = [ss.hub]

    while queue:
        cur = queue.pop(0); cur_fp = fps_by_ref[cur]
        neighbours = sorted(
            [(r, adj[cur][r]) for r in ss.members if r not in placed and r in adj[cur]],
            key=lambda x: -x[1].weight
        )
        for next_ref, edge in neighbours:
            nxt = fps_by_ref[next_ref]
            shared = set(edge.shared_nets)

            # Direction: cur_fp centre toward centroid of cur_fp's shared-net pads
            sp = [p for p in cur_fp["pads"] if p["net"] in shared]
            if sp:
                cx = sum(pad_board_pos(cur_fp,p)[0] for p in sp)/len(sp)
                cy = sum(pad_board_pos(cur_fp,p)[1] for p in sp)/len(sp)
                dx,dy = cx-cur_fp["x_mm"], cy-cur_fp["y_mm"]
            else:
                # No pad info: spread outward at 45° increments
                angle = (len(placed)-1)*math.pi/3
                dx,dy = math.cos(angle), math.sin(angle)

            mag = math.hypot(dx,dy)+0.01
            nx,ny = dx/mag, dy/mag
            dist = body_half(cur_fp) + 2.5 + body_half(nxt)
            nxt["x_mm"] = snap(cur_fp["x_mm"] + nx*dist)
            nxt["y_mm"] = snap(cur_fp["y_mm"] + ny*dist)
            clamp_to_board(nxt)
            placed.add(next_ref); queue.append(next_ref)

    # Place any cluster members the BFS did not reach (no netlist connections).
    # These get a ring position around the hub at the standard body-clearance distance.
    unplaced = [r for r in ss.members if r not in placed]
    hub_fp   = fps_by_ref[ss.hub]
    for i, ref in enumerate(unplaced):
        fp = fps_by_ref[ref]
        # Spread at 45° increments starting at -30° (roughly left/right of hub)
        angle = -math.pi/6 + i * math.pi / 3
        d = body_half(hub_fp) + 2.5 + body_half(fp)
        fp["x_mm"] = snap(hub_fp["x_mm"] + math.cos(angle)*d)
        fp["y_mm"] = snap(hub_fp["y_mm"] + math.sin(angle)*d)
        clamp_to_board(fp)

    # Hard push-apart for within-cluster IC overlaps
    members_fps = [fps_by_ref[r] for r in ss.members]
    for _ in range(20):
        moved = False
        for i in range(len(members_fps)):
            for j in range(i+1,len(members_fps)):
                a,b = members_fps[i],members_fps[j]
                dx=b["x_mm"]-a["x_mm"]; dy=b["y_mm"]-a["y_mm"]
                d = math.hypot(dx,dy)+0.01
                min_d = body_half(a)+body_half(b)+1.8
                if d < min_d:
                    push=(min_d-d)/2; nx,ny=dx/d,dy/d
                    b["x_mm"]=snap(b["x_mm"]+push*nx); clamp_to_board(b)
                    a["x_mm"]=snap(a["x_mm"]-push*nx); clamp_to_board(a)
                    b["y_mm"]=snap(b["y_mm"]+push*ny); clamp_to_board(b)
                    a["y_mm"]=snap(a["y_mm"]-push*ny); clamp_to_board(a)
                    moved = True
        if not moved: break

# ── Pin-level passive placement ───────────────────────────────────────────────

def place_passives_at_pins(fps:list[dict], fps_by_ref:dict,
                            ic_fps:list[dict], net_lut:dict[int,str]) -> dict[str,int]:
    gnd_ids = {nid for nid,name in net_lut.items() if name.lower() in GND_NAMES}

    # Group passives: (parent_ic_ref, power_net_id) → [passive_fps]
    groups: dict[tuple,list[dict]] = defaultdict(list)
    unparented: list[dict] = []

    for fp in fps:
        if fp["ref"][0] in ("U","Q","J"): continue
        power_net = next(
            (p["net"] for p in fp["pads"]
             if p["net"]>=0 and p["net"] not in gnd_ids
             and is_power_net(net_lut.get(p["net"],""))),
            None
        )
        if power_net is None:
            unparented.append(fp); continue

        best_ic, best_dist = None, float("inf")
        for ic in ic_fps:
            if any(p["net"]==power_net for p in ic["pads"]):
                d = math.hypot(ic["x_mm"]-fp["x_mm"], ic["y_mm"]-fp["y_mm"])
                if d < best_dist: best_dist,best_ic = d,ic
        if best_ic:
            groups[(best_ic["ref"], power_net)].append(fp)
        else:
            unparented.append(fp)

    parent_counts: dict[str,int] = defaultdict(int)

    for (ic_ref, net_id), passive_list in groups.items():
        ic = fps_by_ref[ic_ref]
        ic_pads = [p for p in ic["pads"] if p["net"]==net_id]
        if not ic_pads: continue

        pad_bx = [pad_board_pos(ic,p)[0] for p in ic_pads]
        pad_by = [pad_board_pos(ic,p)[1] for p in ic_pads]
        cx = sum(pad_bx)/len(pad_bx)
        cy = sum(pad_by)/len(pad_by)

        # Direction from IC centre → pad cluster centroid
        dx=cx-ic["x_mm"]; dy=cy-ic["y_mm"]
        mag=math.hypot(dx,dy)+0.01; nx,ny=dx/mag,dy/mag
        px,py = -ny,nx  # perpendicular for fan-out

        # Place passives starting at pad centroid + 1.5mm outward
        n = len(passive_list)
        for i, pfp in enumerate(passive_list):
            offset = (i-(n-1)/2)*1.5
            pfp["x_mm"] = snap(max(1,min(99, cx+nx*1.5+px*offset)))
            pfp["y_mm"] = snap(max(1,min(79, cy+ny*1.5+py*offset)))
            pfp["rot_deg"] = 0.0
        parent_counts[ic_ref] += n

    for i,fp in enumerate(unparented):
        fp["x_mm"] = snap(3.0+(i%8)*1.5); fp["y_mm"] = snap(3.0+(i//8)*1.5)

    return dict(parent_counts)

# ── Connector edge placement ──────────────────────────────────────────────────

def place_connectors(fps:list[dict]) -> None:
    for fp in fps:
        if not fp["ref"].startswith("J"): continue
        lib = fp.get("lib","").lower()
        if any(k in lib for k in ("2pin","screw","pwr","power","th_2pin")):
            fp["x_mm"]=3.0; fp["y_mm"]=snap(68.0); fp["rot_deg"]=0.0
            print(f"  {fp['ref']:4s}: power → left edge  (3.0, 68.0)")
        elif any(k in lib for k in ("6pin","jtag","swd","debug","th_6pin")):
            fp["x_mm"]=snap(72.0); fp["y_mm"]=77.0; fp["rot_deg"]=0.0
            print(f"  {fp['ref']:4s}: JTAG  → bottom edge (72.0, 77.0)")
        else:
            fp["x_mm"]=snap(97.0); fp["rot_deg"]=0.0
            print(f"  {fp['ref']:4s}: misc  → right edge  ({fp['x_mm']:.1f}, {fp['y_mm']:.1f})")

# ── Net class upgrade ─────────────────────────────────────────────────────────

NET_WIDTHS = [
    ("vm",2.0,0.30), ("in_12",1.5,0.25), ("in",1.5,0.25),
    ("vdd_core",0.5,0.15), ("vddq",0.4,0.12), ("3v3",0.4,0.12),
    ("v1p8",0.3,0.10), ("vcc",0.3,0.10), ("vdd",0.3,0.10),
    ("default",0.15,0.10),
]

def upgrade_net_classes(net_classes:list[dict]) -> None:
    for nc in net_classes:
        n = nc["name"].lower()
        for prefix,width,cl in NET_WIDTHS:
            if n.startswith(prefix) or n==prefix:
                nc["trace_width_mm"]=width; nc["clearance_mm"]=cl; break
        if nc.get("zdiff_ohm",0)==100 and nc.get("diff_pair_gap_mm",0)==0:
            nc["diff_pair_gap_mm"]=0.10

# ── Main ─────────────────────────────────────────────────────────────────────

def main(project_path:str) -> None:
    print(f"[netlist-placer] Loading {project_path}")
    with open(project_path) as f:
        data = json.load(f)

    fps        = data["footprints"]
    net_table  = data["net_table"]
    net_lut    = {n["id"]:n["name"] for n in net_table}
    fps_by_ref = {fp["ref"]:fp for fp in fps}
    ic_fps     = [fp for fp in fps if fp["ref"][0] in ("U","Q")]

    before = count_overlaps(fps)
    print(f"[netlist-placer] {len(fps)} components, "
          f"{len(data['traces'])} traces, {before} overlapping pairs\n")

    # ── Phase 1: Build IC graph ───────────────────────────────────────────────
    print("━━━━ Phase 1: Component connectivity graph ━━━━")
    adj, net_pads = build_ic_graph(fps, net_lut)

    strong = sum(1 for a in adj for b,e in adj[a].items() if a<b and e.weight>=5)
    print(f"  IC-to-IC edges: {sum(1 for a in adj for b in adj[a] if a<b)} total, "
          f"{strong} strong (signal or exclusive power)")

    # Show strongest connections
    top = sorted(((a,b,e.weight,e.shared_nets)
                  for a in adj for b,e in adj[a].items() if a<b),
                 key=lambda x:-x[2])[:8]
    for a,b,w,nets in top:
        nms = [net_lut.get(n,"?") for n in nets[:3]]
        print(f"    {a:5s} ↔ {b:5s}  weight={w:3d}  [{', '.join(nms)}]")
    print()

    # ── Phase 2: Detect sub-systems ──────────────────────────────────────────
    print("━━━━ Phase 2: Sub-system detection ━━━━")
    raw_clusters = cluster_subsystems(ic_fps, adj, net_lut)
    subsystems: list[Subsystem] = []
    for idx, (cluster_key, members) in enumerate(raw_clusters.items()):
        hub = max(members, key=lambda r: sum(adj[r][n].weight for n in adj[r]))
        name = name_cluster(cluster_key, members, fps_by_ref)
        subsystems.append(Subsystem(idx=idx, members=list(members),
                                    hub=hub, anchor=[50.0,40.0], name=name))
    print(f"  Detected {len(subsystems)} sub-systems:")
    for ss in subsystems:
        print(f"    {ss.idx}: {ss.name:20s}  ICs: {sorted(ss.members)}")

    print_topology(subsystems, fps_by_ref, adj, net_lut)

    # ── Phase 3: Floor-plan sub-systems ──────────────────────────────────────
    print("━━━━ Phase 3: Sub-system floor planning ━━━━")
    assign_anchors(subsystems, adj)
    for ss in subsystems:
        print(f"  {ss.name:22s}  anchor ({ss.anchor[0]:.1f}, {ss.anchor[1]:.1f})")
    with open(project_path,"w") as f:
        json.dump(data,f,separators=(",",":"))
    time.sleep(0.4)

    # ── Phase 4: Pad-directed IC placement ────────────────────────────────────
    print("\n━━━━ Phase 4: IC placement — pad-directed ━━━━")
    for ss in subsystems:
        place_ics_in_subsystem(ss, fps_by_ref, adj)
    for ic in ic_fps:
        print(f"  {ic['ref']:5s} [{detect_role(ic, net_lut):12s}]  "
              f"({ic['x_mm']:5.1f}, {ic['y_mm']:5.1f})")
    with open(project_path,"w") as f:
        json.dump(data,f,separators=(",",":"))
    time.sleep(0.4)

    # ── Phase 5: Passive pin-level placement ──────────────────────────────────
    print("\n━━━━ Phase 5: Passives at IC power pad coordinates ━━━━")
    counts = place_passives_at_pins(fps, fps_by_ref, ic_fps, net_lut)
    for ic_ref,cnt in sorted(counts.items(),key=lambda x:-x[1]):
        print(f"  {ic_ref}: {cnt} passives placed at their power pin locations")
    with open(project_path,"w") as f:
        json.dump(data,f,separators=(",",":"))
    time.sleep(0.4)

    # ── Phase 6: Connectors ───────────────────────────────────────────────────
    print("\n━━━━ Phase 6: Connectors → board edges ━━━━")
    place_connectors(fps)

    # ── Phase 7: Grid snap + net class upgrade ────────────────────────────────
    print("\n━━━━ Phase 7: Grid snap + net class upgrade ━━━━")
    for fp in fps:
        fp["x_mm"]=snap(fp["x_mm"]); fp["y_mm"]=snap(fp["y_mm"])
        if fp["ref"][0] not in ("U","Q","J"): fp["rot_deg"]=0.0
    upgrade_net_classes(data.get("net_classes",[]))

    # ── Phase 8: Clear routes ─────────────────────────────────────────────────
    print("\n━━━━ Phase 8: Clear routes ━━━━")
    nt,nv = len(data["traces"]),len(data["vias"])
    data["traces"]=[]; data["vias"]=[]
    print(f"  Removed {nt} traces and {nv} vias")
    print("  → Run Auto Route to re-route the netlist-optimised placement")

    with open(project_path,"w") as f:
        json.dump(data,f,separators=(",",":"))

    # ── Report ────────────────────────────────────────────────────────────────
    after = count_overlaps(fps)

    def dist(a_ref,b_ref):
        a=fps_by_ref.get(a_ref); b=fps_by_ref.get(b_ref)
        return math.hypot(a["x_mm"]-b["x_mm"],a["y_mm"]-b["y_mm"]) if a and b else None

    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Netlist-driven placement complete")
    print(f"  Overlapping pairs: {before} → {after}")
    for a_ref,b_ref,label,limit in [
        ("U1","U5","SoC→DDR4",16),
        ("U8","Q1","GateDrv→PMOS",10),
        ("U8","Q2","GateDrv→NMOS",10),
        ("U1","U8","SoC→GateDrv",30),
    ]:
        d=dist(a_ref,b_ref)
        if d: print(f"  {label:20s}: {d:.1f}mm  {'✓' if d<=limit else 'needs /route'}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

if __name__=="__main__":
    if len(sys.argv)<2: print("usage: netlist_placer_agent.py <project.dsproj>"); sys.exit(1)
    if not os.path.exists(sys.argv[1]): print(f"error: not found: {sys.argv[1]}"); sys.exit(1)
    main(sys.argv[1])

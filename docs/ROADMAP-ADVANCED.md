# Design Studio — Advanced Capability Roadmap

Goal: grow from "complex embedded" (4–8 layer, mid-size BGA, DDR3/4) toward
server/motherboard-class design. Each gap below is broken into the systems
that must exist, what each one is, how it maps onto the current codebase
(`BoardDocument`, the C++ A* core, `Stackup.cs`, `HighSpeed.cs`), and a build
order where every step is shippable on its own.

Effort scale used below: S = days, M = weeks, L = months, XL = multi-quarter.

---

## 1. Routing scale

Today: single-connection A* on a 0.1 mm grid (`dc_route_run`), greedy
nearest-neighbour net chaining, rip-up-and-reroute only on component move.

### 1.1 Spatial index (S) — prerequisite for everything
A quadtree or R-tree over all copper (traces, pads, vias, pours) in the C++
core. Every later feature (shove, batch routing, online DRC) needs "what is
near this point" in O(log n) instead of the current linear scans.
*Where:* new `core/spatial.cpp`; `dc_board_*` mutators keep it updated.

### 1.2 Push-and-shove interactive routing (L) — biggest UX multiplier
When the user drags a trace through occupied space, existing traces bend out
of the way (springback when room reappears), vias hop aside, and the head
never enters an illegal state.
- Algorithm: maintain the routed head as a "walkaround hull"; on collision,
  compute the minimal displacement of the blocking segment chain (a shortest
  path in the obstacle's freedom region), apply recursively with a depth
  limit. KiCad's `PNS::` router is the reference open implementation to study.
- *Where:* C++ (`core/shove.cpp`), driven per-mouse-move from `PcbCanvas`
  through a new `dc_shove_*` API. The managed side only draws.

### 1.3 Multi-net batch autorouter with rip-up-and-reroute (XL)
Replace "route nets one at a time, first-come-first-served" with negotiated
congestion routing (PathFinder algorithm, used by FPGA routers):
1. Route every connection independently, allowing overlaps.
2. Price each grid cell by overuse (how many nets want it).
3. Iterate: rip up overused nets, re-route with cost = base + history +
   congestion price. Converges to a legal solution that global greedy never finds.
- Needs: connection list from the ratsnest (exists), layer-aware cost model
  (exists in A*), the spatial index (1.1), and a coarse global-routing grid
  (2–5 mm cells) to plan layer assignment before detailed routing.
- *Where:* `core/batchroute.cpp`; managed `BoardDocument.RouteAll()` replaces
  the greedy loop in `RerouteFootprintNets`.

### 1.4 BGA escape planner (M–L)
For a 1718-ball socket nothing works unless escape is planned as a unit:
- Ring assignment: which ball depth escapes on which layer (the
  `StackupPlanner` ring math already computes capacity — reuse it).
- Channel allocation: between-ball routing channels per layer = (pitch −
  land) / (width + clearance) tracks; assign nets to channels by sorting
  destinations radially (a bipartite matching / left-edge algorithm).
- Via fanout pattern generation: dog-bone or via-in-pad per ring, emitted as
  ordinary traces+vias so the rest of the tool sees normal copper.
- *Where:* managed `Model/EscapePlanner.cs` — it's geometry generation, not
  search; C# is fine.

### 1.5 Length-constrained routing (M)
Feed match-group targets (exists) into the router cost so it prefers paths
near the target length instead of always-shortest, then let `SerpentineTuner`
close the residual. Add a detour-cost term: penalty ∝ |projected − target|.

Build order: 1.1 → 1.2 → 1.4 → 1.3 → 1.5.

---

## 2. Signal integrity at 16–50 GT/s

Today: IPC-2141 closed forms (good ≤ ~3 GHz), first-order `SiEstimator`.

### 2.1 2D field solver for trace cross-sections (M)
Replace closed forms with a boundary-element or finite-difference solver of
Laplace's equation over the layer cross-section → exact L, C, R, G matrices
per unit length for arbitrary geometry (asymmetric stripline, broadside
pairs, soldermask effects). 2D is tractable: ~1k unknowns, milliseconds.
- Output: RLGC(f) matrices → exact Z0, Zdiff, odd/even modes, per-mm delay.
- *Where:* `core/fieldsolver2d.cpp`; `ImpedanceEngine` keeps its API and
  becomes a thin wrapper choosing closed-form (fast preview) vs solver (exact).

### 2.2 Via 3D model — equivalent circuit, not full-wave (M)
Full 3D FEM is out of reach, but the industry-standard intermediate works to
~20 GHz: model each via as a π-network (barrel L, pad C, antipad C, stub as
an open transmission line) using analytic formulas calibrated against
published data. Stub resonance f = c/(4·stub·√Er) lands within ~10 %.
- *Where:* extend `DelayEngine.ViaDelayPs` into `Model/ViaModel.cs` returning
  an S-parameter block per via.

### 2.3 Channel S-parameter assembly + export (M)
Cascade per-segment RLGC lines and via blocks into a channel 2-port (ABCD
matrix multiplication, then convert to S). Export Touchstone `.s4p` so
professionals can take the channel into ADS/HyperLynx — this single feature
buys credibility cheaply.
- *Where:* `Model/ChannelExtractor.cs`; menu item on a net → writes `.s4p`.

### 2.4 IBIS-AMI / equalized link simulation (XL — integrate, don't build)
Statistical eye analysis with CTLE/DFE/FFE is a vendor-model ecosystem, not
an algorithm. The right move: export S-parameters (2.3) plus a netlist, and
document a flow into open tools (`scikit-rf`, `SignalIntegrity`, vendor
sims). Building an AMI executor in-house is not rational at this scale.

### 2.5 Crosstalk engine (M)
With RLGC coupling matrices from 2.1, compute NEXT/FEXT between parallel
segments (coupled-line closed forms per segment pair, summed along shared
length). Add DRC rule 18: "aggressor runs > X mm parallel at < Y gap with
victim of class Z". The spatial index (1.1) finds the pairs.

Build order: 2.1 → 2.3 → 2.2 → 2.5 → (2.4 = export path only).

---

## 3. Power delivery network analysis

Today: pours are hatch strokes; nothing is simulated.

### 3.0 Prerequisite — real polygon planes (M)
Replace stroke-based pours with polygon plane shapes (with thermal-relief and
clearance voids) as first-class objects. Needed by everything below and by
gap 6's region rules. Use a polygon clipping library (Clipper2, C++).
- *Where:* `core/poly.cpp`, new `PlaneShape` item in `BoardDocument`,
  renderer + Gerber export updates.

### 3.1 DC IR-drop solver (M)
Mesh each plane polygon into triangles or a regular grid, solve the
resistive network (sheet resistance ρ/t per square, node equation **G**·v = i)
with current sources at sink pads and a voltage source at the VRM. Sparse
Cholesky on ~10–100k nodes is sub-second.
- Output: per-node voltage → heat-map overlay on the canvas; DRC rule 19:
  "net VDD_CORE drops 62 mV to U1 (budget 30 mV)".
- *Where:* `core/irdrop.cpp` + a canvas overlay mode.

### 3.2 PDN AC impedance (M)
Z(f) seen from the die: plane pair = parallel-plate C + spreading L; each
decap = R-L-C series branch (parasitics from the part library); VRM = R + L
behavioral. Sum admittances over 100 kHz–1 GHz, compare against target
impedance Z_target = V·ripple% / I_transient.
- Output: Bode plot dialog + "anti-resonance at 80 MHz exceeds target" DRC.
- Needs: capacitor parasitics in the part schema (`electrical.parameters`
  already carries arbitrary rows — add `esl_nh`, `esr_mohm` conventions).

### 3.3 Decap placement advisor (S–M, after 3.2)
Greedy loop: while Z(f) > target at some f, propose the standard value whose
resonance hits f, placed at the highest-current via cluster; re-evaluate.
This is exactly the loop a power engineer runs by hand.

Build order: 3.0 → 3.1 → 3.2 → 3.3.

---

## 4. Advanced fabrication features

Today: through/blind via spans, single global laminate, RS-274X + Excellon.

### 4.1 Via types & HDI rules (M)
Extend `ViaItem` with `Type` (through, blind, buried, microvia, stacked,
staggered, via-in-pad w/ fill spec). DRC: microvia aspect ratio ≤ 1:1, only
adjacent-layer spans, stacking only over filled vias, etc. `StackupPlanner`
learns HDI build-ups (1+N+1, 2+N+2) and sequential-lamination legality.

### 4.2 Backdrill specification (S–M)
Rule 15 already detects stubs. Add `BackdrillTo` on `ViaItem`, generate the
extra Excellon file pair per backdrill span + the fab-drawing note in
`FabExporter`. This closes a loop that already half-exists.

### 4.3 Laminate library with Dk/Df(f) (S–M)
`Materials.json`: named laminates (FR-4 grades, Megtron 6/7, Tachyon 100G,
Rogers) with Dk/Df at standard frequencies (1/2/5/10/25 GHz) +
interpolation. `StackupLayer` references a material id instead of raw
numbers; the field solver (2.1) consumes the frequency-dependent values.

### 4.4 Impedance coupons + stackup drawing (S)
Generate a coupon strip (serpentine of each controlled class) outside the
board outline in the Gerber set, plus a stackup table drawing — fabs require
both for impedance-controlled orders.

Build order: 4.3 → 4.2 → 4.1 → 4.4.

---

## 5. Thermal & electro-mechanical co-design

Today: 3D view is visual only.

### 5.1 Steady-state thermal solver (M–L)
Reuse the IR-drop mesh (3.1): copper density per cell → in-plane conductivity
map; components are heat sources (θja, P from the datasheet JSON — already
imported!); solve the 2.5D heat equation (per-layer conduction + inter-layer
vias + convection boundary). Output: temperature heat-map; DRC rule 20:
"U3 junction estimate 142 °C > Tjmax 125 °C".

### 5.2 Copper-aware current capacity (S)
IPC-2152 trace ampacity check: given net current (from power_domains in the
datasheet payload), flag undersized traces/vias. Mostly arithmetic; high value.

### 5.3 Mechanical export/import (M)
STEP export of board + 3D bodies (board outline extrusion + package boxes
exists in the 3D builder); IDF/STEP import of enclosure keep-outs as
placement/height constraint regions. Warpage simulation: out of scope —
document a flow to external FEA instead.

Build order: 5.2 → 5.1 → 5.3.

---

## 6. Library & constraint scale

Today: board-only editing, flat per-net classes, JSON footprint library.

### 6.1 Schematic capture (XL — the largest single missing system)
A second editor: symbols, hierarchical sheets, buses, ERC at the schematic
level, then forward/back annotation to the board (netlist diff engine).
The datasheet JSON already carries symbols (pins/types) — that becomes the
symbol library seed. This is its own project; plan it as one.

### 6.2 Constraint hierarchy (M)
Replace flat `NetClassInfo` with a rule tree: design defaults → class →
class-pair (clearance between classes) → region (rule areas as polygons,
needs 3.0) → layer-set overrides → per-net. Resolution = walk up the tree.
DRC and the routers query through one `ResolveRule(net, layer, region)` API.

### 6.3 Library scale-up (M)
Move the footprint library from one-JSON-per-file to SQLite with FTS search,
parameter columns, and bulk import (KiCad `.kicad_mod` converter buys
thousands of proven footprints instantly).

### 6.4 Design reuse blocks (M)
Save a placed+routed cluster (VRM phase, USB port circuit) as a re-usable
block with internal nets; stamp N copies. Motherboards are 80 % repeated
blocks — this multiplies productivity more than any router improvement.

Build order: 6.2 → 6.3 → 6.4 → 6.1.

---

## Suggested global sequence (each step shippable)

| Phase | Items | Outcome |
|-------|-------|---------|
| 1 | 1.1 spatial index, 3.0 polygon planes, 4.3 materials | Foundations every other item needs |
| 2 | 5.2 ampacity, 4.2 backdrill, 4.4 coupons, 6.2 rule tree | Cheap, high-credibility wins |
| 3 | 3.1 IR-drop, 2.1 2D field solver, 2.3 S-param export | "Simulation-grade" claims become true |
| 4 | 1.2 push-and-shove, 1.4 escape planner | Routing UX reaches modern-tool feel |
| 5 | 3.2 PDN-AC, 2.5 crosstalk, 5.1 thermal | Full analysis suite |
| 6 | 1.3 batch autorouter, 6.1 schematic capture | Motherboard-class workflow |

Phases 1–3 ≈ one engineer-quarter each and lift the tool to "DDR4 SoM /
Raspberry-Pi-class with real analysis". Phases 4–6 are the multi-quarter
investments that close on motherboard territory. GPU-accelerator boards
(H100-class) additionally require the 2.4 external-simulation ecosystem and
HDI fab partnerships — keep those as integration points, not build targets.

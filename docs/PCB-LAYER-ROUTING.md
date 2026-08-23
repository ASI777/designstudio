# PCB layer, via, and copper-zone policy

DesignStudio persists routing intent rather than inferring it from layer numbers.
`layer_policies` records each copper layer's signal/plane/mixed role, preferred
direction, whether ordinary tracks are permitted, copper thickness, and source
revision. Net classes may restrict `allowed_layers`, `allowed_via_types`, and
`max_via_count`.

The router rejects prohibited start/goal layers, does not expand onto prohibited
layers, filters via technologies, respects the available via budget, and adds a
cost when a move opposes a layer's preferred direction. A preferred direction is
an optimization policy, not a fabrication error; explicit layer and via
prohibitions are blocking DRC errors.

`copper_zones` are named, net-assigned polygons with a layer, clearance, minimum
island area, connection requirement, and provenance. Zone regeneration clips
copper to both the zone and board topology. Native DRC independently checks net
and layer validity, boundary containment, minimum polygon area, and same-net
connection. Generated fill strokes remain ordinary copper for clearance and
connectivity analysis.

On multilayer boards, footprint `side` remains an assembly-side flag. Top SMD
pads map to layer 0 and bottom SMD pads map to the last copper layer; through-hole
pads span the full stack.

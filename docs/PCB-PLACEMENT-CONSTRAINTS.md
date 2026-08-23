# Structured PCB placement constraints

Placement is a constrained engineering operation, not a cosmetic packing pass.
Each footprint may carry a `placement` object conforming to
`urn:design-studio:schema:pcb-placement:1`:

- `locked` preserves position and rotation and fails placement if illegal;
- `functional_group` overrides inferred net-graph communities;
- `edge_anchor` places external interfaces on the requested board edge;
- `thermal_power_w` and `thermal_clearance_mm` reserve a thermal halo;
- `test_access_required` and `test_access_halo_mm` reserve probe/tool access.

A rule area can prohibit placement and can declare `max_height_mm`. Component
height comes from `h3d_mm`. Exact `courtyard_pts` are authoritative; if missing,
the legalizer derives a conservative polygon from body and pad geometry.

The subsystem placer first proposes a connectivity-optimized layout, then runs
the same deterministic constraint policy used by tests. It evaluates rotated
courtyard polygons against the real board polygon, cutouts, placement/height
areas, components on the same board side, thermal spacing and test access.
Locked and edge-anchored parts are placed first. A fixed outline or any explicit
placement constraint prevents automatic board resizing.

If no legal location exists, the agent exits nonzero and does not rewrite the
project. Native DRC independently rechecks no-placement areas, height limits,
thermal spacing and test-access spacing before release.

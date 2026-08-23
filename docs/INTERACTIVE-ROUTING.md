# Transactional interactive routing

`dc::InteractiveRouterSession` is the authoritative cursor-routing engine.
The Qt canvas is a consumer: it rebuilds the native board once at route start,
then renders the engine's preview records without changing `ProjectModel`.

## Transaction invariants

- `begin` snapshots the authoritative board and validates the net, layer,
  dimensions, and start position.
- Every cursor update starts from the last accepted waypoint snapshot. A failed
  shove therefore rolls back completely; moving away restores exact pre-shove
  geometry (springback).
- Preview additions receive deterministic IDs from the private board copy.
  Existing traces and vias are moved in place, so their IDs never change.
- The shove solver recursively translates movable trace/via chains with a
  bounded depth. Each alternative is attempted on a board copy. Fixed pads,
  custom copper, and pour geometry trigger a walkaround search; if no legal
  detour exists, the preview contains an explicit failure.
- Online DRC runs before a cursor position is accepted. Findings involving
  new or moved items are returned with the preview.
- `commit` is the only operation that replaces the caller's board. `cancel`
  discards the private copies.

The additive C ABI is prefixed `dc_interactive_route_`. Its request carries an
ABI version and struct size, and preview arrays use count-then-fetch calls so
callers cannot silently truncate results. Existing `dc_route_*` callers remain
unchanged.

The Qt PCB route tool starts only on assigned copper, derives width/clearance
and via sizes from the selected net class, previews displaced geometry, stages
a layer change with `V`, commits on click, and cancels on Escape/right-click.

## Milestone boundary

This milestone covers transactional walkaround, bounded trace/via shove,
springback, layer-change vias, deterministic replay, online DRC, and Qt/C API
integration. Differential-pair heads, interactive meander tuning, arbitrary
corner-chain reshaping, and persistent multi-click undo checkpoints remain
separate follow-on routing work; they must not be inferred from this API.

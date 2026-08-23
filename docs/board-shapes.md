# Custom board shapes

The board was historically a `width × height` rectangle. `BoardOutline` makes the
outline a polygon (mm, counter-clockwise, implicitly closed) so the board can be a
rectangle, a square, a circle, a rounded rectangle, or any free-form polygon that
hugs a mechanical enclosure.

## Model

`BoardOutline` provides the shape builders (`Rectangle`, `RoundedRectangle`,
`Circle`, plus arbitrary polygons), `BoundingBox`, and an even-odd `Contains`
point-in-polygon test. `BoardDocument` carries:

- `OutlinePolygon` — the custom outline, or `null` for a plain rectangle.
- `Shape` — the shape kind (for the UI and project round-trip).
- `EffectiveOutline()` — the polygon to use everywhere: the custom one if set,
  otherwise a rectangle synthesised from `BoardWidthMm × BoardHeightMm`.
- `SetBoardOutline(shape, polygon)` / `SetBoardShape(shape, primary, secondary, radius)`
  — set the outline; `BoardWidthMm`/`BoardHeightMm` follow its bounding box, so the
  router keep-out and the canvas view scaling stay correct.

## Where it flows

- **Planes** — the Clipper2 plane generator fills `EffectiveOutline()` when a plane
  has no explicit boundary, so copper pours follow the real board shape.
- **Fab** — the Gerber edge-cuts layer traces the outline polygon (was a hard-coded
  rectangle).
- **Canvas** — `PcbCanvas` draws the outline as a filled polygon.
- **Project files** — `ProjectIO` round-trips `Shape` + `OutlinePolygon`.
- **Sanity check** — `BoardOutline.OutsideOutline(doc)` flags any footprint or via
  whose centre falls outside a non-rectangular outline (a board-edge check).

## UI

Board Setup has a **Shape** picker (rectangle / square / circle / rounded
rectangle). For a circle the Width field is the diameter; rounded rectangles use
the Corner radius field. The same dialog now takes any copper-layer count from 2 to
64 (the engine's full range), not just the old ≤16 presets.

## Scope and next

The router's native keep-out remains the bounding rectangle; the true edge is
enforced by the outline DRC and honoured by the planes and fab output. Cut-outs
(holes/slots in the board) and per-edge clearance zones are the natural next
additions — both are polygon boolean operations the Clipper2 layer already
supports.

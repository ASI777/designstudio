# GLB enclosure concept workflow

DesignStudio has two deliberately separate CAD evidence routes:

- Electronic components use dimensioned engineering drawings and PDF view evidence. Those models must match supplier geometry and PCB interfaces.
- User-created enclosure concepts use a self-contained GLB directly. A PDF conversion adds no authority because the GLB is already the source geometry.

The **Enclosure** tab validates GLB 2.0 container structure, rejects external buffers, unsupported accessors, non-triangle geometry, invalid indices and excessive geometry, then flattens the default scene into millimetres. The source file SHA-256 remains attached to every exported evidence record.

## Current capability

1. Load an uncompressed, self-contained GLB 2.0 concept.
2. Inspect front, right, top and isometric wireframe projections.
3. Confirm the overall X dimension instead of trusting an image-generation scale.
4. Mark datums, fixed supports, applied loads, split lines, preserved surfaces, openings, PCB mounts and connector supports on exact mesh vertices.
5. Record force magnitude/direction, nominal wall thickness, manufacturing intent, reinforcement strategy and target safety factor.
6. Export `mesh-evidence/1` JSON conforming to `docs/schemas/mesh-evidence-v1.schema.json`.

The evidence file intentionally records a **concept**, not a STEP model or a manufacturing approval. Absolute source paths are excluded; the portable identity is the source basename, byte count and SHA-256.

## Required next stage

The next work package consumes the mesh evidence and creates a manufacturable boundary representation: surface segmentation and repair, watertight shell creation, draft/bend/machining constraints, split strategy, PCB and connector interfaces, and parameterized internal ribs/bosses/frame members. Structural output is not accepted until load cases and supports are reviewed, FEA is run with a selected material, and manufacturing rules are checked. STEP is the interchange output; the editable parametric source remains the design authority.

GLB mesh triangles are visual intent. They are never silently presented as Class-B manufacturing CAD.

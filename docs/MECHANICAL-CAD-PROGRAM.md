# Typed mechanical CAD programs

DesignStudio now exposes a small, trusted FreeCAD command layer modelled on
the semantic feature concepts found in commercial parametric CAD systems.
The model does not emit arbitrary Python or GUI clicks. It emits a
`design-studio.mechanical-cad-program/1` JSON document, and the FreeCAD host
turns each command into an editable feature-tree node. Sketch commands use
native `Sketcher::SketchObject` geometry and constraints; solid results use
native `PartDesign::FeaturePython` nodes with deterministic recompute proxies.

## Supported commands

| Command | Purpose |
| --- | --- |
| `part.box` | Create a dimensioned rectangular solid |
| `part.cylinder` | Create a dimensioned cylindrical solid |
| `part.sphere` | Create a dimensioned spherical joint or cap solid |
| `part.ellipsoid` | Create a dimensioned axis-oriented ellipsoidal joint collar |
| `part.cone` | Create a dimensioned tapered conical solid between two radii |
| `sketch.create` | Create an empty native Sketcher sketch on XY, XZ or YZ |
| `sketch.line` | Create a measured profile segment |
| `sketch.circle` | Create a measured circular profile |
| `sketch.rectangle` | Create a closed planar profile |
| `sketch.constraint.add` | Add a typed geometric or dimensional Sketcher constraint |
| `feature.extrude` | Turn a closed profile into an extruded solid |
| `feature.revolve` | Turn a closed profile around a measured axis |
| `feature.cut` | Boolean subtract a tool from a base |
| `feature.fuse` | Boolean union two solids |
| `feature.hole` | Cut a cylindrical hole at a measured location |
| `feature.fillet` | Apply a radius to selected or all source edges |
| `feature.chamfer` | Apply an edge break to selected or all source edges |
| `pattern.linear` | Create repeated linear instances |
| `pattern.circular` | Create repeated angular instances |
| `assembly.create` | Create a native `App::Part` assembly container |
| `assembly.component` | Add an `App::Link` component instance with a placement |
| `assembly.mate` | Apply origin- or topology-referenced fixed/coincident/concentric/distance/face/edge mates |
| `assembly.check_clearance` | Measure B-Rep distance and require a minimum gap |
| `assembly.check_interference` | Measure B-Rep common volume and reject overlap |
| `tolerance.stack.create` | Create a tolerance stack associated with an assembly |
| `tolerance.stack.item` | Add a signed nominal dimension and bilateral plus/minus tolerance |
| `tolerance.stack.check` | Verify worst-case or RSS stack bounds against engineering limits |

Every command has a stable ID, ordered dependencies, explicit millimetre
values, and at least one provenance string. The host rejects forward
references, unknown operations, non-finite values, non-positive dimensions,
and programs larger than the registered command limit before FreeCAD is
mutated.

Assembly mates can align component origins or stable FreeCAD topology
references such as `Face6` and `Edge1`. Face coincidence/distance aligns face
normals and datum centers; edge alignment aligns edge tangents and centers;
concentric mates use analytic circular/cylindrical axes. A stale or invalid
topology reference fails closed instead of silently selecting a new face after
the model changes. Clearance uses `Shape.distToShape`; interference uses
`Shape.common(...).Volume`.

Tolerance stacks record nominal dimensions, signed contribution direction, and
bilateral plus/minus limits. `worst_case` sums absolute deviations;
`rss` combines independent deviations by root-sum-square. The resulting
nominal, lower/upper bounds, limits, method, and source items are stored in a
verification receipt. A failed mate, fit check, or tolerance check removes the
partial rebuild.

## Example

```json
{
  "schema": "design-studio.mechanical-cad-program/1",
  "program_id": "mounting-bracket-v1",
  "units": "mm",
  "author": "sol-5.6",
  "commands": [
    {
      "id": "base",
      "op": "part.box",
      "params": {"length_mm": 80, "width_mm": 40, "height_mm": 4},
      "provenance": ["drawing/page-1#base-plate"]
    },
    {
      "id": "hole",
      "op": "feature.hole",
      "params": {
        "base": "base",
        "radius_mm": 2,
        "center_mm": [10, 10, 0],
        "depth_mm": 4
      },
      "provenance": ["drawing/page-1#hole-diameter", "drawing/page-1#hole-center"]
    }
  ],
  "checks": [
    {"kind": "valid_shape", "target": "hole"},
    {"kind": "volume", "target": "hole"},
    {"kind": "rebuild", "target": "hole"}
  ]
}
```

## Invocation

The native FreeCAD workbench command **Mechanical Feature Program…** opens a
JSON program and applies it to the active document. The trusted dispatcher
also exposes `apply_mechanical_cad_program`. It accepts an inline `program`
object (the normal Sol path) or an existing `program_path` (the reviewed-file
path). For an inline program, the host persists the normalized JSON under
`mechanical/programs/` before construction. The path form is:

```json
{
  "mechanical_path": "<workspace>/mechanical/product.FCStd",
  "workspace_root": "<workspace>",
  "program_path": "<workspace>/mechanical/programs/mounting-bracket-v1.json"
}
```

The inline form uses the same program object shown above:

```json
{
  "mechanical_path": "<workspace>/mechanical/product.FCStd",
  "workspace_root": "<workspace>",
  "program": {
    "schema": "design-studio.mechanical-cad-program/1",
    "program_id": "mounting-bracket-v1",
    "units": "mm",
    "author": "sol-5.6",
    "commands": [],
    "checks": []
  }
}
```

The dispatcher requires both files to remain inside the active workspace,
recomputes the document, saves it, and returns the program digest, generated
FreeCAD object names, and check results.

## Extension path

The next feature families should compile into the same command envelope:

1. loft, sweep, shell, draft, mirror and surface/thicken operations;
2. sheet-metal bends and flattening;
3. drawing views, dimensions, datums and tolerances;
4. standards-backed Hole Wizard and manufacturing metadata;
5. tolerance analysis with statistical distributions beyond RSS.

These should be implemented as typed operations with feature-data parameters,
not by copying SolidWorks UI command IDs. FreeCAD/Open CASCADE remains the
geometry authority and the program receipt remains the verification boundary.

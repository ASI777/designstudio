# SolidWorks-inspired feature map for FreeCAD

We are borrowing the *feature concepts and workflow*, not SolidWorks source
code or private command IDs. SolidWorks exposes these concepts through its API
objects such as `ISketchManager`, `IFeatureManager`, `IAssemblyDoc` and
`IDrawingDoc`; FreeCAD exposes equivalent construction through Part Design,
Part, Sketcher, TechDraw and the Open CASCADE kernel.

| SolidWorks concept | FreeCAD construction target | DesignStudio status |
| --- | --- | --- |
| Sketch + dimensions + relations | `Sketcher::SketchObject`, `Sketcher.Constraint` | Built: native geometry, typed constraints and status receipts |
| Extruded boss/base | `PartDesign::Pad` or `Part.Shape.extrude` | Built as `feature.extrude` |
| Extruded cut | `PartDesign::Pocket` or `Shape.cut` | Built as `feature.cut` / `feature.hole` |
| Revolve | `PartDesign::Revolution` or `Shape.revolve` | Built as `feature.revolve` |
| Sweep / loft | `PartDesign::AdditivePipe`, `AdditiveLoft`, subtractive variants | Planned P1 |
| Fillet | `PartDesign::Fillet` or `Shape.makeFillet` | Built as `feature.fillet` |
| Chamfer | `PartDesign::Chamfer` | Built as `feature.chamfer` |
| Shell / thickness | `PartDesign::Thickness` or `Shape.makeThickness` | Existing enclosure geometry; typed operation planned |
| Draft | `PartDesign::Draft` | Planned P1 |
| Hole Wizard | `PartDesign::Hole` with standard metadata | Current typed cylindrical hole; standards library next |
| Linear / circular pattern | `PartDesign::LinearPattern`, `PolarPattern` | Built as typed compound patterns |
| Mirror | `PartDesign::Mirrored` | Planned P0 extension |
| Boolean combine | `Part::Fuse`, `Part::Cut`, `Part::Common` | Built as `feature.fuse` / `feature.cut` |
| Surface trim / knit / thicken | Part surface tools and OCCT sewing | Planned P1 for scan-derived styling |
| Sheet-metal flange / bend / flatten | FreeCAD SheetMetal workbench objects | Planned P1; capability-detected |
| Weldment structural members | Part Design/Part objects plus cut-list metadata | Planned P2 |
| Assembly components / mates | `App::Part`, `App::Link`, topology-aware mate commands | Built: origin plus `FaceN`/`EdgeN` face-distance, face-coincident, edge-align and concentric mates |
| Interference / clearance | `Shape.common`, `Shape.distToShape`, existing collision report | Built: typed program checks with fail-closed receipts |
| Tolerance stacks | signed dimension contributors, worst-case/RSS verification receipt | Built: nominal, bilateral tolerances, limits, worst-case and RSS checks |
| Drawing views / sections | TechDraw pages, projections and sections | Planned P1 |
| Dimensions / datums / tolerances | TechDraw annotations and expression properties | Planned P1 |
| STEP / STL / DXF / PDF release | FreeCAD exporters and existing release gates | Existing in project; connect to program receipt |

## Why the first command set is small

SolidWorks has hundreds of API calls. Exposing all of them to Sol 5.6 would
create a large, brittle tool surface and make UI selection state part of the
model’s reasoning. The first DesignStudio command set instead covers the
stable dependency graph used by most enclosure and mechanical-product
concepts:

```text
primitive/profile → feature → boolean/detail → pattern → verification
```

The command program records this graph as stable IDs. A later operation can
refer to `base` or `sketch_1`, while the FreeCAD host resolves the reference to
the native object. This is the semantic equivalent of a SolidWorks feature
tree and is more reliable than replaying screen coordinates.

## Recommended extension order

1. Add `sketch.arc` and face/edge-referenced dimensional relations.
2. Add shell, draft, mirror, loft and sweep.
3. Add surface repair/thicken for phone-scan industrial styling.
4. Add TechDraw views, dimensions, datums and tolerances.
5. Add sheet metal, weldments and optional workbench capabilities behind
   capability checks.

Every extension should add a typed schema operation, a deterministic FreeCAD
builder, an explicit verification result and a small fixture program before it
is exposed to the model.

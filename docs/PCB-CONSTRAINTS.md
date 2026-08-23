# PCB constraints and geometry contract

DesignStudio stores manufacturing constraints in the board document under
`pcb_rules`. The object conforms to
`urn:design-studio:schema:pcb-rules:1` and travels unchanged when a flat v2
board is embedded in a mechatronic project v3 document.

The built-in `industrial-class2-b-baseline` is a safe project default, not a
claim that a fabricator supports those dimensions. A production project should
replace its limits and provenance with a selected fabricator/assembler profile.
The source, source revision, IPC performance class and producibility level are
part of the saved design evidence.

## Rule resolution

Current native resolution is:

1. the project PCB profile supplies manufacturing floors and checker switches;
2. a net class overrides default clearance, trace width, via dimensions,
   differential-pair gap, skew and microvia permission for its member nets;
3. a class-pair rule raises clearance for a specific class combination;
4. a pad override raises the requirement for that pad;
5. a layer-scoped rule area may raise clearance and width or prohibit routing,
   vias and placement;
6. unknown rule severities fail closed as errors.

The implemented precedence is
`layer rule area > pad > net class pair > net class > board profile`. Overrides
are additive floors: a narrower downstream value cannot weaken an upstream
manufacturing or safety limit. Class-pair and rule-area records retain source
and revision provenance.

## Geometry crossing the Qt/native boundary

The additive v2 C API preserves the original ABI while carrying:

- rectangular, rounded-rectangle, oval/capsule and circular pads;
- custom copper polygons with an optional cutout;
- exact component body and courtyard geometry;
- explicit through, blind, buried and laser-microvia technology;
- versioned DRC options guarded by ABI version and structure size.

Routing, clearance, connectivity, copper-pour avoidance and courtyard checks use
these shapes. A legacy native library may still open simple rectangular boards,
but rebuilding a board with custom regions/body geometry fails closed when the
required additive API is unavailable.

## Release behavior

The Qt fallback checker is diagnostic only. Absence of the native DRC produces
`DRC_ENGINE_UNAVAILABLE` as an error and cannot produce a manufacturing pass.
Minimum width, drill, annular ring, drill wall, connectivity, copper edge/hole,
unassigned copper, courtyard, height, thermal-spacing and test-access violations
default to blocking errors.

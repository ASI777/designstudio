# Component CAD workflow

DesignStudio exposes three Assistant operations:

- `extract_component_datasheet(mpn, source?)` creates an isolated preview. It
  never writes the authoritative component or bound-component libraries.
- `publish_component_assets(manifest_path)` accepts only a ready,
  digest-consistent preview and is always subject to the UI approval gate.
- Existing product-build operations resolve only approved, published component
  records for physical placement.

The extraction data flow is:

```text
PDF + exact MPN
  -> GPT-5.6 Sol medium datasheet-inspection/1
  -> selected lossless regions + selected-page text
  -> GPT-5.6 Luna xhigh component/2 construction
  -> component-cad-program/1
  -> deterministic footprint checks + Open CASCADE STEP execution
  -> component-preview/1
  -> explicit approval
  -> SQLite component record + PDF + KiCad v6 footprint + AP242 STEP
```

`component-cad-program/1` contains typed symbol, footprint, and model commands.
Every command has source provenance. Supported footprint operations create SMT
or THT pads and an exact courtyard. Supported model operations create grounded
box, circular-cylinder, and hemispherical-dome solids and export AP242 STEP in
the footprint coordinate frame. The executor rejects unsupported operations,
invalid solids, changed STEP solid counts, unverified land patterns, missing
callout provenance, THT annular-ring violations, pin/pad mismatches, and
incomplete visual grounding. Assumptions remain visible and create incomplete
engineering evidence; they are never silently converted into verified facts.

Missing evidence produces a `state=blocked` partial preview containing the
specific required dimensions. A blocked preview cannot be published. A ready
preview still has `placement_allowed=false`; publication changes it to approved
and stores the generated STEP under the bound-component digest.

The authoritative component library is
`~/.local/share/designstudio/datasets/component-library.sqlite3` (or
`DESIGNSTUDIO_COMPONENT_DB`). It content-addresses PDF, `.kicad_mod`, and STEP
bytes by SHA-256 and records exact-MPN provenance, manufacturer reference
circuits, checks, and digest-bound approvals. Files materialized beside a CAD
project are cache projections, not a second authority. When an approved part
already has both footprint and STEP assets, extraction reuses it before making
a vendor or model call.

Bulk datasheets can be imported resumably with:

```text
python3 tools/component_library.py ingest-datasheets DIRECTORY
```

Filename-derived vendor/MPN values are stored only as search hints. Visual/text
inspection must still prove the exact MPN before component approval.

Placement uses the generated rotated courtyard—not the component origin or pad
centres—and rejects or legalizes any footprint crossing the board polygon or a
cutout. Draft placement is substrate-contained even though connectivity and
routing optimization remain pending.

The saved footprint instance is the sole placement authority for both views.
DesignStudio and DesignCore transform every local pad, body, copper region, and
courtyard by `translate(x_mm, y_mm) * rotateZ(rot_deg)`, with local Y mirrored
first for a bottom-side instance. FreeCAD composes that same instance transform
with `model_to_footprint` and the board frame, so the STEP body follows every 2D
move, rotation, and side change without maintaining a second placement record.

# Bound components

`design-studio.bound-component/1` is the authoritative component-library object
shared by schematic capture, PCB layout, FreeCAD assembly, and electrical
analysis. A complete record contains:

- component identity and the digest of its source `component/2` record;
- datasheet symbol pins and electrical types;
- a deterministic pin-to-pad mapping that excludes mechanical pads;
- concrete pad geometry and an explicit placement courtyard;
- a SHA-256-bound STEP asset;
- the reviewed STEP-model-to-footprint transform;
- the complete datasheet electrical payload; and
- one binding digest covering all of the above.

Create a candidate binding with:

```bash
python3 tools/bind_component.py component.json package.step \
  --model-mpn ACTUAL-MPN
```

The normal datasheet-generated path is:

```bash
python3 tools/extract_component_datasheet.py part.pdf --mpn ACTUAL-MPN
python3 tools/publish_component_assets.py /path/to/preview-manifest.json
```

This path generates STEP from Luna's typed commands through Open CASCADE,
validates the STEP round trip, and publishes only after preview approval.
`bind_component.py` remains available for separately reviewed supplier STEP
files.

STEP alignment is independent from 2D PCB placement. An unpublished Sol/Luna
SMT/THT preview cannot be placed on the PCB. For manually supplied STEP, the default
STEP alignment state is `unverified`, so FreeCAD simply does not synchronize a
3D component body until a usable transform is available. To attach a verified
STEP transform, rerun:

```bash
python3 tools/bind_component.py component.json package.step \
  --model-mpn ACTUAL-MPN --alignment verified \
  --transform 1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1
```

For projects carrying a locked FreeCAD mechanical contract, the board harness
places valid automatically extracted `component/2` footprints. Complete bound
components add exact STEP bodies; missing STEP assets no longer cause generic
cylinders or block 2D PCB layout.

Every placed footprint carries a compact `bound-component-ref/1` with the record
digest and absolute STEP path. **Sync Bound STEP Components** in the FreeCAD
workbench verifies the model hash and updates its managed assembly instances.

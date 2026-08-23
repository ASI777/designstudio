# Function-to-form spatial co-design

The implemented vertical slice starts with the parts a person touches, not with
an exterior shell inferred from a car photograph.

## Desktop flow

1. Open a product workspace and create or lock its mechanical contract.
2. In **Mechanical**, load a verified STEP model for a key switch, encoder,
   joystick, FSR, display, connector, or another human-interactive component.
3. Place it in millimetres with translation and roll/pitch/yaw. Grid snapping
   is 0.5 mm and axis snapping is 15 degrees. Coordinate-system, symmetry,
   clearance, and lock intent are persisted in the strict contract. Surface
   snap remains disabled until the host can persist a stable enclosure face ID.
4. Lock position and orientation. Export is disabled until both locks exist and
   the placement is bound to the active mechanical contract revision/digest.
5. Assemble locked placements, protected component/hand/service volumes,
   mounting/load anchors, manufacturing minima, and a deterministic seed into
   `design-studio.support-generation/1`.
6. Generate local deterministic cavities and ribs immediately, or submit the
   same immutable input to `POST /v1/support-jobs` for a ROCm-refined candidate.
7. Invoke the approved `generate_interaction_structure` tool. It accepts only a
   support JSON file inside the active workspace and creates exact FreeCAD
   cavity envelopes and rib B-Reps.

## Authority boundary

Qwen/vLLM may explain trade-offs, propose PCB-island alternatives, and call
typed tools. It does not calculate authoritative ribs and cannot write
FreeCAD. The ROCm worker minimizes approximate path length and AABB collision
penalties; it also marks its result non-authoritative. FreeCAD/OpenCASCADE
reconstructs the selected graph and remains the exact geometry authority.

Release still requires:

- B-Rep validity and exact collision-free clearance;
- minimum wall/rib/draft/tool-access rules for the selected process;
- structural screening followed by exact FEA where risk requires it;
- cable bend radius, connector access, assembly order, serviceability, thermal,
  antenna and battery-safety checks;
- confirmation that the support job's document ID, revision and SHA-256 still
  match the active mechanical contract.

## V1 PCB partitioning

The first release represents electronics as rigid PCB islands connected by
explicit cable or flex-harness contracts. AI may rank alternative island
partitions around the locked interaction zones, but the user chooses the
alternative and the electronics/clearance gates remain deterministic.

The Porsche image or an SVG derived from it can guide silhouette, curvature and
visual language. It cannot supply hidden dimensions, ergonomic hand envelopes,
component clearances, wall thickness, fasteners, load cases or manufacturing
features, so it is reference evidence rather than exact CAD authority.

# Industrial release and handoff gate

The release gate is the only software path that may label a design `ready` for
handoff. It consumes the exact project file, a recent unified verification
report, and `design-studio.release-manifest/1`. It recomputes every digest and
fails closed on stale project revisions, modified artifacts, missing engineering
inputs, built-in rather than fabricator constraints, unresolved components,
unverified MPNs, stale/out-of-stock quotes, or missing approvals.

The manifest also binds the incremental electrical-analysis report. Any failed
ERC, DC operating-point, power-tree, stability, or SPICE category blocks release
and cannot be waived. An incomplete required category blocks release unless the
manifest contains a matching `incomplete_acknowledgements` record with its
domain, category, reviewer, rationale, and timestamp. The gate reports that
accepted incompleteness as a warning, never as a computed pass. `not_applicable`
is accepted only when the analysis engine explicitly determined that the
category does not apply.

Distributed six-axis robot releases additionally bind a hierarchical
`distributed-robot-analysis/1` report, the exact coordinator plus six joint
project digests, and engineering, electrical, mechanical, and manufacturing
approval for every board and the aggregate system. Failed or incomplete axis,
coordinator, system, safety, thermal, routing, simulation, evidence, or
fabrication results block release.

Required production artifacts are a Gerber archive, Excellon drill, BOM,
pick-and-place file, and board STEP. Enclosure projects additionally require an
enclosure STEP and enclosure evidence. The manifest records path, SHA-256,
generator, and generator version for every artifact. Basic format signatures are
checked before release; the gate does not infer that a renamed arbitrary file is
a fabrication output.

## Contracted-manufacturer production export

`tools/export_production_package.py` is the production CAM path. It is separate
from the preview exporter and fails before writing output unless all of the
following bind exactly:

- the authoritative project bytes and revision;
- a passing unified verification report for those exact bytes;
- an approved `design-studio.fabrication-profile/1`;
- the contracted fabricator's and assembler's names;
- the local stackup/DFM source document and its SHA-256;
- the PCB rules, complete physical stackup, impedance contract, CAM origin,
  precision, mask/paste, drill separation and placement conventions applied to
  the project; and
- every placed part's approved STEP digest and model-to-footprint transform.

The profile schema is
`docs/schemas/fabrication-profile-v1.schema.json`. Obtain the profile values
from the contracted manufacturer's controlled capability/stackup document; do
not substitute nominal DesignStudio defaults. Apply it as a new revision:

```text
python3 tools/apply_fabrication_profile.py \
  product-v3.dsproj contracted-fabricator-profile.json product-production.dsproj
```

Run unified verification again on `product-production.dsproj`. Only then export:

```text
python3 tools/export_production_package.py \
  product-production.dsproj contracted-fabricator-profile.json \
  verification-production.json product-production-package \
  --board-step-binary /path/to/designstudio-board-step
```

The package contains Gerber X2 copper, solder-mask, paste, legend and profile
layers plus a Gerber job file; separate PTH and NPTH Excellon files; drill
report; BOM; pick-and-place; IPC-D-356A netlist; and a populated AP242 board
STEP. The STEP exporter imports the exact approved component models and
round-trips the generated assembly through Open CASCADE. A digest manifest and
`SHA256SUMS` bind every output. An empty legend layer is valid only when the
manufacturer profile explicitly specifies `silkscreen: none`; a required
legend cannot be synthesized from courtyard geometry.

For a project containing a locked FreeCAD mechanical contract,
`enclosure_evidence` must be the `mechanical-collision/1` report emitted by
**Sync Bound STEP Components**. The gate validates its canonical digest, exact
project revision/hash, contract digest, age, checked object counts, and status.
Reported solid intersections are non-waivable failures; an incomplete check
requires the scoped `verification.mechanical_collision` acknowledgement.

Every placed part must have manufacturer/MPN identity, an exact-MPN PDF digest,
and a fresh quantity quote with enough stock. Manufacturing and mechanical
review approvals are explicit named records. The manifest must be signed with
an Ed25519 key in the local release trust store. Its canonical signature binds
the project, analysis, evidence, approvals, compliance decisions and artifacts.
Separate evidence-backed distribution reviews for the pinned FreeCAD source and
Hunyuan3D-Omni model terms are mandatory; missing, duplicate, stale or modified
reviews block release.

`designstudio-update package` creates a reproducible archive and unsigned
`design-studio.update-manifest/1`; an authorized operator signs it with
`designstudio-sign-release`. `designstudio-update install` verifies the trusted
signature, archive digest and byte count, extracts to staging, and atomically
installs to an explicit destination. Absolute paths, traversal, links, devices
and FIFOs are rejected before extraction.

Run the gate headlessly:

```text
python3 -m swarm.release.release_gate \
  --project product.dsproj \
  --verification verification.json \
  --manifest release-manifest.json \
  --out release-gate.json
```

Exit code 0 means `ready`; exit code 2 means `blocked`. The legacy
`fab_export_agent.py` is deliberately preview-only because it does not yet emit
the full exact production layer set. Its files cannot be substituted for the
manufacturer-bound output above.

# KiCad 10.0.4 reference workflow

KiCad is a developer-only behavioral oracle for DesignStudio. It is not a
runtime dependency, a product name, a source-code donor, or part of the
distributed build. The pinned record is
`references/kicad-10.0.4.reference.json`; large binary and source artifacts
live under the ignored `reference-only/kicad-10.0.4/` directory.

The upstream release is pinned to tag `10.0.4`, commit
`f7414d419cae5df2d00e7eaacb16fc0e803799bc`. KiCad is predominantly licensed
under GPL-3.0-or-later. A separate license review remains mandatory before any
commercial distribution involving upstream artifacts.

## Recording a subsystem study

Before implementation, add one manifest observation containing the exact
upstream files and tests inspected, behavior visible at the public boundary,
the DesignStudio-owned implementation paths, and the differential tests. Do
not paste implementation excerpts, upstream identifiers, screenshots, icons,
themes, or other UI assets into the checkout.

Behavioral notes should describe inputs, outputs, invariants, ordering,
failure states, and artifact semantics. DesignStudio code and tests should use
its existing terminology and APIs. "Clean room" is not claimed by this
workflow; the goal is documented independent implementation with traceable
reference use.

Run the provenance check with:

```bash
python3 tools/check_reference_provenance.py
```

When verified reference source exists locally, the check also compares exact
file digests and normalized token shingles for only the upstream files named
in the manifest. This keeps the CI gate fast and makes every similarity input
explicit. A similarity finding must be resolved by rewriting original code or
by documenting a legitimate, separately licensed origin outside this gate.

The AppImage and source archive SHA-256 values must replace
`pending-download` immediately after each complete download. A locally present
artifact with a pending or mismatched digest fails the provenance check.

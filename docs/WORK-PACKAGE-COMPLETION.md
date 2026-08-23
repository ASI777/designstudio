# DesignStudio work-package implementation evidence

| Package | Delivered evidence |
|---|---|
| Constraint/fabricator profile and exact geometry | Versioned Class 2/B baseline and fabricator import; additive native ABI; exact pads, custom copper, bodies/courtyards, board polygons/cutouts, explicit via technology; fail-closed DRC rebuild. |
| Structured placement | Persisted locks, functional groups, edge anchors, exact rotated courtyards, height areas, thermal spacing and test access; deterministic fail-closed legalizer and native recheck. |
| Layer connectivity, routing and zones | Layer roles/directions, class layer/via masks and via budgets, correct bottom-SMD-to-last-layer mapping, physical-contact connectivity, policy-aware router, authoritative clipped copper zones. |
| Unified verification | `design-studio.verification/1` report bound to project revision/digest and rule provenance; DRC, connectivity, SI, PI, thermal and mechanical categories with pass/fail/incomplete/not-applicable semantics; UI and headless export. |
| Procurement/datasheet evidence | Exact-MPN quantity quotes, original currency tiers and FX provenance, vendor error reporting, rate/retry bounds, owner-only credentials/tokens, public-web discovery fallback, bounded public HTTPS PDF retrieval, digest cache, exact-MPN and component trust-boundary validation. |
| Release/handoff | `design-studio.release-manifest/1` and fail-closed gate; fresh verification and quote checks, component evidence, artifact formats/digests/generator versions, approvals, tamper/stale/preview rejection, packaged native library and release CLI. |
| Source-built desktop | Pinned FreeCAD 1.1.1 source and submodule digests, reproducible source script, in-process C++ DesignStudio module, electronics-only native profile, workspace navigation and retained upstream workbenches. |
| Product/control plane | Rust JSON-RPC daemon, manifest validation, supervised restart/reconnect, immutable configurations, product slots, candidate Preview/Equip/Revert/Commit, evidence persistence and revision synchronization. |
| Enclosure and generation | Three editable FreeCAD enclosure templates, legal PCB volume derivation, catalog comparison, signed cloud job artifacts, pinned ROCm Hunyuan service and local mesh-to-B-Rep/interface reconstruction. |
| Engineering propagation | Affected-subgraph causal propagation with deterministic solver adapters, engine/input digests, evidence/margins/uncertainty and fail-closed incomplete analysis. |
| Parallel development | Immutable proposal heads, contract compatibility, integration sandboxes, author/approver separation, bounded context retrieval and recoverable budgeted tasks. |
| Production hardening | Trusted Ed25519 release/update manifests, mandatory FreeCAD/model compliance evidence, safe reproducible packages, atomic installer, archive traversal/tamper rejection and fail-closed release gates. |

Completion verification:

- CMake build succeeds with Qt and Open CASCADE enabled.
- The complete current CTest graph passes on the supported Linux baseline.
- The native core suite passes 404 checks, including dense-board routing/DRC.
- Generated unified verification validates as a passing report for the release fixture.
- Release regressions prove signed ready, explicit compliance review,
  explicitly acknowledged incompleteness,
  stale-project, tampered electrical/component/artifact evidence, and
  preview-generator outcomes.
- Installed-layout regression proves `DesignStudio` finds the installed
  `libdesigncore.so` and performs native verification.
- Every installed JSON schema passes Draft 2020-12 schema meta-validation.
- `git diff --check` and Python byte-compilation pass.

The mock Hunyuan service proves API, security, cancellation, deterministic seed,
artifact and reconstruction contracts without a GPU. ROCm correctness/parity is
a separate hardware gate and must run on the pinned MI300X environment before a
production image may be approved; absence of that evidence remains incomplete,
never pass.

The legacy `fab_export_agent.py` remains intentionally preview-only. Production
handoff must use a validated, versioned KiCad or equivalent manufacturing-data
generator; the release gate blocks the preview generator so it cannot be mistaken
for fabrication-ready output.

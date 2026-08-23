# Unified design verification

`design-studio.verification/1` is the release-oriented evidence report produced
by the native engine. It binds results to project document ID, revision, file
digest, and the exact constraint-profile provenance used for the run.

The report has six categories:

- DRC: all enabled native manufacturing and placement rules;
- connectivity: authoritative copper-island count for every multi-terminal net;
- signal integrity: controlled-impedance screening against class targets;
- power integrity: routed DC-current capacity screening at a 10 °C rise;
- thermal: powered-component input completeness and spacing results;
- mechanical: component XYZ envelopes, board/placement DRC, enclosure-evidence
  digest verification, and—on a locked FreeCAD project—a separate exact solid
  intersection report consumed by the release gate.

Each category is `pass`, `fail`, `incomplete`, or `not_applicable`. DRC,
connectivity, and mechanical checks are always required. SI, PI, thermal, and
enclosure evidence become release requirements through
`verification_requirements`; a required category with missing engineering inputs
is `incomplete`, never a pass. Screening calculations are identified by method
and do not claim field-solver sign-off.

Reports can be exported from the DRC panel or headlessly with:

```text
DesignStudio --smoke-test --verification-out report.json project.dsproj
```

# Incremental schematic and electrical analysis

`IncrementalDesign` makes the schematic connectivity state authoritative. Every
component add/remove/replace and pin connect/disconnect operation:

1. advances the design revision;
2. preserves existing schematic symbol positions;
3. identifies the affected component/net dependency cone;
4. reruns ERC for power-pin completeness, NC connections, output conflicts, and
   power/ground shorts;
5. propagates source/load voltage ranges, maximum current, and load power;
6. rebuilds affected power-tree edges;
7. checks mandatory datasheet support components across their required nets;
8. evaluates regulator phase/gain-margin evidence; and
9. executes a bound declarative power model, or reports external SPICE readiness
   without inventing a simulation result; and
10. verifies that the selected manufacturer application-circuit mode is backed
    by datasheet page/figure evidence and applied to the actual netlist.

Results use `pass`, `fail`, `incomplete`, or `not_applicable`. Missing bound
components, current limits, source models, control-loop data, SPICE models, or a
solver are explicitly `incomplete`. A missing mandatory capacitor or incompatible
rail voltage is `fail`.

For active power parts, `simulation_models.behavioral_power` may bind a safe
`design-studio.behavioral-power/1` model. Each edit then recomputes input/output
operating points, dropout, current limit, device loss, and junction temperature.
The model and its datasheet-derived limits are covered by the bound-component
digest.

The event log records the changed references and recomputed nets for every edit.
The harness replays its final proposal through this same edit boundary during
verification and stores the resulting `incremental-analysis/1` report in the
session/result sidecar.

Validated generated netlists can execute with the bounded ngspice batch adapter.
External includes/libraries, control or shell constructs, unsupported syntax,
missing solvers, timeouts, and solver errors remain `incomplete`. A validated
behavioral model can also pass the simulation category; an unvalidated raw model remains
`incomplete` until it is actually executed. Critical incomplete electrical,
thermal, safety, routing, mechanical, evidence, or manufacturing checks block
release. An acknowledgement records the concern for visibility but cannot turn
an incomplete critical check into a pass.

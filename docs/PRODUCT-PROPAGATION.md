# Product-subgraph engineering propagation

`ProductPropagation` walks only outgoing dependency edges from the semantic
nodes changed by a configuration. Analysis rules declare their dependency
nodes, exact input keys, method, domain solver and requirement. A run records
the affected nodes and causal paths, executes reached rules, reuses unchanged
input digests, and lists analyses that were provably outside the dependency
cone.

Evidence uses explicit value, unit, requirement, margin, method, uncertainty,
assumptions, source run and dependencies. Missing inputs, unavailable adapters
and solver failures are `incomplete`; none can become an inferred pass. The
initial adapters cover battery runtime, thermal operating point, clearance and
control margin. Native engine versions are part of each input digest so an
engine upgrade forces a rerun.

The regression fixture models battery and shell changes in the balancing
sphere. A battery change reruns runtime, thermal and control but not shell
clearance or deep FEA. A shell change reruns clearance, reuses unchanged control
inputs, leaves electrical/thermal analyses untouched, and reports unavailable
deep FEA as incomplete with a causal path.

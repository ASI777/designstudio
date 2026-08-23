# Distributed six-axis robot electronics

Six-axis robot requests use one central CAN/E-stop coordinator and six isolated
joint-controller configurations. The application contract contains exactly six
`axis_requirements` rows and a digest-bound `system_architecture`; no joint
inherits another joint's motor, current, encoder, brake, thermal, cable, or
safety values.

Sparse requests ask at most three grouped questions: the six-row axis table;
supply/CAN/cable information; and E-stop/braking/environment/safety
expectations. The resolver still returns exactly three understandable product
directions. Applying a direction creates seven immutable board snapshots plus a
top-level sandbox configuration. Matching board templates may be reused only
when both requirement and resulting artifact digests match.

`swarm.memory.distributed_robot` builds the coordinator/joint product graph and
produces hierarchical axis, coordinator, and aggregate reports. It calculates
continuous and peak bus current, voltage/current margins, loss and junction
temperature, cable/connector loading, CAN termination/loading, E-stop
propagation, and braking/regeneration readiness. Every report links immutable
input and output digests. Evidence is labelled as `calculated`, `simulated`,
`assumption`, or `missing_evidence`; it is never called a physical measurement.

Validated generated SPICE netlists may run through the bounded ngspice batch
adapter. Includes, libraries, control blocks, shell/file operations, unsupported
constructs, missing solvers, and execution failures yield `incomplete`.

Component acquisition checks the approved SQLite library first. Remaining
unique MPNs use concurrent exact-MPN DigiKey/Mouser lookup, sequential
datasheet attempts, and serial component CAD generation. Digest-bound previews
enter the review queue; approval is per exact artifact digest, and blocked,
rejected, changed, or unreviewed assets cannot be published for placement.

Distributed release requires pass reports for every axis, the coordinator, and
the system; exact coordinator and six joint project digests; and engineering,
electrical, mechanical, and manufacturing approval for every board and the
system. Any failed or incomplete critical gate blocks release. Eurocircuits
remains the initial manufacturing profile, but analysis and approval do not
constitute a manufacturing or safety guarantee.

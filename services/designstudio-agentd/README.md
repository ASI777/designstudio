# designstudio-agentd

`designstudio-agentd` is the local DesignStudio control plane. It owns product
semantics, immutable configuration history, selection, jobs, evidence state,
typed permissions and release evaluation. It does not own FreeCAD geometry,
electronic connectivity, solver implementations, or cloud-generated meshes.

The service accepts newline-delimited JSON-RPC 2.0 over a user-only Unix socket:

```sh
cargo run --release -- --socket "$XDG_RUNTIME_DIR/designstudio-agentd.sock"
```

`--stdio` is provided for contract tests and supervised child-process startup.
Every request contains an explicit permission set. For example:

```json
{"jsonrpc":"2.0","id":1,"method":"project/open","params":{"manifest_path":"/work/Product.dsworkspace/manifest.json"},"auth":{"permissions":["project:open"]}}
```

The daemon rejects unknown fields, unknown operations, path traversal, stale
workspace state, incompatible slot/variant pairs, in-place edits of committed
configurations, unbounded generation requests, and release attempts with failed,
incomplete or missing required evidence.

Parallel development uses immutable `branch/*` proposal branches and
`integration/*` sandboxes. Sandboxes snapshot selected branch heads, reject
stale ancestry, missing evidence and conflicting contract revisions, and
require every requested approval role. An author of any selected branch cannot
approve that integration. `product/context` returns a bounded semantic
subgraph, while `agent/*` tasks enforce step, elapsed-time and cost budgets with
explicit cancellation and at most three recovery attempts.

Phase 1 adds the `desktop_ai_control_console` local backend. `product/compile`
turns the approved USB-C plus BLE controller intent into a deterministic,
digest-bound dependency DAG. Its packages reference the existing component
evidence/CAD, incremental schematic, native PCB, FreeCAD, integration, and
unified verification engines. `workflow/approve`, `workflow/start`,
`workflow/read`, `workflow/cancel`, and `workflow/resume` enforce architecture
approval, stale-input rejection, checkpoint digests, and unit budgets. This
surface has no GPU, remote provisioning, or arbitrary command execution.
The intent requires an explicit absolute `source_root`, then binds the
authoritative pre-v72 source project and acceptance report beneath that root by
SHA-256. It also records the approved USB4085-GF-A USB-C connector and
ESP32-S3-WROOM-1-N8R8 BLE module.

Phase 1 adapters are deliberately typed artifact-replay validators, not rebuild
commands. They consume genuine outputs already produced by the registered
subsystems and reject missing or inconsistent evidence: exact component
bindings and STEP digests, ERC topology, routed PCB and DRC/connectivity,
FreeCAD AP242 STEP, cross-domain acceptance, and unified verification. A
successful result therefore means the existing artifact set passed its
domain-specific contract; it does not claim that the daemon reran those tools.
Future rebuild executors must remain external typed host adapters rather than
arbitrary shell commands inside this daemon.

Workflow execution advances exactly one validated package per start/resume
request. It writes a digest-bound result below `.designstudio/workflows/` and
persists the checkpoint before returning, so callers can cancel between package
boundaries without losing or double-charging completed work. Failed validation
does not create a successful checkpoint or consume budget.

## Workspace contract

The manifest and referenced data use these schemas:

- `docs/schemas/workspace-v1.schema.json`
- `docs/schemas/product-graph-v1.schema.json`
- `docs/schemas/configuration-v1.schema.json`
- `docs/schemas/evidence-record-v1.schema.json`
- `docs/schemas/generation-job-v1.schema.json`
- `docs/schemas/product-intent-v1.schema.json`
- `docs/schemas/work-package-graph-v1.schema.json`
- `docs/schemas/workflow-run-v1.schema.json`
- `docs/schemas/interaction-surface-map-v1.schema.json`

Native documents remain authoritative at the paths named by the workspace.
New configuration snapshots are created with `create_new` semantics in
`configurations/`; the service never overwrites them. Mutable recovery indexes
are replaced atomically under `.designstudio/`, while `events.jsonl` is an
append-only audit trail.

## Verification

Run native Rust tests where the Rust toolchain is available:

```sh
cargo test --manifest-path services/designstudio-agentd/Cargo.toml
```

Schema and source-boundary tests can run in the repository's existing Python
environment:

```sh
python3 services/designstudio-agentd/run_contract_tests.py
```

The Rust test fixture proves that Equip and Commit create child snapshots and
leave their parents unchanged, permission checks fail closed, traversal is
rejected, corrupt persisted configurations fail closed, the audit log persists,
missing required evidence blocks release, all six artifact validators can
complete in dependency order, and failed engine validation cannot consume
budget. `test_stdio_protocol.py` drives the real executable across two process
lifetimes and proves permission, workspace, graph and daemon revisions remain
synchronized after restart.

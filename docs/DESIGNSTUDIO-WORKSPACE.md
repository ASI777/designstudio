# DesignStudio workspace and control plane

A `.dsworkspace` is a directory, not a new monolithic document format. Its
`manifest.json` names the authoritative FreeCAD and electronics documents and
the versioned semantic files consumed by `designstudio-agentd`:

```text
Product.dsworkspace/
├── manifest.json
├── mechanical/product.FCStd
├── electronics/product.dsproj
├── product-graph.json
├── configurations/baseline.json
├── contracts/
├── evidence/
├── assets/
├── generated/
├── cache/
└── .designstudio/          # local recovery index and append-only event log
```

Opening a workspace is deliberately strict. All manifest paths must be relative,
must remain below the workspace root, and all authoritative references must
exist. The baseline configuration must match both the workspace and graph
revisions and its SHA-256 content digest must validate. Graph edges, slots and
variants must resolve to existing semantic IDs.

The Qt shell supervises `designstudio-agentd --stdio` as a child process. It
validates the manifest locally before opening the electronics document, then
requires the daemon to echo the same workspace identity and revision plus the
product-graph revision. If the daemon exits, the shell restarts it with a bounded
retry budget and re-opens the manifest; the daemon rebuilds its live state from
the immutable configuration snapshots and atomic `.designstudio/state.json`
recovery index. A mismatch is shown as a failed control-plane state and is never
silently accepted.

## Revision behavior

Preview returns a derived view and writes nothing. Equip writes a new sandbox
configuration. Commit writes another child with `state: committed`; it does not
change the sandbox. Configuration files are immutable, so Revert is selection of
an ancestor rather than reverse mutation. The daemon's atomic state file is only
a recovery index and never replaces those snapshots as engineering history.

## Authority and release behavior

The product graph stores identity and relationships, while each node declares
its authority (`freecad`, `electronics`, `product_graph`, or `contract`) and a
source reference. This prevents the graph from silently becoming a second CAD
or PCB document.

Release evaluation is independent of candidate ranking. A committed
configuration is blocked when a selected variant declares a failed/incomplete
gate, or when any evidence required by its slots is missing, failed, or
incomplete. Missing evidence is never interpreted as a pass.

Cloud generation submission records only a bounded local job and an input
digest. The AMD service and signed artifact transfer are separate adapters; a
returned mesh cannot change a configuration or satisfy a manufacturing release
gate by itself.

# Plain-language application configuration

The application layer sits before the authoritative mechanical, schematic, and
PCB documents. It classifies a normal-language product request into one of the
seven reference families (or `custom_concept`), records user values separately
from assumptions, asks at most three high-impact questions, and returns exactly
three outcome-named alternatives.

The local `designstudio-agentd` control plane exposes:

- `application/catalog` — the seven Product Home starters and their ranked
  decisions;
- `application/resolve` (also available as `application/options`) — deterministic
  family matching, adaptive questions, and the typed option set;
- `application/preview` — evaluates one option with `persisted: false`;
- `application/apply` — requires `approved: true`, then creates an isolated
  sandbox child configuration with mechanical, schematic, PCB, component-
  binding, and verification stage requirements.

The option cards intentionally keep component identities, calculation inputs,
and rule keys under `technical_details`. Estimates are marked as estimates,
and incomplete or failed gates remain visible until evidence-backed checks pass.
Applying an option does not edit the authoritative documents; the existing
commit/release gate remains the only path to a product revision or release.

For `robotic_joint_capstone`, the contract is the distributed six-axis form:
`axis_requirements` contains six independently digest-bound rows and
`system_architecture` describes the coordinator, six joint boards, supply bus,
CAN, E-stop, and braking paths. Applying it creates one coordinator and six
joint-board configuration snapshots; missing per-axis values stay explicit and
incomplete.

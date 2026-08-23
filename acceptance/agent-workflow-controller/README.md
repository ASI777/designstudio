# Agent Workflow Controller acceptance project

This is an original DesignStudio engineering design: a 4×3 key field, separate
command key, encoder, two-axis joystick, thumb-edge FSR, ESP32-S3 module, and 13
addressable RGB LEDs. KiCad 10.0.4 was used only as the pinned behavioral
reference documented in `docs/KICAD-REFERENCE-WORKFLOW.md`; it is not required
to open, verify, or continue this project.

## Final artifacts

- `generated/agent-workflow-controller.dsproj` — editable DesignStudio project/2.
- `generated/agent-workflow-controller-v3.dsproj` — synchronized,
  non-destructive `design-studio.project/3` wrapper with the same final board.
- `generated/schematic.svg` and `generated/pcb-layout.svg` — portable review
  views. The `.dsproj` remains authoritative for pin/net and copper geometry.
- `generated/components/*/package.step` — 29 approved, project-local AP242 STEP
  assets built
  through the typed component-CAD pipeline and round-trip read by Open CASCADE.
- `generated/step-models.csv` and `generated/evidence-manifest.json` — STEP,
  datasheet, footprint, binding, and SHA-256 index.
- `generated/verification-report.json` — passing native DesignCore
  DRC/connectivity, signal-integrity, power-integrity, thermal, and mechanical
  report.
- `generated/agent-workflow-controller-board.step` — populated AP242
  enclosure-reference PCB assembly with 451 solids and all 106 placed
  components (explicitly not contracted-manufacturer CAM).
- `generated/agent-workflow-controller-board.preview.json` — thickness, project
  and STEP digests, kernel results, and release-boundary evidence.
- `generated/enclosure-assets/FSR-402-manufacturer-reference.step` —
  manufacturer FSR 402 CAD for placement in the enclosure assembly.
- `generated/erc-report.json` — schematic/PCB annotation and electrical-rule
  report.
- `generated/acceptance-report.json` — compact, hash-bound acceptance summary.
- `generated/drc-waivers.json` — empty: no DRC errors or warnings are waived.
- `generated/agent-workflow-controller-fab/` — explicitly marked preview-only
  Gerber/Excellon/BOM/PnP output. The release gate intentionally rejects it.
- `fabricator-handoff/` — controlled-profile request, guarded response template,
  and instructions for converting a named fabricator's approved response into
  the final production revision.

The repository now has a manufacturer-bound production exporter, but no
controller production package is intentionally present yet. The contracted
fabricator/assembler and their controlled stackup/DFM document have not been
provided. `generated/agent-workflow-controller-v3.dsproj` therefore retains
`fabricator: unassigned`; the exporter rejects it instead of inventing rules.

## Verified result

The finalized project contains 106 annotated symbols/footprints, 342 schematic
pins, 65 nets, 2,369 copper strokes/segments (including the filled ground
reference), 249 vias, and 14 designated test-access points. Native verification
and ERC both pass with zero errors and zero warnings. GND is one connected
copper component. USB routing is length matched at 23.9248/23.9249 mm; the
screening model predicts 45.10 ohm single-ended and 89.27 ohm differential
impedance.

The BQ24074RGTR is modeled as the datasheet's 16-pin, 3×3 mm RGT VQFN with an
exposed pad. Its OUT, IN, BAT, programming, status, and mode-select pins are
annotated explicitly; EN2/EN1 select the external-ILIM mode and CE is asserted
for charging.

## Reproducible final pipeline

From the repository root, after building DesignStudio and the Open CASCADE
component compiler:

```bash
DESIGNSTUDIO_COMPONENT_CAD=/tmp/designstudio-occt-build/designstudio-component-cad \
python3 tools/build_agent_workflow_controller.py

python3 tools/route_agent_controller_usb.py --clear-all \
  acceptance/agent-workflow-controller/generated/agent-workflow-controller.dsproj

DESIGNSTUDIO_CORE_LIB=/tmp/designstudio-full-build/core/libdesigncore.so \
DESIGNSTUDIO_ROUTE_MAX_EXPANSIONS=900000 \
python3 tools/route_agent_controller_power.py \
  acceptance/agent-workflow-controller/generated/agent-workflow-controller.dsproj

DESIGNSTUDIO_CORE_LIB=/tmp/designstudio-full-build/core/libdesigncore.so \
DESIGNSTUDIO_ROUTE_MAX_EXPANSIONS=500000 DESIGNSTUDIO_ROUTE_PRESERVE=1 \
DESIGNSTUDIO_ROUTE_SKIP_NETS='GND,+3V3,VBAT,VSYS,+5V_LED_RAW,+5V_LED,VBUS,USB_D+,USB_D-,CC1,CC2,LED_BOOST_SW' \
python3 swarm/agents/bga_reroute_agent.py \
  acceptance/agent-workflow-controller/generated/agent-workflow-controller.dsproj

DESIGNSTUDIO_CORE_LIB=/tmp/designstudio-full-build/core/libdesigncore.so \
python3 tools/pour_net_plane.py \
  acceptance/agent-workflow-controller/generated/agent-workflow-controller.dsproj \
  --net GND --layer 1 --line-width 1.0 --clearance 0.25 --no-stitch

python3 tools/optimize_route_corners.py \
  acceptance/agent-workflow-controller/generated/agent-workflow-controller.dsproj \
  --setback 0.05

QT_QPA_PLATFORM=offscreen LD_LIBRARY_PATH=/tmp/designstudio-full-build/core \
/tmp/designstudio-full-build/app/QtDesignStudio/DesignStudio \
  --verification-out acceptance/agent-workflow-controller/generated/verification-report.json \
  --smoke-test acceptance/agent-workflow-controller/generated/agent-workflow-controller.dsproj

python3 tools/finalize_agent_workflow_controller.py --prepare

QT_QPA_PLATFORM=offscreen LD_LIBRARY_PATH=/tmp/designstudio-full-build/core \
/tmp/designstudio-full-build/app/QtDesignStudio/DesignStudio \
  --verification-out acceptance/agent-workflow-controller/generated/verification-report.json \
  --smoke-test acceptance/agent-workflow-controller/generated/agent-workflow-controller.dsproj

python3 tools/finalize_agent_workflow_controller.py
python3 tools/render_agent_workflow_controller.py
python3 swarm/agents/fab_export_agent.py \
  acceptance/agent-workflow-controller/generated/agent-workflow-controller.dsproj
python3 tools/check_reference_provenance.py
```

## Release boundary

This is a completed engineering acceptance design, not a manufacturing release.
A named fabricator/assembler profile, measured battery runtime, RF/regulatory
certification, protected-cell qualification, battery-transport approval, and
physical prototype validation remain blocking gates. Credentials and agent
session data are not stored on the controller.

To create the CAM package after contracting the manufacturer, provide:

1. Fabricator and assembler legal/trading names.
2. The controlled four-layer stackup/capability PDF or URL, revision and date.
3. Finished thickness/tolerance, copper weights, dielectric materials and
   thicknesses, surface finish, mask/legend choices, and controlled-impedance
   targets/tolerances.
4. Minimum trace/space, drills, annular ring, drill spacing, copper-to-edge,
   copper-to-hole, mask sliver, and silkscreen limits.
5. CAM coordinate precision/origin, PTH/NPTH, paste, placement rotation, and
   bottom-side conventions.

Record those values in `design-studio.fabrication-profile/1`, apply the profile
as a new revision, and rerun native verification before using
`tools/export_production_package.py`. The exact request and guarded response
template are in `fabricator-handoff/`. See `docs/INDUSTRIAL-RELEASE.md`.

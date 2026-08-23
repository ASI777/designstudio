# Request for controlled PCB fabrication and assembly profile

To: T-Works Foundation PCB Fabrication Lab / selected assembly partner  
Project: Agent Workflow Controller  
DesignStudio document: `project:agent-workflow-controller-v1`, revision 1

Please review this design for four-layer prototype fabrication and assembly and
return a controlled capability/stackup document plus named engineering approval.
DesignStudio cannot issue production CAM until the values below are bound to the
project and the board is reverified.

## Board summary

- Finished outline envelope: 126.0 × 116.0 mm, polygon outline with four
  mounting cutouts.
- Layer count: 4.
- Placed components: 106; bottom-side addressable LEDs are included.
- Through vias: 249.
- Drill range used by the design: 0.25–1.20 mm.
- Minimum annular ring used: 0.125 mm.
- Routed non-pour trace-width range: 0.15–0.80 mm.
- Minimum designed copper clearance: 0.15 mm.
- USB 2.0 differential pair: 0.22 mm traces, 0.40 mm pair gap,
  23.9248/23.9249 mm routed lengths, 90 ohm differential target with ±10%
  tolerance.
- Engineering stackup target: 1.60 mm finished FR-4, 35 µm copper,
  F.Cu / In1.GND / In2.Power / B.Cu, with a 0.10 mm F.Cu-to-GND dielectric.
  This is a target only; please replace it with your controlled construction.
- Electrical/native DRC, connectivity, power, signal, thermal, mechanical and
  ERC reports currently pass with zero errors and zero warnings.

## Controlled data required from the fabricator

Please provide:

1. Legal/trading fabricator name and the legal/trading name of the assembler.
2. Controlled document title/path or URL, revision, issue date and SHA-256.
3. Finished thickness and tolerance.
4. Every copper/dielectric layer in order, including:
   - material designation;
   - pressed dielectric thickness;
   - copper finished thickness/weight;
   - dielectric constant at the relevant frequency;
   - loss tangent.
5. Surface finish, solder-mask colour/type and legend colour/process.
6. Guaranteed production limits for:
   - trace width and copper spacing;
   - mechanical and laser drill;
   - annular ring;
   - drill-to-drill wall;
   - copper-to-board-edge and copper-to-foreign-hole;
   - solder-mask sliver;
   - silkscreen width and clearance.
7. Controlled-impedance process for 90 ohm differential USB:
   - permitted layers;
   - recommended trace width/gap for the supplied stackup;
   - tolerance;
   - whether a test coupon and impedance report are required.
8. CAM conventions:
   - accepted Gerber format and coordinate precision;
   - absolute-origin convention;
   - separate PTH/NPTH requirements;
   - mask expansion and paste reduction;
   - legend requirements.
9. Assembly conventions:
   - whether through-hole parts are included in placement;
   - bottom-side rotation viewed from top or bottom;
   - any package-specific rotation corrections;
   - via-in-pad policy and whether filled/capped vias are available.
10. Named reviewer, approval status and approval timestamp for this profile.

## Requested manufacturing outputs

After the profile is accepted, DesignStudio will emit:

- Gerber X2 copper, mask, paste, legend, profile and Gerber job files;
- separate PTH and NPTH Excellon files plus drill report;
- BOM and pick-and-place;
- IPC-D-356A electrical netlist;
- populated AP242 board STEP;
- SHA-256 manifest for the exact verified project and every output.

Please do not manufacture from the `agent-workflow-controller-fab` directory.
Those files are explicitly preview-only and omit production mask/paste/legend
contracts.

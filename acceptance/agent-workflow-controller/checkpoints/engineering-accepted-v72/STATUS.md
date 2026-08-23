# Engineering-accepted controller v72

The authoritative project is
`../../generated/agent-workflow-controller.dsproj`.

Verified state:

- ERC: pass, zero errors and zero warnings.
- Native connectivity: pass; every routed net, including GND, has one copper
  component.
- Native DRC: pass, zero errors and zero warnings.
- Courtyard overlaps: zero.
- Right-angle bends: zero.
- Mechanical, power-integrity, signal-integrity and thermal categories: pass.
- AP242 board STEP: 451 solids, 106 placed components and 306 holes.
- DesignStudio Recent Products: authoritative project is the first entry.

Authoritative digests:

- Project:
  `0cbd3244611fa64fb3ec828f12398a82ce21781b56aaad1fd0b2e588c26da7c8`
- Unified verification:
  `50c21820112c5e5926038645b9f7891916c9184e7f66967d45c85c728b6b39af`
- Engineering acceptance:
  `babb38c458ca4605aa39f87846232f1bb19387fdf2f6f76d8c8a6f9b20deb841`
- Board STEP:
  `eb582e2cdf854f39a4ac4b44f8bd575a9337a35a6c704d49249dedddf21fab39`

Manufacturing release remains blocked intentionally. The production exporter
was exercised and rejected the project because no contracted fabricator and
assembler profile, controlled stackup/DFM source, approval, impedance contract,
or CAM conventions have been supplied. Preview Gerbers are retained for visual
inspection only and must not be sent to a board house.

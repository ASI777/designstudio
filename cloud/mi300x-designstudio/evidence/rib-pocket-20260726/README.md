# MI300X rib and pocket experiment — 2026-07-26

This evidence set tests the DesignStudio boundary in which a ROCm worker
suggests a non-authoritative cavity/rib graph and the local FreeCAD host rebuilds
and verifies exact B-Rep geometry.

## Runtime

- Accelerator: AMD Instinct MI300X VF
- ROCm/HIP: 6.4.43484
- PyTorch: 2.5.1
- Base image:
  `rocm/pytorch:rocm6.4.2_ubuntu22.04_py3.10_pytorch_release_2.5.1`
- Base digest:
  `sha256:37f41a1cd94019688669a1b20d33ea74156e0c129ef6b8270076ef214a6a1a2c`
- Fixed optimizer image:
  `sha256:5fb23d5aab3f60c30a25296740c2e48418ed12204ee02212b632d5287753c112`

The service was bound to Droplet loopback only and stopped after every
experiment iteration.

## Findings

1. The first two MI300X runs were deterministic and rejected a stale placement
   revision, but FreeCAD rejected their graph because it contained zero-length
   rib segments.
2. The optimizer now removes collapsed consecutive path points. Repeating the
   same input produced identical fixed results, but exact FreeCAD intersections
   found four ribs starting inside protected component pockets.
3. Revision 2 preserves all component placements and moves only the attachment
   anchors to explicit mount interfaces outside the clearance envelopes. Two
   MI300X runs again produced identical results.
4. The revision-2 result contains 16 ribs and four protected pockets. FreeCAD
   passed digest binding, B-Rep validity, exact collision, manufacturing
   dimensions, AP242 export, and STEP re-import.

Structural screening and assembly-order validation were not run, so the result
remains `review_required` and is not release-eligible.

## Primary artifacts

- `result-v2-a.json` and `result-v2-b.json`: identical ROCm candidate graphs.
- `run-evidence-v2.json`: runtime, timing, image digest, and determinism record.
- `freecad-v2/RibPocketExperiment.FCStd`: editable FreeCAD cavity/rib objects.
- `freecad-v2/RibPocketExperiment.step`: AP242 review export.
- `freecad-v2/host-validation.json`: exact host-gate report and artifact hashes.

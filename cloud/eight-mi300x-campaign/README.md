# Single-MI300X engineering campaign

This directory retains its historical name, but its active profile is now a
bounded five-hour **single-MI300X** DigitalOcean campaign. It does not
provision anything. Provisioning starts only after an exact Droplet IP and
numeric Droplet ID are supplied.

## Local preparation status

`validate_inputs.py` checks the six Porsche masks, five exact component
MPN/AP242 bindings, enclosure anchors, explicit material/load/thermal
assumptions, and the PCB DRC/connectivity/SI/power/thermal baseline.
`build_config.py` then hashes those inputs into
`campaign.single-gpu.mock.json`. That file is deliberately rejected when
`--mock` is absent; production revisions and commands cannot silently fall
back to mock evidence.

The scheduler:

- creates one worker and sets `ROCR_VISIBLE_DEVICES`,
  `HIP_VISIBLE_DEVICES`, and `CUDA_VISIBLE_DEVICES` to `0` for every GPU job;
- executes the six work packages serially on GPU 0;
- records 100 ms mock or five-second production telemetry;
- persists state atomically, resumes interrupted jobs, and re-verifies every
  successful artifact before trusting it;
- retries only where `max_attempts` explicitly permits it;
- stops long-job admission at T+240 minutes and stops compute no later than
  T+290 minutes, reserving ten minutes for verified closeout;
- writes a digest-bound `multi-gpu-campaign/1` manifest containing lane,
  role, revision, seed, inputs, outputs, solver version, utilization and
  authority.

The single-GPU time allocation is:

- T+0–20 minutes: hardware/ROCm gate and burn-in;
- T+20–100: Porsche seed 200;
- T+100–140: hero component visualization evidence;
- T+140–200: curvature segmentation and editable surface fitting;
- T+200–260: enclosure optimization and MFEM-HIP screening;
- T+260–285: Qwen expert routing plus CPU PCB verification;
- T+285–300: exact reconstruction, artifact verification and destruction.

Run the full local mock:

```sh
python3 cloud/eight-mi300x-campaign/validate_inputs.py
python3 cloud/eight-mi300x-campaign/build_config.py
run_dir="$(mktemp -d /tmp/designstudio-single-gpu.XXXXXX)"
python3 cloud/eight-mi300x-campaign/campaign.py \
  --config cloud/eight-mi300x-campaign/campaign.single-gpu.mock.json \
  --run-directory "$run_dir" --mock
python3 cloud/eight-mi300x-campaign/verify_artifacts.py \
  --directory "$run_dir" --write "$run_dir/artifact-inventory.json"
python3 cloud/eight-mi300x-campaign/verify_artifacts.py \
  --directory "$run_dir" --verify "$run_dir/artifact-inventory.json"
```

The campaign is not authorized to create `CarBodyBaseline` merely because a
fit ran. `fit_glb_bspline_surfaces.py` performs deterministic curvature
segmentation, regularized tensor-product cubic B-spline control-grid fitting,
and produces a deviation heatmap. `reconstruct_bspline_fit_freecad.py`
constructs exact FreeCAD surfaces and performs AP242 export/re-import. Release
remains closed until all density ablations, G0/G1/G2 edge checks, deviation
limits, valid closed shell, and AP242 round-trip pass.

`Dockerfile.mfem-hip` pins MFEM 4.9 commit
`d9d6526cc1749980a2ba1da16e2c1ca1e07d82ec` and targets `gfx942`.
`convergence_gate.py` rejects fewer than three mesh levels and requires the
last two levels to agree within 5% for displacement, strain energy, maximum
temperature, and thermal resistance. It also requires p95 von Mises stress,
factor of safety, and six positive modes.

## Production gate and closeout

On the host, run `hardware_gate.py` before any model or solver work. It
requires exactly one MI300X device, at least 180 GB HBM, one render node, ROCm
health/topology, and a matrix burn-in. Invoke it with `--expected-gpus 1`.
Any failure is a campaign abort and immediate destruction condition.

Closeout is intentionally local and requires both the IP and exact numeric
Droplet ID:

```sh
python3 cloud/eight-mi300x-campaign/remote_closeout.py \
  --host DROPLET_IP --droplet-id NUMERIC_ID \
  --remote-run-directory /workspace/run \
  --local-directory /secure/local/campaign-run \
  --destroy
```

It creates a remote exhaustive SHA-256 inventory, downloads with resumable
`rsync`, compares every local file, invokes `doctl ... delete` only after a
zero-mismatch verification, and confirms the exact ID is absent from the
provider list. If `DIGITALOCEAN_ACCESS_TOKEN` is missing, it stops and emits a
manual-destruction alert instead of claiming success. Powered-off Droplets are
not used as a pause mechanism.

Generated car and component meshes are private, review-required evidence.
AI/Qwen output is proposal-only. Supplier/deterministic AP242, PCB tools,
FreeCAD/OCCT, and MFEM remain the only engineering authorities.

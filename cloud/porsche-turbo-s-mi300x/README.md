# 2026 911 Turbo S MI300X run

This deployment runs Hunyuan3D-2.1 and Hunyuan3D-Omni sequentially on one
MI300X. The services deliberately use separate Compose profiles so both model
stacks cannot contend for GPU memory.

Before provisioning, `references/porsche-911-turbo-s/reference-audit.json`
must contain `"bootstrap_compute_allowed": true`. On the host, verify
`rocminfo`, `torch.version.hip`, and `cloud/hunyuan-omni-amd/rocm_gate.py`
before downloading weights.

Copy `.env.example` to `.env`, populate non-default secrets, then run:

```sh
docker compose --env-file cloud/porsche-turbo-s-mi300x/.env \
  -f cloud/porsche-turbo-s-mi300x/docker-compose.yml \
  --profile shape up -d --build
curl http://127.0.0.1:18001/v1/health
python3 tools/run_turbo_s_generation.py bootstrap \
  --endpoint http://127.0.0.1:18001/v1/generate3d
```

The runner stores the decoded mesh and hash-bound response under
`references/porsche-911-turbo-s/generation/`. Render that mesh into
front/rear/left/right/top/bottom PNGs, correct them against the approved
references, and record their approved revisions in the study manifest.

Only after rebuilding the audit and seeing `"omni_generation_allowed": true`,
stop the shape profile before starting Omni:

```sh
docker compose -f cloud/porsche-turbo-s-mi300x/docker-compose.yml \
  --profile shape down
docker compose --env-file cloud/porsche-turbo-s-mi300x/.env \
  -f cloud/porsche-turbo-s-mi300x/docker-compose.yml \
  --profile omni up -d --build
DS_HUNYUAN_API_TOKEN="$(sed -n 's/^DS_HUNYUAN_API_TOKEN=//p' \
  cloud/porsche-turbo-s-mi300x/.env)" \
python3 tools/run_turbo_s_generation.py omni \
  --endpoint http://127.0.0.1:18002/v1/jobs
```

Submit exactly four candidates with seeds 200–203. Artifacts are candidates,
not CAD authority; they must pass the local scoring and FreeCAD baseline gates.
For the bounded eight-GPU campaign, retrieval is followed immediately by
hash verification and automatic Droplet destruction; review happens from the
downloaded evidence. Standalone exploratory runs still require an explicit
owner decision before destruction.

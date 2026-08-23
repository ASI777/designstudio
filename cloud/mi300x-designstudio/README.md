# DesignStudio on one AMD MI300X

This deployment keeps three responsibilities separate:

- `vllm` runs Qwen as the tool-calling design orchestrator.
- `support-optimizer` uses PyTorch tensors on ROCm to suggest cavity/rib paths.
- `gateway` exposes the bounded API and never grants CAD mutation authority.

The gateway binds to the droplet's loopback interface only. Connect from the
desktop through an SSH tunnel:

```sh
ssh -N -L 8000:127.0.0.1:18000 USER@DROPLET_IP
curl http://127.0.0.1:8000/v1/health
```

On the droplet, from the repository root:

```sh
cp cloud/mi300x-designstudio/.env.example cloud/mi300x-designstudio/.env
# Put the Hugging Face token in .env if the selected model requires it.
docker compose --env-file cloud/mi300x-designstudio/.env \
  -f cloud/mi300x-designstudio/docker-compose.yml up -d --build
docker compose -f cloud/mi300x-designstudio/docker-compose.yml ps
```

The first start downloads model weights and is not ready merely because the
containers are running. Poll `/v1/health`, then send a small `/v1/chat`
request. A support result remains a non-authoritative graph until the desktop
checks its document digest, reconstructs exact B-Rep, and passes collision,
manufacturing, structural-screening and assembly-order gates.

The vLLM service uses the official `vllm/vllm-openai-rocm` image. Its ROCm
device flags follow the official vLLM Docker deployment guidance.

## Bounded rib/pocket qualification

`rib-pocket-experiment-request-v2.json` is the retained placement-driven
qualification input. It binds five locked hardware placements, four protected
pockets, eight load/support anchors, and injection-moulding limits to
`rib-pocket-experiment-design-v2.json` by SHA-256.

The support optimizer is built from the pinned ROCm 6.4.2 / PyTorch 2.5.1 base,
not a mutable `latest` vLLM image. A cloud result remains a neutral suggestion:

```sh
python3 tools/validate_rib_pocket_experiment.py \
  --request cloud/mi300x-designstudio/rib-pocket-experiment-request-v2.json \
  --result cloud/mi300x-designstudio/evidence/rib-pocket-20260726/result-v2-a.json \
  --output-directory cloud/mi300x-designstudio/evidence/rib-pocket-20260726/freecad-v2 \
  --freecadcmd /path/to/FreeCADCmd
```

The validator reconstructs separate editable cavity and rib B-Reps, checks
exact rib/pocket intersections and manufacturing dimensions, exports AP242,
and re-imports the STEP file. It deliberately leaves structural screening and
assembly-order acceptance unresolved. See
`evidence/rib-pocket-20260726/README.md` for the measured run.

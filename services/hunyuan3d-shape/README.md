# Hunyuan3D-2.1 shape fine-tuning on MI300X

This service intentionally excludes Hunyuan3D-Paint. Dimensional evaluation
needs shape geometry, while Paint adds CUDA-specific rasterizer dependencies
that are unrelated to the target.

## Build and run the mandatory gate

Build from the repository root:

```bash
docker build -f services/hunyuan3d-shape/Dockerfile.rocm \
  -t design-studio-hunyuan3d-rocm .
```

Run on an MI300X host:

```bash
docker run --rm -it \
  --device=/dev/kfd --device=/dev/dri --group-add video \
  --ipc=host --shm-size 64g \
  -v "$PWD/datasets:/workspace/datasets" \
  design-studio-hunyuan3d-rocm \
  python /opt/design-studio/go_no_go.py \
    --image /workspace/datasets/hunyuan3d/procedural/preprocessed/ipc-000000/render_cond/000.png
```

Do not start paid training unless this prints `GO` and emits a valid,
watertight GLB. ROCm's PyTorch compatibility API still uses the `cuda` device
name; `torch.version.hip` is the check that distinguishes ROCm.

## Fine-tune

The config uses Tencent's released trainer's native PEFT hook to inject rank-64
LoRA matrices into self-attention, cross-attention, and MLP projections. The
VAE and image conditioner remain frozen. `lora_checkpoint.py` writes
adapter-only checkpoints that the gateway can load with `DS_3D_LORA`.
`cad_data.py` replaces the
upstream loader's hardcoded 24-image assumption with the available 1-4 RGBA
CAD views; all other sampling and augmentation behavior stays upstream. The
config builder also materializes disjoint train/validation file lists from each
part's `dimensions.json`, preventing held-out procedural samples from leaking
into training.

```bash
docker run --rm -it \
  --device=/dev/kfd --device=/dev/dri --group-add video \
  --ipc=host --shm-size 64g \
  -v "$DESIGNSTUDIO_DATA_ROOT/hunyuan3d/procedural:/workspace/dataset" \
  -v "$PWD/checkpoints:/workspace/checkpoints" \
  -e DATASET_ROOT=/workspace/dataset \
  -e DS_LORA_RANK=64 \
  design-studio-hunyuan3d-rocm \
  bash /opt/design-studio/train_mi300x.sh
```

The image is pinned to AMD's ROCm 6.4.2 / Ubuntu 22.04 / Python 3.10 /
PyTorch 2.5.1 base, code commit `82920d643c0d`, model revision
`0b94677654c5`, and the exact Python versions in `requirements.lock.txt`.
`source.lock.json` records the complete source archive digest. A build must
record its final container digest in the generation manifest.

`precompute_latents.py` can cache ShapeVAE outputs for dataset QA or a custom
latent-aware loader. Tencent's released `AlignedShapeLatentModule` reads
surface samples and encodes them during training, so the stock training path
does not consume these cache files. Integrating a latent cache requires a
matching data-module change and must not be claimed as an optimization until
that path is benchmarked.

## Serve

The same image starts the gateway by default. In production:

```bash
docker run --rm \
  --device=/dev/kfd --device=/dev/dri --group-add video \
  --ipc=host --shm-size 32g \
  -e DS_AI_MOCK=0 \
  -p 8000:8000 design-studio-hunyuan3d-rocm
```

`POST /v1/generate3d` accepts a base64 conditioning image and explicit overall
X/Y/Z bounds in millimetres, returns GLB, and performs deterministic anisotropic
scale recovery after diffusion. Local mock mode returns a dimensioned binary
STL without loading a model.

Set `DS_3D_LORA=/workspace/checkpoints/.../adapters/final` when serving a
fine-tuned adapter. The backend merges it into the base DiT once at lazy load.

Hunyuan model/code use is governed by Tencent's Hunyuan license. Verify that
the intended commercial and deployment use is allowed before training.

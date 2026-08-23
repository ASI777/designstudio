# Hunyuan3D-Omni 2.1 integration on AMD MI300X

This service owns asynchronous exterior-form and complex electronic-component
mesh generation. The
source archive is pinned by commit and SHA-256 in `source.lock.json`. Its ROCm
container uses the ROCm PyTorch base instead of the upstream CUDA 12.4 wheel and
omits `cupy-cuda12x` and NVIDIA runtime wheels. The upstream pipeline still uses
PyTorch's `torch.cuda` API; ROCm deliberately implements that API over HIP.

The service accepts a controlled design prompt, six directional image
references, exact Width x Depth x Height dimensions, point controls, voxel
keep-outs, symmetry intent, protected regions, candidate count and unique
seeds. Jobs are bounded and cancellable. A restart marks any
orphaned queued/running record `WORKER_LOST` so the pinned request can be
resubmitted without pretending the interrupted attempt succeeded. The worker
also enforces a per-job wall-time budget between candidates. Large GLBs are read
through one-hour HMAC-signed artifact URLs rather than the JSON control channel.
The artifact manifest records model/container digests, input digest, seed,
bounding-box error, control adherence and mesh diagnostics.

## Six-view input contract

Submit exactly one `front`, `rear`, `left`, `right`, `top` and `bottom` view.
Each must be an orthographic image with the object centered at the same scale.
Use a transparent PNG, or provide a separate `mask_url` whose white pixels are
the object and black pixels are background. Perspective images and opaque
images without a mask are rejected because they cannot define a controlled
visual hull.

The desktop component pipeline does not need to publish local images. It may
send `image_base64`/`mask_base64` together with their SHA-256 fields instead of
the signed HTTPS fields. Embedded inputs must be PNG and are capped at 16 MiB
each. The six-view uniqueness, silhouette and dimension rules are identical.

World axes are fixed: X is width, Y is depth and Z is height. Front/rear masks
constrain Width x Height, left/right constrain Depth x Height, and top/bottom
constrain Width x Depth. Opposing views are mirrored by the adapter. Dimensions
are never inferred from prompt wording:

```json
{
  "configuration_id": "992bb75c-cfe8-4f5a-8bcd-91cdcebe3b44",
  "slot_id": "slot.upper_shell",
  "candidate_count": 2,
  "seeds": [101, 202],
  "input_package": {
    "prompt": "Compact weather-resistant exterior with a continuous upper surface.",
    "dimensions_mm": {"width": 160, "depth": 120, "height": 80},
    "reference_images": [
      {"view": "front", "image_url": "https://signed.example/front.png"},
      {"view": "rear", "image_url": "https://signed.example/rear.png"},
      {"view": "left", "image_url": "https://signed.example/left.png"},
      {"view": "right", "image_url": "https://signed.example/right.png"},
      {"view": "top", "image_url": "https://signed.example/top.png"},
      {"view": "bottom", "image_url": "https://signed.example/bottom.png"}
    ]
  }
}
```

## Datasheet component path

GPT-5.6 Luna with `xhigh` reasoning reads the selected package/mechanical
datasheet pages and emits a millimetre-scale construction plan. Simple plans
become deterministic local GLBs. For a plan marked complex, DesignStudio keeps
that GLB as a visible proxy, renders exactly six orthographic views, and calls
this service. Configure the desktop process with:

```sh
export DESIGNSTUDIO_HUNYUAN_OMNI_URL=https://hunyuan.example.internal
export DESIGNSTUDIO_HUNYUAN_OMNI_TOKEN=replace-with-secret
```

The returned GLB is digest-checked, attached to the exact footprint instance,
and shown on the completed PCB in FreeCAD. A generated GLB remains visualization
evidence; it does not acquire the manufacturing authority of a verified STEP
binding.

The pinned upstream pipeline has a `prompt` argument, but commit `4d47c0cc`
does not read it. It also treats a list of images as independent batch items.
DesignStudio therefore fuses the six silhouettes into one native voxel control
and uses the front image for appearance conditioning. The prompt is retained in
the input digest and evidence, but artifacts explicitly report
`TEXT_PROMPT_NOT_CONDITIONED` and remain release-blocked. True semantic prompt
conditioning requires a separately trained Omni text/multi-view adapter; it is
not enabled by passing text into the current checkpoint.

Run the deterministic contract suite without a GPU:

```sh
DS_HUNYUAN_MOCK=1 python3 cloud/hunyuan-omni-amd/test_service.py
python3 cloud/hunyuan-omni-amd/test_multiview.py
```

For MI300X qualification, build the pinned image, set non-default API/signing
secrets and immutable model/container digests, then run `rocm_gate.py`. First
prove bbox, point and voxel controls individually with optional acceleration
disabled. FlashVDM or other acceleration is a later parity gate. Generated GLB
is always a candidate; local FreeCAD reconstruction and deterministic interface
substitution remain mandatory before manufacturing release.

The upstream model/code uses the Tencent Hunyuan 3D Omni Community License.
Commercial distribution therefore remains blocked until the formal model-use
and transitive-license review is signed off.

Selected artifacts cross back into `DesignStudio.reconstruction` in the local
FreeCAD workbench. The imported mesh is retained but hidden as evidence; exact
dimensions drive a new editable B-Rep enclosure, and walls, split, gasket,
bosses, openings and legal PCB volume are deterministic substitutions. Every
reference silhouette must clear its explicit threshold before reconstruction.

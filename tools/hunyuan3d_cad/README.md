# Hunyuan3D CAD dataset pipeline

This pipeline separates geometry learning from absolute scale:

1. tessellate STEP in millimetres with Open CASCADE;
2. preserve the original bounding box in metadata;
3. center and normalize the longest axis to 1.0 for Hunyuan3D;
4. produce the directory layout expected by Hunyuan3D-2.1;
5. restore an inferred mesh to explicit X/Y/Z bounds after generation.

The files found in `~/Downloads` are a held-out evaluation set. The current
inventory contains 14 distinct parts: four UltraLibrarian package models, one
Analog Devices evaluation board, eight ESP32-project models, and one Tensility
audio jack. STEP/STL pairs count as one part.

## Build the STEP tessellator

```bash
cmake -S tools/hunyuan3d_cad -B /tmp/hy3d-cad-build -DCMAKE_BUILD_TYPE=Release
cmake --build /tmp/hy3d-cad-build -j
```

## Prepare the held-out set

```bash
python tools/hunyuan3d_cad/cad_pipeline.py inventory \
  --downloads "$HOME/Downloads" \
  --output "$DESIGNSTUDIO_DATA_ROOT/hunyuan3d/seed_eval" \
  --step-converter /tmp/hy3d-cad-build/step_to_stl \
  --split eval
```

The manifest records SHA-256 hashes, source-unit evidence, true bounds,
normalization transforms, conditioning images, and topology diagnostics. Some
vendor assemblies are closed collections of solids but non-manifold as one
triangle mesh. That is acceptable for held-out surface evaluation; do not move
those parts into training without running Tencent's watertight remesher.

## Build training data

Generate deterministic IPC-inspired SOIC, QFN, and two-terminal packages:

```bash
python tools/hunyuan3d_cad/cad_pipeline.py generate-ipc \
  --output "$DESIGNSTUDIO_DATA_ROOT/hunyuan3d/procedural" \
  --count 3000 \
  --surface-samples 81920
```

Every procedural part is generated from exact dimensions and includes four
518 px RGBA conditioning renders, an OBJ/STL, surface samples, body dimensions,
overall bounds, and pin pitch.

An existing licensed CAD tree can be added with:

```bash
python tools/hunyuan3d_cad/cad_pipeline.py inventory \
  --source-tree /data/kicad-packages3D \
  --output /data/hunyuan-kicad \
  --step-converter /tmp/hy3d-cad-build/step_to_stl \
  --split train \
  --license CC-BY-SA-4.0
```

KiCad's package library is CC-BY-SA-4.0 with a design-output exception, not a
generic permissive license. Keep its license and provenance with the dataset,
and obtain legal guidance before distributing weights trained on the library.
UltraLibrarian/SnapEDA files also require source-specific redistribution and
ML-training rights; possession of a downloaded model is not sufficient.

## Scale and evaluate

`target_dimensions_mm` means the final overall mesh bounds. Body-only
dimensions from `design-studio.component/2` cannot correctly scale leaded
packages unless the generated body is segmented or an overall dimension is
also known.

```bash
python tools/hunyuan3d_cad/cad_pipeline.py scale normalized.stl scaled.stl \
  --dimensions-mm 10.0 6.0 2.0

python tools/hunyuan3d_cad/cad_pipeline.py evaluate reference.stl scaled.stl \
  --threshold-mm 0.10 --output metrics.json

python tools/hunyuan3d_cad/cad_pipeline.py evaluate-manifest \
  --manifest "$DESIGNSTUDIO_DATA_ROOT/hunyuan3d/seed_eval/eval_manifest.json" \
  --predictions-root /data/predictions \
  --variants pretrained finetuned \
  --output /data/evaluation.json
```

The evaluator reports symmetric surface Chamfer-L1, F-score, and per-axis
bounding-box error. Pin-pitch evaluation should use the exact procedural
metadata or manually annotated vendor landmarks; inferring pitch from an
arbitrary assembly mesh would make the benchmark unreliable.

## Tests

```bash
PYTHONPATH=tools/hunyuan3d_cad \
  python -m unittest tools/hunyuan3d_cad/test_cad_pipeline.py
```

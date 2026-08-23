#!/usr/bin/env bash
set -euo pipefail

: "${DATASET_ROOT:?set DATASET_ROOT to the uploaded Hunyuan dataset}"
: "${OUTPUT_DIR:=/workspace/checkpoints/hunyuan3d-cad-lora}"
: "${HUNYUAN3D_ROOT:=/opt/Hunyuan3D-2.1}"
: "${DS_LORA_RANK:=64}"

CONFIG="${OUTPUT_DIR}/mi300x-lora.yaml"
mkdir -p "${OUTPUT_DIR}"
python /opt/design-studio/prepare_training.py \
  --upstream "${HUNYUAN3D_ROOT}/hy3dshape/configs/hunyuandit-finetuning-flowmatching-dinol518-bf16-lr1e5-4096.yaml" \
  --dataset "${DATASET_ROOT}" \
  --output "${CONFIG}" \
  --lora-rank "${DS_LORA_RANK}"

export HIP_VISIBLE_DEVICES=0
export CUDA_VISIBLE_DEVICES=0
export DS_LORA_RANK
export NCCL_DEBUG=WARN
cd "${HUNYUAN3D_ROOT}/hy3dshape"
python main.py \
  --num_nodes 1 \
  --num_gpus 1 \
  --config "${CONFIG}" \
  --output_dir "${OUTPUT_DIR}" \
  --deepspeed

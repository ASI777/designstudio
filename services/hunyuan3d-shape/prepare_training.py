#!/usr/bin/env python3
"""Create a single-MI300X LoRA config from Tencent's released fine-tune config."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=20_000)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--lora-rank", type=int, default=64)
    args = parser.parse_args()

    config = yaml.safe_load(args.upstream.read_text(encoding="utf-8"))
    config["training"]["steps"] = args.steps
    config["training"]["base_lr"] = args.learning_rate
    config["training"]["every_n_train_steps"] = min(1000, args.steps)
    config["training"]["val_check_interval"] = min(500, args.steps)
    params = config["dataset"]["params"]
    config["dataset"]["target"] = "cad_data.CadAlignedShapeLatentModule"
    params["batch_size"] = args.batch_size
    params["num_workers"] = 8
    parts = sorted(path for path in (args.dataset / "preprocessed").iterdir() if path.is_dir())
    train_parts: list[str] = []
    validation_parts: list[str] = []
    for index, part in enumerate(parts):
        metadata_path = part / "dimensions.json"
        split = None
        if metadata_path.exists():
            split = json.loads(metadata_path.read_text(encoding="utf-8")).get("split")
        if split == "validation" or (split is None and index % 20 == 0):
            validation_parts.append(str(part))
        else:
            train_parts.append(str(part))
    if not train_parts or not validation_parts:
        raise SystemExit("dataset must contain non-empty train and validation splits")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    train_list = args.output.with_name("train-files.json")
    validation_list = args.output.with_name("validation-files.json")
    train_list.write_text(json.dumps(train_parts, indent=2) + "\n", encoding="utf-8")
    validation_list.write_text(json.dumps(validation_parts, indent=2) + "\n", encoding="utf-8")
    params["train_data_list"] = str(train_list)
    params["val_data_list"] = str(validation_list)
    config["model"]["params"]["lora_config"] = {
        "rank": args.lora_rank,
        "target_modules": ["to_q", "to_k", "to_v", "out_proj", "fc1", "fc2"],
    }
    config["model"]["params"]["optimizer_cfg"]["optimizer"]["params"]["weight_decay"] = 0.0
    config.setdefault("callbacks", {})["lora_adapter_checkpoint"] = {
        "target": "lora_checkpoint.LoraAdapterCheckpoint",
        "params": {"output_dir": str(args.output.parent / "adapters")},
    }

    args.output.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()

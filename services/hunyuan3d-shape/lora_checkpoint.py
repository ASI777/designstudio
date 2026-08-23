"""Export adapter-only PEFT checkpoints from the upstream Lightning trainer."""

from __future__ import annotations

from pathlib import Path

from pytorch_lightning.callbacks import Callback


class LoraAdapterCheckpoint(Callback):
    def __init__(self, output_dir: str):
        super().__init__()
        self.output_dir = Path(output_dir)
        self._last_step = -1

    def _save(self, trainer, pl_module, label: str) -> None:
        if trainer.global_rank != 0 or trainer.global_step == self._last_step:
            return
        model = getattr(pl_module, "model", None)
        if model is None or not hasattr(model, "save_pretrained"):
            raise RuntimeError("LoRA adapter export expected a PEFT model")
        destination = self.output_dir / label
        destination.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(destination, safe_serialization=True)
        self._last_step = trainer.global_step

    def on_validation_end(self, trainer, pl_module) -> None:
        self._save(trainer, pl_module, f"step-{trainer.global_step:08d}")

    def on_train_end(self, trainer, pl_module) -> None:
        self._save(trainer, pl_module, "final")

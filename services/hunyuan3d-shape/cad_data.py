"""Hunyuan training data adapter for 1-4 CAD conditioning renders."""

from __future__ import annotations

import glob
import json
import os

import torch
from hy3dshape.data.dit_asl import AlignedShapeLatentDataset, AlignedShapeLatentModule
from hy3dshape.data.utils import worker_init_fn


class CadAlignedShapeLatentDataset(AlignedShapeLatentDataset):
    def __init__(self, data_list=None, **kwargs):
        if isinstance(data_list, str) and data_list.endswith(".json"):
            with open(data_list, encoding="utf-8") as stream:
                data_list = json.load(stream)
        super().__init__(data_list=data_list, **kwargs)

    def decode(self, item):
        uid = os.path.basename(item.rstrip("/"))
        render_img_paths = sorted(glob.glob(os.path.join(item, "render_cond", "*.png")))
        if not render_img_paths:
            raise FileNotFoundError(f"{uid}: no conditioning PNGs")
        surface_path = os.path.join(item, "geo_data", f"{uid}_surface.npz")
        surface_data = self.read_surface(surface_path)
        return {
            "image": render_img_paths,
            "random_surface": surface_data["random_surface"],
            "sharpedge_surface": surface_data["sharp_surface"],
        }

    @staticmethod
    def read_surface(path):
        import numpy as np

        return np.load(path)


class CadAlignedShapeLatentModule(AlignedShapeLatentModule):
    def _loader(self, data_list, workers, image_transform):
        params = {
            "data_list": data_list,
            "cond_stage_key": self.cond_stage_key,
            "image_transform": image_transform,
            "pc_size": self.pc_size,
            "pc_sharpedge_size": self.pc_sharpedge_size,
            "sharpedge_label": self.sharpedge_label,
            "return_normal": self.return_normal,
            "padding": self.padding,
            "padding_ratio_range": self.padding_ratio_range,
        }
        dataset = CadAlignedShapeLatentDataset(**params)
        return torch.utils.data.DataLoader(
            dataset,
            batch_size=self.batch_size,
            num_workers=workers,
            pin_memory=True,
            drop_last=True,
            worker_init_fn=worker_init_fn,
        )

    def train_dataloader(self):
        return self._loader(self.train_data_list, self.num_workers, self.train_image_transform)

    def val_dataloader(self):
        return self._loader(self.val_data_list, self.val_num_workers, self.val_image_transform)

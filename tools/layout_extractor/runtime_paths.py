"""Runtime paths shared by layout-extractor command-line tools.

Mutable data never defaults to the source checkout. Repository `testdata` is a
read-only convenience for deterministic examples.
"""

from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _xdg_root(variable: str, fallback: Path) -> Path:
    value = os.environ.get(variable)
    return Path(value).expanduser() if value else fallback


def data_root() -> Path:
    explicit = os.environ.get("DESIGNSTUDIO_DATA_ROOT")
    if explicit:
        return Path(explicit).expanduser()
    base = _xdg_root("XDG_DATA_HOME", Path.home() / ".local" / "share")
    return base / "designstudio" / "datasets"


def artifact_root() -> Path:
    explicit = os.environ.get("DESIGNSTUDIO_ARTIFACT_ROOT")
    if explicit:
        return Path(explicit).expanduser()
    base = _xdg_root("XDG_STATE_HOME", Path.home() / ".local" / "state")
    return base / "designstudio" / "artifacts"


def layout_output_dir() -> Path:
    path = artifact_root() / "layout_extractor"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sample_datasheet(name: str = "usb4910.pdf") -> Path:
    return REPO_ROOT / "testdata" / "datasheets" / "source" / name


def sample_layout_fixture(name: str) -> Path:
    return REPO_ROOT / "testdata" / "layout_extractor" / "usb4910" / name

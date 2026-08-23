"""Canonical mutable-data and immutable-fixture locations for the swarm."""

from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def data_root() -> Path:
    explicit = os.environ.get("DESIGNSTUDIO_DATA_ROOT")
    if explicit:
        return Path(explicit).expanduser()
    xdg = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return xdg / "designstudio" / "datasets"


def component_library_dir() -> Path:
    explicit = os.environ.get("DESIGNSTUDIO_LIBRARY_ROOT")
    return Path(explicit).expanduser() if explicit else data_root() / "components"


def component_library_db_path() -> Path:
    """Authoritative component/datasheet/CAD library database."""
    explicit = os.environ.get("DESIGNSTUDIO_COMPONENT_DB")
    return Path(explicit).expanduser() if explicit else data_root() / "component-library.sqlite3"


def bound_component_library_dir() -> Path:
    explicit = os.environ.get("DESIGNSTUDIO_BOUND_LIBRARY_ROOT")
    return Path(explicit).expanduser() if explicit else data_root() / "bound-components"


def component_asset_library_dir() -> Path:
    """Generated, non-authoritative component visualization assets.

    These assets are deliberately separate from verified STEP bindings.  A GLB
    generated from datasheet diagrams can be shown on the assembled PCB without
    accidentally acquiring the release authority of an exact supplier model.
    """
    explicit = os.environ.get("DESIGNSTUDIO_COMPONENT_ASSET_ROOT")
    return Path(explicit).expanduser() if explicit else data_root() / "component-3d-assets"


def component_fixture_dir() -> Path:
    return REPO_ROOT / "testdata" / "component-library"

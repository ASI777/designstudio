#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from swarm.memory.component_binding import (  # noqa: E402
    BindingError, BoundComponentStore, bind_component, validate_binding,
)
WB = ROOT / "freecad" / "DesignStudioWorkbench"
sys.path.insert(0, str(WB))
from DesignStudio.freecad_adapter import component_world_transform  # noqa: E402


def component2():
    return {
        "schema": "design-studio.component/2",
        "component": {"manufacturer": "Fixture", "mpn": "REG-5P",
                      "category": "regulator", "description": "test regulator"},
        "symbol": {"ref_des_prefix": "U", "pins": [
            {"number": "1", "name": "IN", "electrical_type": "power_in"},
            {"number": "2", "name": "GND", "electrical_type": "power_in"},
            {"number": "3", "name": "EN", "electrical_type": "input"},
            {"number": "4", "name": "OUT", "electrical_type": "power_out"},
            {"number": "5", "name": "EP", "electrical_type": "power_in"},
        ]},
        "electrical": {
            "supply": {"vmin_v": 2.5, "vmax_v": 5.5, "max_current_a": 0.5},
            "power_domains": [{"name": "VIN", "vmin_v": 2.5, "vnom_v": 3.3,
                               "vmax_v": 5.5, "max_current_a": 0.5, "pins": ["1"]}],
            "required_externals": [],
            "parameters": [{"name": "quiescent current", "typ": 0.00001,
                            "min": None, "max": None, "unit": "A", "condition": "enabled"}],
        },
        "footprint": {
            "name": "QFN-5", "mount": "smd", "courtyard_margin_mm": 0.25,
            "body": {"width_mm": 3.0, "length_mm": 3.0, "height_mm": 0.8},
            "pads": [
                {"number": "1", "x_mm": -1, "y_mm": 1, "width_mm": 0.5, "height_mm": 0.7},
                {"number": "2", "x_mm": 1, "y_mm": 1, "width_mm": 0.5, "height_mm": 0.7},
                {"number": "3", "x_mm": 1, "y_mm": -1, "width_mm": 0.5, "height_mm": 0.7},
                {"number": "4", "x_mm": -1, "y_mm": -1, "width_mm": 0.5, "height_mm": 0.7},
                {"number": "5", "x_mm": 0, "y_mm": 0, "width_mm": 1.2, "height_mm": 1.2},
                {"number": "MH1", "x_mm": -2, "y_mm": 0, "width_mm": 0.6,
                 "height_mm": 1.0, "mechanical": True},
            ],
        },
        "package_3d": {"height_mm": 0.8, "standoff_mm": 0.05, "shape": "box"},
        "orientation": {"pin1_marker": "dot", "pin1_position": "top-left"},
        "evidence": {"schema": "design-studio.datasheet-evidence/1", "sha256": "a" * 64,
                     "package_variant": "QFN-5", "package_pin_count": 5},
    }


def write_step(path: Path):
    path.write_text(
        "ISO-10303-21;\nHEADER;FILE_DESCRIPTION(('fixture'),'2;1');ENDSEC;\n"
        "DATA;#1=CARTESIAN_POINT('',(0.,0.,0.));ENDSEC;END-ISO-10303-21;\n",
        encoding="ascii")


def test_complete_binding_and_store():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        step = tmp_path / "REG-5P.step"
        write_step(step)
        binding = bind_component(component2(), step, alignment_status="verified", model_mpn="REG-5P",
                                 source_uri="fixture/component.json")
        validate_binding(binding)
        assert binding["status"] == "complete"
        assert len(binding["pin_pad_map"]) == 5
        assert all(mapping["pad_ids"] for mapping in binding["pin_pad_map"])
        assert not any("pad:6" in mapping["pad_ids"] for mapping in binding["pin_pad_map"])
        assert binding["footprint"]["courtyard_pts"] == [
            [-2.55, -1.75], [1.75, -1.75], [1.75, 1.75], [-2.55, 1.75]]
        store = BoundComponentStore(tmp_path / "library")
        saved = store.save(binding)
        loaded = store.get("REG-5P")
        assert saved.is_file() and loaded is not None
        assert loaded["binding_digest"] != binding["binding_digest"]  # portable URI changes digest
        assert (store.root / loaded["model_3d"]["asset_uri"]).is_file()


def test_pin_pad_mismatch_and_fake_step_fail_closed():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        step = tmp_path / "valid.step"
        write_step(step)
        missing = component2()
        missing["footprint"]["pads"] = [pad for pad in missing["footprint"]["pads"]
                                          if pad["number"] != "4"]
        try:
            bind_component(missing, step, alignment_status="verified", model_mpn="REG-5P")
        except BindingError as exc:
            assert any("pin 4" in issue for issue in exc.issues)
        else:
            raise AssertionError("missing electrical pad was accepted")

        extra = component2()
        extra["footprint"]["pads"].append(
            {"number": "99", "x_mm": 3, "y_mm": 0, "width_mm": 0.5, "height_mm": 0.5})
        try:
            bind_component(extra, step, alignment_status="verified", model_mpn="REG-5P")
        except BindingError as exc:
            assert any("pad 99" in issue for issue in exc.issues)
        else:
            raise AssertionError("electrical pad absent from symbol was accepted")

        fake = tmp_path / "fake.step"
        fake.write_text("this is not STEP", encoding="utf-8")
        try:
            bind_component(component2(), fake, alignment_status="verified", model_mpn="REG-5P")
        except BindingError as exc:
            assert any("ISO-10303-21" in issue for issue in exc.issues)
        else:
            raise AssertionError("fake STEP was accepted")


def test_tampered_binding_or_asset_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        step = tmp_path / "part.step"
        write_step(step)
        binding = bind_component(component2(), step, alignment_status="verified", model_mpn="REG-5P")
        tampered = copy.deepcopy(binding)
        tampered["electrical"]["supply"]["vmax_v"] = 50
        try:
            validate_binding(tampered)
        except BindingError as exc:
            assert any("digest mismatch" in issue for issue in exc.issues)
        else:
            raise AssertionError("tampered electrical model was accepted")

        store = BoundComponentStore(tmp_path / "library")
        store.save(binding)
        record = json.loads((store.bindings / "REG-5P.bound.json").read_text())
        model = store.root / record["model_3d"]["asset_uri"]
        model.write_text("ISO-10303-21;tampered", encoding="ascii")
        try:
            store.get("REG-5P")
        except BindingError as exc:
            assert any("asset digest mismatch" in issue for issue in exc.issues)
        else:
            raise AssertionError("tampered STEP asset was accepted")


def test_component_transform_composes_board_frame_and_placement():
    frame = {"origin_mm": [10, 20, 30], "x_axis": [0, 1, 0],
             "y_axis": [-1, 0, 0], "z_axis": [0, 0, 1]}
    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    world = component_world_transform(frame, 5, 2, 0, identity)
    # board point (5,2,0) -> origin + 5*x_axis + 2*y_axis = (8,25,30)
    assert [round(world[3], 6), round(world[7], 6), round(world[11], 6)] == [8, 25, 30]


if __name__ == "__main__":
    test_complete_binding_and_store()
    test_pin_pad_mismatch_and_fake_step_fail_closed()
    test_tampered_binding_or_asset_is_rejected()
    test_component_transform_composes_board_frame_and_placement()
    print("Bound component tests passed")

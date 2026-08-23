from __future__ import annotations

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from DesignStudio.mechanical_view_modes import classify_object, visibility_policy


class Object:
    def __init__(self, name, label="", role=""):
        self.Name = name
        self.Label = label
        self.DesignStudioRole = role


def test_semantic_modes_hide_reservations_by_default():
    reservation = classify_object(Object("COMP_RESERVATION", "camera.reservation.battery"))
    rib = classify_object(Object("Rib_1", "manufactured rib", "manufactured_rib"))
    assert reservation == "reservation"
    assert rib == "internal_structure"
    assert not visibility_policy("manufacturing_internals", reservation)
    assert visibility_policy("clearance", reservation)
    assert visibility_policy("manufacturing_internals", rib)
    assert not visibility_policy("product", rib)


if __name__ == "__main__":
    test_semantic_modes_hide_reservations_by_default()
    print("MECHANICAL_VIEW_MODES_OK")

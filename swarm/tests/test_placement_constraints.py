#!/usr/bin/env python3
import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AGENTS = os.path.abspath(os.path.join(HERE, "..", "agents"))
sys.path.insert(0, AGENTS)

from placement_constraints import apply_constraints, component_polygon, point_in_polygon, polygon_gap
from subsystem_placer_agent import apply_explicit_groups


def part(ref, x, y, placement=None, height=1.0):
    return {
        "ref": ref, "x_mm": x, "y_mm": y, "rot_deg": 0, "side": 0,
        "body_w_mm": 2.0, "body_h_mm": 2.0, "h3d_mm": height,
        "pads": [], "placement": placement or {},
    }


def board(*fps):
    return {"board_width_mm": 20.0, "board_height_mm": 20.0,
            "net_table": [], "footprints": list(fps), "rule_areas": []}


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def test_locked_and_halos():
    hot = part("Q1", 5, 5, {"locked": True, "thermal_power_w": 2.0,
                             "thermal_clearance_mm": 3.0})
    other = part("U1", 6, 5)
    data = board(hot, other)
    report = apply_constraints(data, [(5, 5, 0), (6, 5, 0)])
    check(report["ok"], report["errors"])
    check((hot["x_mm"], hot["y_mm"]) == (5, 5), "locked part moved")
    check(polygon_gap(component_polygon(hot), component_polygon(other)) >= 3.0 - 1e-6,
          "thermal halo was not legalized")


def test_edge_anchor_and_cutout():
    connector = part("J1", 10, 10, {"edge_anchor": "left"})
    data = board(connector)
    data["board_cutouts"] = [[[8, 8], [12, 8], [12, 12], [8, 12]]]
    report = apply_constraints(data)
    check(report["ok"], report["errors"])
    poly = component_polygon(connector)
    check(abs(min(p[0] for p in poly) - 0.25) < 1e-6, "left anchor is not at edge margin")
    check(not any(point_in_polygon(p, data["board_cutouts"][0]) for p in poly),
          "edge anchored part intersects board cutout")


def test_height_area_legalization():
    fp = part("C1", 10, 10, height=5.0)
    data = board(fp)
    data["rule_areas"] = [{"id": 2, "name": "lid-lip", "max_height_mm": 2.0,
                            "pts": [[7, 7], [13, 7], [13, 13], [7, 13]]}]
    report = apply_constraints(data)
    check(report["ok"], report["errors"])
    area = [(7, 7), (13, 7), (13, 13), (7, 13)]
    check(polygon_gap(component_polygon(fp), area) > 0, "tall part remained in low-height area")


def test_probe_access_from_edge():
    probe = part("TP1", 1.5, 1.5, {"test_access_required": True,
                                   "test_access_halo_mm": 2.0})
    data = board(probe)
    report = apply_constraints(data)
    check(report["ok"], report["errors"])
    poly = component_polygon(probe)
    check(min(p[0] for p in poly) >= 2.0 - 1e-6
          and min(p[1] for p in poly) >= 2.0 - 1e-6,
          "test point was not moved away from the board edge")


def test_unsatisfiable_locked_parts_fail_closed():
    a = part("U1", 5, 5, {"locked": True})
    b = part("U2", 5, 5, {"locked": True})
    data = board(a, b)
    before = copy.deepcopy(data)
    report = apply_constraints(data, [(5, 5, 0), (5, 5, 0)])
    check(not report["ok"] and "U2" in " ".join(report["errors"]),
          "conflicting locked placement did not fail")
    check(before["footprints"][0]["x_mm"] == data["footprints"][0]["x_mm"],
          "first locked part was changed")


def test_explicit_functional_group_override():
    fps = [part("U1", 1, 1, {"functional_group": "sensor"}),
           part("R1", 2, 2, {"functional_group": "SENSOR"}),
           part("J1", 3, 3)]
    labels = apply_explicit_groups(fps, {0: 0, 1: 1, 2: 2})
    check(labels[0] == labels[1] and labels[2] != labels[0],
          "explicit functional group did not override inferred clusters")


def test_bottom_side_uses_same_transform_as_step_assembly():
    top = part("U1", 10, 20)
    top["rot_deg"] = 90
    top["courtyard_pts"] = [[2, 1], [3, 1], [3, 2], [2, 2]]
    bottom = copy.deepcopy(top)
    bottom["side"] = 1
    top_poly = component_polygon(top)
    bottom_poly = component_polygon(bottom)
    check(top_poly[0] == (9.0, 22.0), "top footprint transform changed convention")
    check(bottom_poly[0] == (11.0, 22.0),
          "bottom footprint did not mirror local Y before rotation")


if __name__ == "__main__":
    test_locked_and_halos()
    test_edge_anchor_and_cutout()
    test_height_area_legalization()
    test_probe_access_from_edge()
    test_unsatisfiable_locked_parts_fail_closed()
    test_explicit_functional_group_override()
    test_bottom_side_uses_same_transform_as_step_assembly()
    print("Placement constraint tests passed")

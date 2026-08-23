from __future__ import annotations

import copy
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from DesignStudio.manufacturing_rules import (ManufacturingRuleError,
                                               check_feature, default_profile,
                                               recommended_rib, validate_profile)


def test_default_profile_and_recommendation():
    profile = default_profile()
    assert profile["schema"] == "design-studio.manufacturing-profile/1"
    dimensions = recommended_rib(profile)
    assert profile["rib"]["minimum_mm"] <= dimensions["thickness_mm"] <= profile["rib"]["maximum_mm"]
    assert dimensions["height_mm"] > dimensions["thickness_mm"]
    assert not check_feature({"id": "rib-a", "kind": "rib",
                              "thickness_mm": dimensions["thickness_mm"],
                              "draft_deg": dimensions["draft_deg"],
                              "root_fillet_mm": dimensions["root_fillet_mm"],
                              "clearance_mm": profile["clearance"]["component_mm"]}, profile)


def test_profile_and_feature_fail_closed():
    profile = default_profile()
    invalid = copy.deepcopy(profile)
    invalid["rib"]["ratio_to_wall"] = 1.5
    try:
        validate_profile(invalid)
        raise AssertionError("invalid ratio accepted")
    except ManufacturingRuleError:
        pass
    findings = check_feature({"id": "thin", "kind": "rib", "thickness_mm": 0.1,
                              "draft_deg": 0.0, "root_fillet_mm": 0.0,
                              "clearance_mm": 0.0}, profile)
    assert {item["code"] for item in findings} >= {
        "RIB_TOO_THIN", "DRAFT_TOO_LOW", "ROOT_FILLET_TOO_SMALL", "CLEARANCE_TOO_SMALL"
    }


if __name__ == "__main__":
    test_default_profile_and_recommendation()
    test_profile_and_feature_fail_closed()
    print("MANUFACTURING_RULES_OK")

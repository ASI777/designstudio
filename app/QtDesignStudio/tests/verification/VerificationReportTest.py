#!/usr/bin/env python3
import json
import hashlib
import os
import subprocess
import sys
import tempfile

binary, fixture = sys.argv[1:3]
with tempfile.TemporaryDirectory() as directory:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", DS_AI_MOCK="1")
    def run(project, name):
        output = os.path.join(directory, name + "-verification.json")
        result = subprocess.run([binary, "--smoke-test", "--verification-out", output, project],
                                env=env, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=30)
        if result.returncode:
            raise AssertionError(f"verification CLI failed ({result.returncode}):\n{result.stdout}")
        with open(output, encoding="utf-8") as handle:
            report = json.load(handle)
        return report

    report = run(fixture, "pass")
    assert report["schema"] == "design-studio.verification/1"
    assert report["overall_status"] == "pass", report
    categories = report["categories"]
    assert set(categories) == {"drc", "connectivity", "signal_integrity",
                               "power_integrity", "thermal", "mechanical"}
    for name in ("drc", "connectivity", "signal_integrity", "power_integrity", "mechanical"):
        assert categories[name]["status"] == "pass", (name, categories[name])
    assert categories["thermal"]["status"] == "not_applicable"
    assert categories["signal_integrity"]["metrics"]["target_classes"] == 1
    assert categories["signal_integrity"]["metrics"]["method"] == \
        "actual-route-screening-plus-closed-form-impedance"
    assert categories["power_integrity"]["metrics"]["specified_power_nets"] == 1

    with open(fixture, encoding="utf-8") as handle:
        wrapped_board = json.load(handle)
    wrapper = {"format": "design-studio.project/3", "units": "nm",
               "materials": [], "parts": [], "board": wrapped_board,
               "assembly": {"tree": [], "joints": []}, "constraints": [],
               "tolerances": [], "rationale": []}
    wrapper_path = os.path.join(directory, "authoritative-v3.dsproj")
    with open(wrapper_path, "w", encoding="utf-8") as handle:
        json.dump(wrapper, handle, sort_keys=True)
    wrapped = run(wrapper_path, "authoritative-v3")
    with open(wrapper_path, "rb") as handle:
        wrapper_sha = hashlib.sha256(handle.read()).hexdigest()
    assert wrapped["overall_status"] == "pass", wrapped
    assert wrapped["project"] == {
        "document_id": wrapped_board["document_id"],
        "revision": wrapped_board["revision"],
        "file_sha256": wrapper_sha,
    }

    with open(fixture, encoding="utf-8") as handle:
        incomplete_project = json.load(handle)
    incomplete_project["net_classes"][0]["z0_ohm"] = 0
    incomplete_project["net_table"][0]["required_current_a"] = 0
    incomplete_path = os.path.join(directory, "incomplete.dsproj")
    with open(incomplete_path, "w", encoding="utf-8") as handle:
        json.dump(incomplete_project, handle)
    incomplete = run(incomplete_path, "incomplete")
    assert incomplete["overall_status"] == "incomplete"
    assert incomplete["categories"]["signal_integrity"]["status"] == "incomplete"
    assert incomplete["categories"]["power_integrity"]["status"] == "incomplete"

    failed_project = json.loads(json.dumps(incomplete_project))
    failed_project["traces"][0]["net"] = -1
    failed_path = os.path.join(directory, "failed.dsproj")
    with open(failed_path, "w", encoding="utf-8") as handle:
        json.dump(failed_project, handle)
    failed = run(failed_path, "failed")
    assert failed["overall_status"] == "fail"
    assert failed["categories"]["drc"]["status"] == "fail"

    sharp_project = json.loads(json.dumps(incomplete_project))
    sharp_project["net_classes"][0]["z0_ohm"] = 50
    sharp_project["net_classes"][0]["signal_frequency_hz"] = 480000000
    sharp_project["net_classes"][0]["trace_width_mm"] = 0.367
    sharp_project["traces"] = [
        {"ax_mm":5,"ay_mm":10,"bx_mm":10,"by_mm":10,"w_mm":0.367,
         "net":0,"layer":0,"pour":False},
        {"ax_mm":10,"ay_mm":10,"bx_mm":15,"by_mm":10,"w_mm":0.2,
         "net":0,"layer":0,"pour":False},
        {"ax_mm":10,"ay_mm":10,"bx_mm":10,"by_mm":15,"w_mm":0.367,
         "net":0,"layer":0,"pour":False},
    ]
    sharp_path = os.path.join(directory, "sharp-high-speed-route.dsproj")
    with open(sharp_path, "w", encoding="utf-8") as handle:
        json.dump(sharp_project, handle)
    sharp = run(sharp_path, "sharp-high-speed-route")
    codes = {item["code"] for item in sharp["categories"]["signal_integrity"]["findings"]}
    assert sharp["categories"]["signal_integrity"]["status"] == "fail"
    assert "SI_ACTUAL_WIDTH_DISCONTINUITY" in codes
    assert "SI_RIGHT_ANGLE_CORNERS" in codes

    rebuild_project = json.loads(json.dumps(incomplete_project))
    rebuild_project["traces"][0]["layer"] = 99
    rebuild_path = os.path.join(directory, "rebuild-failure.dsproj")
    with open(rebuild_path, "w", encoding="utf-8") as handle:
        json.dump(rebuild_project, handle)
    rebuild_failed = run(rebuild_path, "rebuild-failure")
    assert rebuild_failed["overall_status"] == "fail"
    assert any(item["code"] == "DRC_REBUILD_FAILED"
               for item in rebuild_failed["categories"]["drc"]["findings"])
    assert rebuild_failed["categories"]["connectivity"]["status"] == "fail"

    advisory_failure = json.loads(json.dumps(incomplete_project))
    advisory_failure["verification_requirements"]["require_signal_integrity"] = False
    advisory_failure["verification_requirements"]["require_power_integrity"] = False
    advisory_failure["footprints"][0]["placement"] = {
        "thermal_power_w": 1.0, "thermal_clearance_mm": 0.0}
    advisory_path = os.path.join(directory, "nonrequired-failure.dsproj")
    with open(advisory_path, "w", encoding="utf-8") as handle:
        json.dump(advisory_failure, handle)
    advisory = run(advisory_path, "nonrequired-failure")
    assert advisory["categories"]["thermal"]["required"] is False
    assert advisory["categories"]["thermal"]["status"] == "fail"
    assert advisory["overall_status"] == "fail"
    print("Unified verification report test passed")

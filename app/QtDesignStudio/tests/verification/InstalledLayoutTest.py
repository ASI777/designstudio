#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

app, core, fixture = map(Path, sys.argv[1:4])
with tempfile.TemporaryDirectory() as raw:
    prefix = Path(raw); bindir = prefix / "bin"; libdir = prefix / "lib"
    bindir.mkdir(); libdir.mkdir()
    installed_app = bindir / "DesignStudio"; installed_core = libdir / "libdesigncore.so"
    shutil.copy2(app, installed_app); shutil.copy2(core, installed_core)
    output = prefix / "verification.json"
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["DS_AI_MOCK"] = "1"
    result = subprocess.run([str(installed_app), "--smoke-test", "--verification-out",
                             str(output), str(fixture)], env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    assert result.returncode == 0, result.stdout
    report = json.loads(output.read_text())
    assert report["overall_status"] == "pass", report
    assert report["categories"]["drc"]["metrics"]["engine"] == "designcore-native"
    print("Installed-layout native engine test passed")

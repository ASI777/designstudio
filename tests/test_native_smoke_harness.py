#!/usr/bin/env python3
"""Regression tests for the installed native-smoke wrapper.

FreeCAD does not consistently propagate QApplication.exit(1) to its host
process.  The wrapper must therefore treat the fixture's explicit marker as
authoritative, even when the launcher itself returns zero.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "modules" / "DesignStudioGui" / "tests" / "run-native-smoke.sh"


class NativeSmokeHarnessTests(unittest.TestCase):
    def run_fixture(self, marker: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix="designstudio-smoke-contract-") as temp:
            prefix = Path(temp) / "install"
            launcher = prefix / "bin" / "DesignStudioLauncher"
            launcher.parent.mkdir(parents=True)
            launcher.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"${DESIGNSTUDIO_FAKE_SMOKE_MARKER:?}\"\n"
                "exit 0\n",
                encoding="utf-8",
            )
            launcher.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "DESIGNSTUDIO_XVFB_ACTIVE": "1",
                    "DESIGNSTUDIO_FAKE_SMOKE_MARKER": marker,
                    "QT_QPA_PLATFORM": "offscreen",
                }
            )
            return subprocess.run(
                ["bash", str(SMOKE), str(prefix)],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=15,
                check=False,
            )

    def test_failure_marker_overrides_zero_host_status(self) -> None:
        result = self.run_fixture(
            "DESIGNSTUDIO_FREECAD_NATIVE_SMOKE_FAILED: synthetic failure"
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_success_requires_exact_ok_marker(self) -> None:
        result = self.run_fixture("DESIGNSTUDIO_FREECAD_NATIVE_SMOKE_OK")
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_zero_without_marker_is_failure(self) -> None:
        result = self.run_fixture("unrelated launcher output")
        self.assertNotEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "freecad/DesignStudioWorkbench/DesignStudio/native_tool_worker.py")
SPEC = importlib.util.spec_from_file_location("native_tool_worker", SOURCE)
worker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(worker)


class NativeToolWorkerTest(unittest.TestCase):
    def request(self, root: Path, stage: Path) -> dict:
        return {
            "schema": worker.SCHEMA,
            "operation": "apply_mechanical_stage",
            "stage_root": str(stage),
            "arguments": {
                "workspace_root": str(root),
                "mechanical_path": str(root / "mechanical.FCStd"),
                "template": "sealed_wall_mount",
                "width_mm": 145,
                "depth_mm": 95,
                "height_mm": 32,
                "wall_mm": 2,
            },
        }

    def test_success_commits_shadow_atomically_and_rewrites_paths(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            mechanical = root / "mechanical.FCStd"
            mechanical.write_bytes(b"approved-revision")
            stage = root / ".designstudio-native-worker-test-success"
            stage.mkdir()

            def execute(_operation: str, encoded: str) -> str:
                arguments = json.loads(encoded)
                mapped = Path(arguments["mechanical_path"])
                self.assertNotEqual(mapped, mechanical)
                self.assertEqual(mechanical.read_bytes(), b"approved-revision")
                mapped.write_bytes(b"candidate-revision")
                contract = Path(arguments["workspace_root"]) / "contracts/result.json"
                contract.parent.mkdir(parents=True)
                contract.write_text('{"status":"candidate"}', encoding="utf-8")
                return json.dumps({"ok": True, "message": "built",
                                   "data": {"path": str(mapped)}})

            result = worker.execute_transaction(self.request(root, stage), execute,
                                                cancel_check=lambda: None)
            self.assertTrue(result["ok"])
            self.assertEqual(mechanical.read_bytes(), b"candidate-revision")
            self.assertEqual(result["data"]["path"], str(mechanical))
            receipt = result["data"]["worker_transaction"]
            self.assertEqual(receipt["status"], "published-awaiting-host-ack")
            self.assertTrue(receipt["host_ack_required"])
            self.assertTrue(receipt["last_approved_revision_preserved_until_commit"])
            self.assertEqual({item["path"] for item in receipt["changed_files"]},
                             {"mechanical.FCStd", "contracts/result.json"})
            journal = root / ".designstudio-native-transaction.json"
            self.assertTrue(journal.is_file())
            backup_root = root / json.loads(journal.read_text(encoding="utf-8"))["backup_root"]
            self.assertTrue(backup_root.is_dir())
            worker._ack_pending_transaction(root)
            self.assertFalse(journal.exists())
            self.assertFalse(backup_root.exists())

    def test_failed_or_cancelled_worker_preserves_approved_revision(self) -> None:
        for mode in ("failed", "cancelled"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as value:
                root = Path(value)
                mechanical = root / "mechanical.FCStd"
                mechanical.write_bytes(b"approved-revision")
                stage = root / f".designstudio-native-worker-test-{mode}"
                stage.mkdir()

                def execute(_operation: str, encoded: str) -> str:
                    mapped = Path(json.loads(encoded)["mechanical_path"])
                    mapped.write_bytes(b"unapproved-partial")
                    return json.dumps({"ok": mode != "failed", "message": mode})

                calls = 0

                def cancel() -> None:
                    nonlocal calls
                    calls += 1
                    if mode == "cancelled" and calls >= 2:
                        raise worker.WorkerCancelled("test cancellation")

                if mode == "cancelled":
                    with self.assertRaises(worker.WorkerCancelled):
                        worker.execute_transaction(self.request(root, stage), execute, cancel)
                else:
                    result = worker.execute_transaction(self.request(root, stage), execute, cancel)
                    self.assertFalse(result["ok"])
                self.assertEqual(mechanical.read_bytes(), b"approved-revision")

    def test_cancellation_between_publication_steps_rolls_back_entire_revision(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            first = root / "a.FCStd"
            second = root / "contracts/b.json"
            first.write_bytes(b"approved-a")
            second.parent.mkdir()
            second.write_bytes(b"approved-b")
            stage = root / ".designstudio-native-worker-commit-window"
            stage.mkdir()

            def execute(_operation: str, encoded: str) -> str:
                arguments = json.loads(encoded)
                shadow = Path(arguments["workspace_root"])
                (shadow / "a.FCStd").write_bytes(b"candidate-a")
                (shadow / "contracts/b.json").write_bytes(b"candidate-b")
                return json.dumps({"ok": True, "message": "candidate ready"})

            calls = 0

            def cancel_between_files() -> None:
                nonlocal calls
                calls += 1
                # execute_transaction checks twice before commit.  The third
                # check precedes file one and the fourth follows file one.
                if calls == 4:
                    raise worker.WorkerCancelled("cancelled in publication window")

            with self.assertRaises(worker.WorkerCancelled):
                worker.execute_transaction(self.request(root, stage), execute,
                                           cancel_between_files)
            self.assertEqual(first.read_bytes(), b"approved-a")
            self.assertEqual(second.read_bytes(), b"approved-b")
            self.assertFalse((root / ".designstudio-native-transaction.json").exists())

    def test_pending_journal_is_recovered_before_new_work(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            destination = root / "mechanical.FCStd"
            destination.write_bytes(b"partially-published")
            stage = root / ".designstudio-native-worker-recovery"
            stage.mkdir()
            backup_root = root / ".designstudio-native-transaction-backup-recovery"
            backup = backup_root / "mechanical.FCStd"
            backup.parent.mkdir(parents=True)
            backup.write_bytes(b"approved-revision")
            journal = {"schema": "design-studio.native-tool-journal/1", "state": "prepared",
                       "backup_root": str(backup_root.relative_to(root)),
                       "entries": [{"path": "mechanical.FCStd", "existed": True,
                                    "backup_path": str(backup.relative_to(root))}]}
            journal_path = root / ".designstudio-native-transaction.json"
            journal_path.write_text(json.dumps(journal), encoding="utf-8")
            # The GUI can destroy its QTemporaryDir after escalating Stop to
            # SIGKILL.  Durable recovery data must not depend on that stage.
            stage.rmdir()
            worker._recover_pending_transaction(root)
            self.assertEqual(destination.read_bytes(), b"approved-revision")
            self.assertFalse(journal_path.exists())
            self.assertFalse(backup_root.exists())

    def test_host_source_wires_stop_to_supervised_native_worker(self) -> None:
        main = (ROOT / "app/QtDesignStudio/MainWindow.cpp").read_text(encoding="utf-8")
        host = (ROOT / "modules/DesignStudioGui/AppDesignStudioGui.cpp").read_text(encoding="utf-8")
        main = (ROOT / "app/QtDesignStudio/MainWindow.cpp").read_text(encoding="utf-8")
        self.assertIn("m_nativeToolCanceller", main)
        self.assertIn("last approved revision preserved", main)
        self.assertIn("cancelTrustedNativeTool", host)
        self.assertIn("setNativeToolHandler(runTrustedNativeTool, cancelTrustedNativeTool)", host)
        self.assertIn("QProcess", host)
        self.assertIn("native_tool_worker.py", host)
        self.assertIn("--recover-root", host)
        self.assertIn("--ack-root", host)
        self.assertIn("host_acknowledged", host)
        self.assertIn("rollback_failed", host)
        self.assertIn("QJsonObject stopped = result", main)
        self.assertIn("completeCodexTool(token, false, jsonText(stopped))", main)


if __name__ == "__main__":
    unittest.main()

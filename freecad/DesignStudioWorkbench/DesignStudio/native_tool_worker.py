"""Transactional, cancellable worker for trusted FreeCAD live tools.

The GUI never sends source code to this worker.  It sends one registered
operation and schema-bound JSON arguments.  Work runs against a shadow copy of
the product workspace; files are atomically copied back only after the trusted
operation returns ``ok=true``.  Killing the worker therefore leaves the last
approved workspace revision untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import time
from typing import Any, Callable


SCHEMA = "design-studio.native-tool-request/1"
ALLOWED_OPERATIONS = {
    "create_product_workspace", "apply_mechanical_stage", "sync_completed_pcb_3d",
    "generate_interaction_structure", "apply_mechanical_cad_program",
    "create_physical_design_session", "create_physical_design_session_v2",
    "create_guided_physical_design_session", "generate_constraint_candidates",
    "export_physical_test_plan", "record_physical_observation",
    "create_physical_candidate_decision", "capture_selected_design_region",
    "preview_local_redesign", "commit_local_redesign", "capture_local_redesign_v2",
    "preview_local_redesign_v2", "commit_local_redesign_v2",
    "discard_local_redesign_v2", "rollback_local_redesign_v2",
    "generate_authoritative_drawings", "create_pcb_topology_study",
    "derive_mechanical_component_requirements",
}


class WorkerCancelled(RuntimeError):
    pass


_cancelled = False


def _handle_cancel(_signum: int, _frame: Any) -> None:
    global _cancelled
    _cancelled = True
    raise WorkerCancelled("native tool worker cancelled")


def _check_cancelled() -> None:
    if _cancelled:
        raise WorkerCancelled("native tool worker cancelled")


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _workspace_root(arguments: dict[str, Any]) -> Path:
    if arguments.get("workspace_root"):
        root = Path(str(arguments["workspace_root"])).expanduser().resolve()
    elif arguments.get("mechanical_path"):
        root = Path(str(arguments["mechanical_path"])).expanduser().resolve().parent
    else:
        raise ValueError("transactional native tools require workspace_root or mechanical_path")
    if not root.is_dir():
        raise ValueError("native tool workspace root is unavailable")
    return root


def _copy_workspace(root: Path, stage_root: Path) -> Path:
    if not _inside(stage_root, root) or not stage_root.name.startswith(".designstudio-native-worker-"):
        raise ValueError("worker stage must be a dedicated directory inside the workspace")
    shadow = stage_root / "workspace"

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name.startswith(".designstudio-native-worker-")}

    shutil.copytree(root, shadow, symlinks=True, ignore=ignore)
    return shadow


def _remap(value: Any, source: Path, destination: Path) -> Any:
    if isinstance(value, dict):
        return {key: _remap(item, source, destination) for key, item in value.items()}
    if isinstance(value, list):
        return [_remap(item, source, destination) for item in value]
    if not isinstance(value, str):
        return value
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        return value
    resolved = candidate.resolve(strict=False)
    if not _inside(resolved, source):
        return value
    return str(destination / resolved.relative_to(source))


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".designstudio-commit-",
                                         dir=str(destination.parent))
    os.close(handle)
    temp_path = Path(temporary)
    try:
        shutil.copy2(source, temp_path)
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    os.close(handle)
    temporary_path = Path(temporary)
    try:
        temporary_path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")),
                                  encoding="utf-8")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _rollback_journal(root: Path, journal: dict[str, Any]) -> None:
    for entry in reversed(journal.get("entries", [])):
        destination = (root / entry["path"]).resolve()
        if not _inside(destination, root):
            raise RuntimeError("transaction destination escapes the workspace")
        if entry["existed"]:
            backup = (root / entry["backup_path"]).resolve()
            if not _inside(backup, root):
                raise RuntimeError("transaction backup escapes the workspace")
            if not backup.is_file():
                raise RuntimeError(f"transaction backup is missing: {backup}")
            _atomic_copy(backup, destination)
        else:
            destination.unlink(missing_ok=True)


def _recover_pending_transaction(root: Path) -> None:
    journal_path = root / ".designstudio-native-transaction.json"
    if not journal_path.is_file():
        return
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if journal.get("schema") != "design-studio.native-tool-journal/1":
        raise RuntimeError("unknown native-tool transaction journal")
    backup_root = (root / journal["backup_root"]).resolve()
    if (not _inside(backup_root, root)
            or not backup_root.name.startswith(".designstudio-native-transaction-backup-")):
        raise RuntimeError("native-tool transaction backup root is unsafe")
    _rollback_journal(root, journal)
    # Restoration is idempotent while the journal remains.  Once every file
    # is restored, removing the journal is the rollback linearization point;
    # cleanup after it may safely leave only an orphaned backup directory.
    journal_path.unlink()
    shutil.rmtree(backup_root, ignore_errors=True)


def _ack_pending_transaction(root: Path) -> None:
    """Make a published transaction permanent after the host accepts it.

    Removing the journal is the commit linearization point.  Backup cleanup is
    deliberately after that point, so a crash can leave only harmless orphaned
    recovery bytes rather than a journal whose backups no longer exist.
    """
    journal_path = root / ".designstudio-native-transaction.json"
    if not journal_path.is_file():
        raise RuntimeError("native-tool transaction acknowledgement is missing a journal")
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if (journal.get("schema") != "design-studio.native-tool-journal/1"
            or journal.get("state") != "published-awaiting-host-ack"):
        raise RuntimeError("native-tool transaction is not ready for acknowledgement")
    backup_root = (root / journal["backup_root"]).resolve()
    if (not _inside(backup_root, root)
            or not backup_root.name.startswith(".designstudio-native-transaction-backup-")):
        raise RuntimeError("native-tool transaction backup root is unsafe")
    journal_path.unlink()
    shutil.rmtree(backup_root, ignore_errors=True)


def _commit_priority(path: Path) -> tuple[int, str]:
    suffix = path.suffix.lower()
    authoritative = {".fcstd": 20, ".dsproj": 30, ".dsworkspace": 40}.get(suffix, 0)
    return authoritative, path.as_posix()


def _commit_shadow(shadow: Path, root: Path,
                   cancel_check: Callable[[], None] = _check_cancelled) -> list[dict[str, Any]]:
    changed: list[tuple[Path, Path]] = []
    for source in shadow.rglob("*"):
        if source.is_symlink() or not source.is_file():
            continue
        relative = source.relative_to(shadow)
        destination = root / relative
        if not destination.is_file() or _digest(source) != _digest(destination):
            changed.append((source, destination))
    changed.sort(key=lambda pair: _commit_priority(pair[1].relative_to(root)))
    if not changed:
        return []
    # Backups must outlive the GUI-owned QTemporaryDir that contains the
    # shadow workspace.  The host may escalate Stop from SIGTERM to SIGKILL;
    # keeping recovery bytes in that temporary stage would make the durable
    # journal impossible to replay after the stage destructor runs.
    backup_root = Path(tempfile.mkdtemp(
        prefix=".designstudio-native-transaction-backup-", dir=str(root)))
    entries = []
    for _source, destination in changed:
        relative = destination.relative_to(root)
        backup = backup_root / relative
        existed = destination.is_file()
        if existed:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, backup)
        entries.append({"path": relative.as_posix(), "existed": existed,
                        "backup_path": backup.relative_to(root).as_posix()})
    journal = {"schema": "design-studio.native-tool-journal/1", "state": "prepared",
               "backup_root": backup_root.relative_to(root).as_posix(), "entries": entries}
    journal_path = root / ".designstudio-native-transaction.json"
    _atomic_json(journal_path, journal)
    receipt = []
    try:
        for source, destination in changed:
            cancel_check()
            _atomic_copy(source, destination)
            receipt.append({"path": str(destination.relative_to(root)),
                            "sha256": _digest(destination)})
            delay_ms = int(os.environ.get("DESIGNSTUDIO_NATIVE_TOOL_TEST_COMMIT_DELAY_MS", "0") or 0)
            if delay_ms:
                time.sleep(delay_ms / 1000.0)
            cancel_check()
    except BaseException:
        _rollback_journal(root, journal)
        journal_path.unlink(missing_ok=True)
        shutil.rmtree(backup_root, ignore_errors=True)
        raise
    # Publication is reversible until the GUI has consumed the response,
    # refreshed the active document and explicitly acknowledges the revision.
    # This closes both the hard-kill-during-copy window and the smaller
    # post-publication/pre-response cancellation race.
    journal["state"] = "published-awaiting-host-ack"
    _atomic_json(journal_path, journal)
    return receipt


def execute_transaction(request: dict[str, Any],
                        executor: Callable[[str, str], str],
                        cancel_check: Callable[[], None] = _check_cancelled) -> dict[str, Any]:
    if request.get("schema") != SCHEMA:
        raise ValueError("unsupported native tool request schema")
    operation = str(request.get("operation", ""))
    if operation not in ALLOWED_OPERATIONS:
        raise ValueError("unregistered native worker operation")
    arguments = request.get("arguments")
    if not isinstance(arguments, dict):
        raise ValueError("native tool arguments must be an object")
    root = _workspace_root(arguments)
    _recover_pending_transaction(root)
    stage_root = Path(str(request.get("stage_root", ""))).expanduser().resolve()
    shadow = _copy_workspace(root, stage_root)
    mapped_arguments = _remap(arguments, root, shadow)
    cancel_check()

    delay_ms = int(os.environ.get("DESIGNSTUDIO_NATIVE_TOOL_TEST_DELAY_MS", "0") or 0)
    while delay_ms > 0:
        cancel_check()
        interval = min(delay_ms, 50)
        time.sleep(interval / 1000.0)
        delay_ms -= interval

    raw = executor(operation, json.dumps(mapped_arguments, separators=(",", ":")))
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("trusted native tool returned a non-object result")
    if not result.get("ok"):
        return _remap(result, shadow, root)
    cancel_check()
    committed = _commit_shadow(shadow, root, cancel_check)
    result = _remap(result, shadow, root)
    data = result.setdefault("data", {})
    if isinstance(data, dict):
        data["worker_transaction"] = {
            "schema": "design-studio.native-tool-transaction/1",
            "status": ("published-awaiting-host-ack" if committed else "no-changes"),
            "changed_files": committed,
            "last_approved_revision_preserved_until_commit": True,
            "transaction_wide_rollback_journal": True,
            "host_ack_required": bool(committed),
        }
    return result


def _write_response(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".native-response-", dir=str(path.parent))
    os.close(handle)
    temp_path = Path(temporary)
    try:
        temp_path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path,
                        default=os.environ.get("DESIGNSTUDIO_NATIVE_TOOL_REQUEST"))
    parser.add_argument("--response", type=Path,
                        default=os.environ.get("DESIGNSTUDIO_NATIVE_TOOL_RESPONSE"))
    parser.add_argument("--recover-root", type=Path)
    parser.add_argument("--ack-root", type=Path)
    arguments, _freecad = parser.parse_known_args()
    signal.signal(signal.SIGTERM, _handle_cancel)
    signal.signal(signal.SIGINT, _handle_cancel)
    if arguments.recover_root is not None:
        try:
            root = arguments.recover_root.expanduser().resolve()
            if not root.is_dir():
                raise ValueError("native tool recovery root is unavailable")
            _recover_pending_transaction(root)
            return 0
        except Exception as exc:
            print(f"native tool recovery failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    if arguments.ack_root is not None:
        try:
            root = arguments.ack_root.expanduser().resolve()
            if not root.is_dir():
                raise ValueError("native tool acknowledgement root is unavailable")
            _ack_pending_transaction(root)
            return 0
        except Exception as exc:
            print(f"native tool acknowledgement failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    if arguments.request is None or arguments.response is None:
        parser.error("request and response paths are required")
    try:
        request = json.loads(arguments.request.read_text(encoding="utf-8"))
        package_root = Path(__file__).resolve().parents[1]
        if str(package_root) not in sys.path:
            sys.path.insert(0, str(package_root))
        from DesignStudio import live_tools
        result = execute_transaction(request, live_tools.execute)
        delay_ms = int(os.environ.get("DESIGNSTUDIO_NATIVE_TOOL_TEST_POST_COMMIT_DELAY_MS", "0") or 0)
        while delay_ms > 0:
            _check_cancelled()
            interval = min(delay_ms, 50)
            time.sleep(interval / 1000.0)
            delay_ms -= interval
        _write_response(arguments.response, result)
        return 0 if result.get("ok") else 2
    except WorkerCancelled:
        return 130
    except Exception as exc:
        _write_response(arguments.response, {
            "ok": False, "message": f"{type(exc).__name__}: {exc}",
            "worker_failed_closed": True,
        })
        return 1


if (__name__ == "__main__" or os.environ.get("DESIGNSTUDIO_NATIVE_TOOL_REQUEST")
        or os.environ.get("DESIGNSTUDIO_NATIVE_TOOL_CONTROL")):
    raise SystemExit(main())

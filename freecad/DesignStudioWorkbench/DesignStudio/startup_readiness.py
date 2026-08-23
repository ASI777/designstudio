"""Post-activation receipts, native lifecycle checks, and clean test shutdown."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


_activation_probe_started = False


def _canonical_digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _atomic_json(destination, payload):
    destination = Path(destination).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(".%s.%s.tmp" % (destination.name, os.getpid()))
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, destination)


def _workspace_objects():
    from PySide import QtWidgets
    import FreeCADGui as Gui

    main_window = Gui.getMainWindow()
    widgets = list(main_window.findChildren(QtWidgets.QWidget)) if main_window else []

    def count(name, kind=None):
        return sum(
            1 for widget in widgets
            if widget.objectName() == name and (kind is None or isinstance(widget, kind))
        )

    return main_window, widgets, {
        "workspace_controllers": count("DesignStudioProductWorkspace", QtWidgets.QMainWindow),
        "workspace_documents": count("DesignStudioWorkspaceDocument", QtWidgets.QMdiSubWindow),
        "product_surfaces": count("DesignStudioProductSurface"),
        "chat_docks": count("dock_chat", QtWidgets.QDockWidget),
        "context_toolbars": count("toolbar_designstudio_context", QtWidgets.QToolBar),
    }


def workspace_probe_snapshot():
    from PySide import QtWidgets
    import FreeCADGui as Gui

    main_window, _widgets, counts = _workspace_objects()
    application = QtWidgets.QApplication.instance()
    active = Gui.activeWorkbench()
    active_name = active.name() if active is not None else ""
    document = main_window.findChild(
        QtWidgets.QMdiSubWindow, "DesignStudioWorkspaceDocument") if main_window else None
    surface = main_window.findChild(
        QtWidgets.QWidget, "DesignStudioProductSurface") if main_window else None
    chat_dock = main_window.findChild(
        QtWidgets.QDockWidget, "dock_chat") if main_window else None
    visible_top_levels = [
        widget for widget in (application.topLevelWidgets() if application else [])
        if widget.isVisible() and isinstance(widget, QtWidgets.QMainWindow)
    ]
    required_workbenches = {"PartDesignWorkbench", "SketcherWorkbench", "AssemblyWorkbench"}
    checks = {
        "main_window_visible": bool(main_window and main_window.isVisible()),
        "single_visible_host_window": visible_top_levels == [main_window],
        "active_designstudio_workbench": active_name == "DesignStudioWorkbench",
        "workspace_document_visible": bool(document and document.isVisible()),
        "product_surface_visible": bool(surface and surface.isVisible()),
        "non_mainwindow_product_surface": bool(
            document and document.widget() is not None
            and not isinstance(document.widget(), QtWidgets.QMainWindow)),
        "chat_dock_visible": bool(chat_dock and chat_dock.isVisible()),
        "native_cad_workbenches": required_workbenches.issubset(set(Gui.listWorkbenches())),
        "one_workspace_controller": counts["workspace_controllers"] == 1,
        "one_workspace_document": counts["workspace_documents"] == 1,
        "one_product_surface": counts["product_surfaces"] == 1,
        "one_chat_dock": counts["chat_docks"] == 1,
        "one_context_toolbar": counts["context_toolbars"] == 1,
    }
    return {
        "checks": checks,
        "counts": counts,
        "active_workbench": active_name,
        "visible_host_windows": len(visible_top_levels),
        "window_title": main_window.windowTitle() if main_window else "",
        "ok": all(checks.values()),
    }


def _write_workspace_receipt(lifecycle_cycles=0, lifecycle_history=None, error=None):
    receipt_name = os.environ.get("DESIGNSTUDIO_STARTUP_RECEIPT", "").strip()
    if not receipt_name:
        return None
    snapshot = workspace_probe_snapshot() if error is None else {
        "checks": {}, "counts": {}, "active_workbench": "",
        "visible_host_windows": 0, "window_title": "", "ok": False,
    }
    payload = {
        "schema": "design-studio.startup-readiness/1",
        "status": "pass" if snapshot["ok"] and error is None else "fail",
        "pid": os.getpid(),
        "token": os.environ.get("DESIGNSTUDIO_STARTUP_RECEIPT_TOKEN", ""),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "lifecycle_cycles": int(lifecycle_cycles),
        "snapshot": snapshot,
    }
    if lifecycle_history is not None:
        payload["lifecycle_history"] = lifecycle_history
    if error is not None:
        payload["error"] = str(error)
    payload["receipt_digest"] = _canonical_digest(payload)
    _atomic_json(receipt_name, payload)
    return payload


def _assert_workspace_closed():
    _main_window, _widgets, counts = _workspace_objects()
    leftovers = {key: value for key, value in counts.items() if value != 0}
    if leftovers:
        raise RuntimeError("workspace close left objects: %s" % json.dumps(leftovers, sort_keys=True))
    return counts


def complete_activation(cycles=0):
    """Wait for the completed object tree; optionally run native workbench cycles."""
    global _activation_probe_started
    from PySide import QtCore, QtWidgets

    # Reactivating DesignStudio during the lifecycle fixture calls the
    # workbench's Activated() method again.  Keep exactly one orchestration
    # state machine; otherwise each cycle recursively starts another runner.
    if _activation_probe_started:
        return
    _activation_probe_started = True

    def check(attempt):
        snapshot = workspace_probe_snapshot()
        if snapshot["ok"]:
            if cycles:
                run_activation_cycle(cycles)
            else:
                _write_workspace_receipt()
            return
        if attempt < 200:
            QtCore.QTimer.singleShot(50, lambda: check(attempt + 1))
            return
        _write_workspace_receipt(error="post-activation workspace assertion failed")
        QtWidgets.QApplication.instance().exit(1)

    QtCore.QTimer.singleShot(0, lambda: check(0))


def run_activation_cycle(cycles):
    """Exercise actual FreeCAD workbench callbacks and close/reopen ownership."""
    from PySide import QtCore, QtWidgets
    import DesignStudioGui
    import FreeCADGui as Gui

    state = {"cycle": 0, "attempt": 0, "phase": "deactivate", "history": []}

    def fail(error):
        _write_workspace_receipt(lifecycle_cycles=0, lifecycle_history=state["history"], error=error)
        QtWidgets.QApplication.instance().exit(1)

    def verify_designstudio():
        try:
            snapshot = workspace_probe_snapshot()
            if snapshot["ok"]:
                state["cycle"] += 1
                state["history"].append({
                    "cycle": state["cycle"], "phase": "designstudio_activated",
                    "active_workbench": snapshot["active_workbench"],
                    "counts": snapshot["counts"], "ok": True,
                })
                if state["cycle"] >= cycles:
                    _write_workspace_receipt(
                        lifecycle_cycles=cycles, lifecycle_history=state["history"])
                    return
                state["attempt"] = 0
                QtCore.QTimer.singleShot(0, deactivate)
                return
            if state["attempt"] < 200:
                state["attempt"] += 1
                QtCore.QTimer.singleShot(25, verify_designstudio)
                return
            raise RuntimeError("DesignStudio activation failed: %s" % json.dumps(snapshot, sort_keys=True))
        except Exception as exc:
            fail(exc)

    def verify_native():
        try:
            if Gui.activeWorkbench().name() != "PartDesignWorkbench":
                if state["attempt"] < 200:
                    state["attempt"] += 1
                    QtCore.QTimer.singleShot(25, verify_native)
                    return
                raise RuntimeError("native workbench was not activated")
            DesignStudioGui.closeWorkspace()
            closed = _assert_workspace_closed()
            state["history"].append({
                "cycle": state["cycle"] + 1, "phase": "native_activated_document_closed",
                "active_workbench": "PartDesignWorkbench", "counts": closed, "ok": True,
            })
            Gui.activateWorkbench("DesignStudioWorkbench")
            state["attempt"] = 0
            QtCore.QTimer.singleShot(0, verify_designstudio)
        except Exception as exc:
            fail(exc)

    def deactivate():
        try:
            state["attempt"] = 0
            Gui.activateWorkbench("PartDesignWorkbench")
            QtCore.QTimer.singleShot(0, verify_native)
        except Exception as exc:
            fail(exc)

    QtCore.QTimer.singleShot(0, deactivate)

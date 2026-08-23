"""Headless startup assertion for the installed branded application."""

from pathlib import Path

from PySide import QtCore, QtGui, QtWidgets
import FreeCAD as App
import FreeCADGui as Gui
import DesignStudioGui


def verify_and_exit(
    App=App,
    Path=Path,
    QtGui=QtGui,
    QtWidgets=QtWidgets,
    Gui=Gui,
    DesignStudioGui=DesignStudioGui,
):
    try:
        main_window = Gui.getMainWindow()
        attempts = getattr(main_window, "_designstudio_native_smoke_attempts", 0) + 1
        setattr(main_window, "_designstudio_native_smoke_attempts", attempts)
        if attempts > 240:
            raise RuntimeError("DesignStudio did not finish branded startup within 60 seconds")
        if not main_window.isVisible():
            return
        # Do not activate the workbench here. The branded executable must open
        # DesignStudio automatically, exactly as the desktop shortcut does.
        active_workbench = Gui.activeWorkbench()
        if active_workbench is None or active_workbench.name() != "DesignStudioWorkbench":
            return
        document = main_window.findChild(
            QtWidgets.QMdiSubWindow, "DesignStudioWorkspaceDocument"
        )
        if document is None or document.widget() is None or not document.isVisible():
            return
        if isinstance(document.widget(), QtWidgets.QMainWindow):
            raise RuntimeError("DesignStudio nested a QMainWindow in FreeCAD MDI")
        chat_dock = main_window.findChild(QtWidgets.QDockWidget, "dock_chat")
        if chat_dock is None or chat_dock.widget() is None or not chat_dock.isVisible():
            return
        toolbars = main_window.findChildren(QtWidgets.QToolBar)
        toggle_toolbar = next(
            (toolbar for toolbar in toolbars if toolbar.isVisible() and any(
                action.objectName() == "action_toggle_codex_side_panel"
                for action in toolbar.actions()
            )),
            None,
        )
        toggle_action = next(
            (action for action in toggle_toolbar.actions()
             if action.objectName() == "action_toggle_codex_side_panel"),
            None,
        ) if toggle_toolbar is not None else None
        if toggle_action is None:
            toolbar_state = [
                (toolbar.objectName(), toolbar.windowTitle(), toolbar.isVisible(),
                 [action.objectName() for action in toolbar.actions()])
                for toolbar in toolbars
            ]
            raise RuntimeError(
                f"Codex side-panel toggle is missing from a visible toolbar: {toolbar_state}"
            )
        if toggle_action.shortcut() != QtGui.QKeySequence("Ctrl+Alt+B"):
            raise RuntimeError("Codex side-panel shortcut is not Ctrl+Alt+B")
        toggle_action.trigger()
        QtWidgets.QApplication.processEvents()
        if chat_dock.isVisible():
            raise RuntimeError("Codex side-panel toggle did not hide the dock")
        toggle_action.trigger()
        QtWidgets.QApplication.processEvents()
        if not chat_dock.isVisible():
            raise RuntimeError("Codex side-panel toggle did not restore the dock")
        options_button = main_window.findChild(
            QtWidgets.QToolButton, "codex_options_button"
        )
        if options_button is None or options_button.menu() is None:
            raise RuntimeError("Codex model options control is missing")
        option_text = [action.text() for action in options_button.menu().actions()]
        if not any(text.startswith("Model") for text in option_text):
            raise RuntimeError(f"Codex model option row is missing: {option_text}")
        if not any(text.startswith("Effort") for text in option_text):
            raise RuntimeError(f"Codex effort option row is missing: {option_text}")
        if not any(text.startswith("Speed") for text in option_text):
            raise RuntimeError(f"Codex speed option row is missing: {option_text}")
        top_levels = [widget for widget in QtWidgets.QApplication.topLevelWidgets()
                      if widget.isVisible() and isinstance(widget, QtWidgets.QMainWindow)]
        if top_levels != [main_window]:
            raise RuntimeError(f"expected one top-level main window, found {len(top_levels)}")
        title = main_window.windowTitle()
        if "DesignStudio" not in title or "FreeCAD" in title:
            raise RuntimeError(f"unexpected branded window title: {title!r}")
        expected_icon_path = Path(App.getHomePath()) / (
            "share/icons/hicolor/scalable/apps/designstudio.svg"
        )
        expected_icon = QtGui.QIcon(str(expected_icon_path))
        actual_image = main_window.windowIcon().pixmap(64, 64).toImage()
        expected_image = expected_icon.pixmap(64, 64).toImage()
        if expected_icon.isNull() or actual_image != expected_image:
            raise RuntimeError("main window does not use the DesignStudio icon")
        workbenches = Gui.listWorkbenches()
        required = {"PartDesignWorkbench", "SketcherWorkbench", "AssemblyWorkbench"}
        missing = sorted(required.difference(workbenches))
        if missing:
            raise RuntimeError(f"native CAD workbenches are unavailable: {missing}")
        main_window._designstudio_native_smoke_timer.stop()
        print("DESIGNSTUDIO_FREECAD_NATIVE_SMOKE_OK", flush=True)
        QtWidgets.QApplication.instance().exit(0)
    except Exception as error:  # FreeCAD must return a failing process code.
        print(f"DESIGNSTUDIO_FREECAD_NATIVE_SMOKE_FAILED: {error}", flush=True)
        QtWidgets.QApplication.instance().exit(1)


_timer = QtCore.QTimer(Gui.getMainWindow())
_timer.timeout.connect(verify_and_exit)
setattr(Gui.getMainWindow(), "_designstudio_native_smoke_timer", _timer)
# FreeCAD pumps events during workbench discovery before QApplication::exec().
# Poll until the main window and dock visibility are settled, then stop.
_timer.start(250)

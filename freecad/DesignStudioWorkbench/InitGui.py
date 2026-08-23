"""GUI initializer for the FreeCAD-hosted DesignStudio workbench."""

import os
from pathlib import Path

import FreeCAD as App
import FreeCADGui as Gui


def _enforce_designstudio_startup():
    general = App.ParamGet("User parameter:BaseApp/Preferences/General")
    general.SetString("AutoloadModule", "DesignStudioWorkbench")
    start = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Start")
    start.SetBool("ShowOnStartup", False)


_enforce_designstudio_startup()


def _workspace_argument():
    """Return the launcher-validated workspace path, if one was supplied."""
    raw = os.environ.get("DESIGNSTUDIO_WORKSPACE_PATH", "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    if path.is_dir() and (path / "manifest.json").is_file():
        return str(path)
    return None


class DesignStudioWorkbench(Gui.Workbench):
    MenuText = "DesignStudio"
    ToolTip = "Unified product design: mechanical, schematic, PCB and verification"

    def Initialize(self):
        try:
            import PySide6  # noqa: F401
            import shiboken6  # noqa: F401
            from DesignStudio import commands
        except ImportError as exc:
            raise RuntimeError(
                "DesignStudio workbench dependencies are unavailable "
                "(PySide6, shiboken6, or DesignStudio): %s" % exc
            ) from exc
        import DesignStudioGui  # noqa: F401
        registered = commands.register()
        commands.verify_registered(registered)
        self.appendToolbar("DesignStudio", commands.PRIMARY_COMMANDS)
        self.appendMenu("Product", commands.PRIMARY_COMMANDS)

    def GetClassName(self):
        return "Gui::PythonWorkbench"

    def Activated(self):
        try:
            import PySide6  # noqa: F401
            import shiboken6  # noqa: F401
            import DesignStudioGui
        except ImportError as exc:
            raise RuntimeError(
                "DesignStudio cannot activate because its Python dependencies "
                "are unavailable: %s" % exc
            ) from exc
        # Resolve the launcher handoff here instead of relying on a module
        # global.  FreeCAD can execute workbench initializers in a transient
        # namespace while activating a Python workbench; a module-level
        # helper is then not guaranteed to be visible from this bound method.
        # Keeping the small validation local makes direct desktop launches
        # reliable while still rejecting arbitrary paths.
        workspace_path = None
        raw_workspace = os.environ.get("DESIGNSTUDIO_WORKSPACE_PATH", "").strip()
        if raw_workspace:
            from pathlib import Path as _WorkspacePath
            candidate = _WorkspacePath(raw_workspace).expanduser().resolve()
            if candidate.is_dir() and (candidate / "manifest.json").is_file():
                workspace_path = str(candidate)
        if workspace_path:
            DesignStudioGui.showWorkspace(workspace_path)
        else:
            DesignStudioGui.showWorkspace()
        if os.environ.get("DESIGNSTUDIO_STARTUP_RECEIPT", "").strip():
            from DesignStudio import startup_readiness
            cycles = int(os.environ.get("DESIGNSTUDIO_ACTIVATION_CYCLES", "0") or 0)
            if cycles < 0 or cycles > 100:
                raise RuntimeError("DESIGNSTUDIO_ACTIVATION_CYCLES must be between 0 and 100")
            startup_readiness.complete_activation(cycles)

    def Deactivated(self):
        try:
            import DesignStudioGui
            DesignStudioGui.hideWorkspace()
        except ImportError:
            pass


Gui.addWorkbench(DesignStudioWorkbench())

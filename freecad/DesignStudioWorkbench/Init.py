"""FreeCAD application initializer for the unified DesignStudio workbench.

FreeCAD executes module initializers without defining ``__file__`` and already
adds the module directory to its Python search path. GUI registration lives in
InitGui.py, so the application-side initializer is intentionally side-effect
free.
"""

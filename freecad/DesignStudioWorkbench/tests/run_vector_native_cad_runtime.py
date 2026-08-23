#!/usr/bin/env python3
"""Launcher entry point for the kernel-backed vector-native runtime test."""
from pathlib import Path

fixture = (Path.cwd() / "freecad" / "DesignStudioWorkbench" / "tests" /
           "vector_native_cad_runtime.py").resolve()
namespace = {"__file__": str(fixture), "__name__": "__main__"}
exec(compile(fixture.read_text(encoding="utf-8"), str(fixture), "exec"), namespace)

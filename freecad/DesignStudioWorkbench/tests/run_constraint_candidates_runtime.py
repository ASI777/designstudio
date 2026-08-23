#!/usr/bin/env python3
from pathlib import Path

p = (Path.cwd() / "freecad" / "DesignStudioWorkbench" / "tests" /
     "constraint_candidates_runtime.py").resolve()
exec(compile(p.read_text(encoding="utf-8"), str(p), "exec"),
     {"__file__": str(p), "__name__": "__main__"})

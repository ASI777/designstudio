"""Fail-closed execution of small, validated generated SPICE netlists.

The runner accepts no file includes, control language, shell escapes, or model
library paths. A missing solver, missing model, unsupported directive, timeout,
or parse/execution error is ``incomplete`` and is never promoted to a pass.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()


_BLOCKED = re.compile(
    r"(?im)^\s*\.(?:include|inc|lib|control|endc|shell|exec|csparam|alter|save)\b|"
    r"\b(?:system\s*\(|shell\s+)"
)
_SUPPORTED = re.compile(
    r"(?i)^\s*(?:[RCLVIDEGB]\S*|\.model\b|\.param\b|\.temp\b|"
    r"\.op\b|\.dc\b|\.tran\b|\.ac\b|\.print\b|\.measure\b|\.end\b|\*|$)"
)


def run_validated_spice(model: dict | None, *, solver: str | None = None,
                        timeout_s: float = 10.0) -> dict:
    base = {"evidence_kind": "simulated", "engine": "unavailable",
            "value": None, "unit": None, "margin": None}
    if not isinstance(model, dict):
        return {**base, "status": "incomplete", "input_digest": _digest(model),
                "output_digest": _digest({"status": "incomplete", "reason": "missing model"}),
                "message": "No supported SPICE model is bound"}
    input_digest = _digest(model)
    netlist = model.get("generated_netlist") or model.get("netlist")
    if model.get("validated") is not True or not isinstance(netlist, str) or not netlist.strip():
        result = {**base, "status": "incomplete", "input_digest": input_digest,
                  "message": "SPICE model is absent or has not passed model validation"}
        result["output_digest"] = _digest(result)
        return result
    if len(netlist.encode()) > 256 * 1024 or _BLOCKED.search(netlist):
        result = {**base, "status": "incomplete", "input_digest": input_digest,
                  "message": "SPICE model contains a blocked external or control construct"}
        result["output_digest"] = _digest(result)
        return result
    unsupported = [line.strip() for line in netlist.splitlines()
                   if line.strip() and not _SUPPORTED.match(line)]
    if unsupported:
        result = {**base, "status": "incomplete", "input_digest": input_digest,
                  "message": f"Unsupported SPICE construct: {unsupported[0][:80]}"}
        result["output_digest"] = _digest(result)
        return result
    executable = solver or shutil.which("ngspice")
    if not executable:
        result = {**base, "status": "incomplete", "input_digest": input_digest,
                  "message": "ngspice is unavailable"}
        result["output_digest"] = _digest(result)
        return result
    executable = str(Path(executable).resolve())
    if Path(executable).name.lower() not in ("ngspice", "ngspice.exe"):
        result = {**base, "status": "incomplete", "input_digest": input_digest,
                  "message": "Only the validated ngspice batch adapter is supported"}
        result["output_digest"] = _digest(result)
        return result
    if not netlist.rstrip().lower().endswith(".end"):
        netlist = netlist.rstrip() + "\n.end\n"
    try:
        with tempfile.TemporaryDirectory(prefix="designstudio-spice-") as raw:
            root = Path(raw)
            circuit = root / "generated.cir"
            log = root / "solver.log"
            circuit.write_text(netlist, encoding="utf-8")
            completed = subprocess.run(
                [executable, "-b", "-o", str(log), str(circuit)], cwd=root,
                env={"PATH": str(Path(executable).parent), "LC_ALL": "C"},
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, timeout=timeout_s,
                check=False,
            )
            output = (log.read_text(encoding="utf-8", errors="replace")
                      if log.is_file() else completed.stdout)[:64 * 1024]
    except (OSError, subprocess.TimeoutExpired) as exc:
        result = {**base, "status": "incomplete", "input_digest": input_digest,
                  "message": f"SPICE execution did not complete: {type(exc).__name__}"}
        result["output_digest"] = _digest(result)
        return result
    failed = completed.returncode != 0 or re.search(
        r"(?im)^\s*(?:fatal|error|abort(?:ed)?)\b", output or "")
    result = {**base, "engine": f"ngspice:{Path(executable).name}",
              "status": "incomplete" if failed else "pass",
              "input_digest": input_digest,
              "message": "SPICE solver failed or returned an error" if failed
                         else "Validated generated netlist executed successfully",
              "solver_return_code": completed.returncode,
              "solver_output_sha256": hashlib.sha256((output or "").encode()).hexdigest()}
    result["output_digest"] = _digest(result)
    return result

#!/usr/bin/env python3
"""Fail closed unless structural and thermal mesh sequences converge within 5%."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def finite_positive(value: object, name: str, *, allow_zero: bool = False) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")
    result = float(value)
    if result < 0 or (result == 0 and not allow_zero):
        raise ValueError(f"{name} must be positive")
    return result


def relative_change(a: float, b: float) -> float:
    return abs(b - a) / max(abs(b), 1e-15)


def validate(result: dict) -> dict:
    if result.get("schema") != "multi-gpu-campaign.mfem-physics-result/1":
        raise ValueError("physics result schema is invalid")
    if result.get("device") != "hip" or result.get("hip_arch") != "gfx942":
        raise ValueError("result was not produced by the gfx942 HIP path")
    levels = result.get("mesh_levels")
    if not isinstance(levels, list) or len(levels) < 3:
        raise ValueError("at least three mesh levels are required")
    previous_elements = 0
    required = (
        "maximum_displacement_mm", "strain_energy_mj",
        "maximum_temperature_c", "thermal_resistance_k_w",
    )
    for index, level in enumerate(levels):
        elements = level.get("elements")
        if type(elements) is not int or elements <= previous_elements:
            raise ValueError("mesh element counts must increase strictly")
        previous_elements = elements
        for field in required:
            finite_positive(level.get(field), f"mesh_levels[{index}].{field}",
                            allow_zero=field == "maximum_displacement_mm")
    coarse, fine = levels[-2:]
    convergence = {
        field: relative_change(float(coarse[field]), float(fine[field]))
        for field in required
    }
    modes = result.get("first_six_modes_hz")
    if not isinstance(modes, list) or len(modes) != 6 or any(
            finite_positive(value, "mode") <= 0 for value in modes):
        raise ValueError("first six positive modal frequencies are required")
    stress = finite_positive(result.get("von_mises_p95_mpa"), "von_mises_p95_mpa")
    yield_strength = finite_positive(result.get("yield_strength_mpa"),
                                     "yield_strength_mpa")
    claimed_fos = finite_positive(result.get("factor_of_safety"), "factor_of_safety")
    if abs(claimed_fos - yield_strength / stress) > 1e-6:
        raise ValueError("factor of safety is inconsistent with p95 stress")
    passed = all(change <= 0.05 for change in convergence.values())
    return {"convergence": convergence, "mesh_converged": passed,
            "release_eligible": passed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.input.read_text(encoding="utf-8"))
    report = validate(source)
    report["schema"] = "multi-gpu-campaign.physics-convergence/1"
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["mesh_converged"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

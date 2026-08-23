from convergence_gate import validate


def result():
    return {
        "schema": "multi-gpu-campaign.mfem-physics-result/1",
        "device": "hip", "hip_arch": "gfx942",
        "mesh_levels": [
            {"elements": 1000, "maximum_displacement_mm": 1.0,
             "strain_energy_mj": 2.0, "maximum_temperature_c": 55.0,
             "thermal_resistance_k_w": 3.1},
            {"elements": 8000, "maximum_displacement_mm": 0.96,
             "strain_energy_mj": 1.94, "maximum_temperature_c": 54.0,
             "thermal_resistance_k_w": 3.0},
            {"elements": 64000, "maximum_displacement_mm": 0.95,
             "strain_energy_mj": 1.92, "maximum_temperature_c": 53.5,
             "thermal_resistance_k_w": 2.98},
        ],
        "first_six_modes_hz": [110, 180, 250, 320, 400, 510],
        "von_mises_p95_mpa": 14.0, "yield_strength_mpa": 42.0,
        "factor_of_safety": 3.0,
    }


def test_converged_result_passes():
    assert validate(result())["mesh_converged"]


def test_non_converged_result_fails():
    value = result()
    value["mesh_levels"][-1]["strain_energy_mj"] = 1.0
    assert not validate(value)["mesh_converged"]

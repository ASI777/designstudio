"""Interchangeable exact-analysis worker boundary.

The AI gateway may rank and explain results, but it never turns a geometry
proposal into a trusted result.  A job must carry an approved geometry digest
and an exact worker returns a digest-bound result.  Workers that are not
installed report ``unavailable`` instead of fabricating physics.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Protocol


ANALYSIS_KINDS = (
    "linear_static",
    "modal_vibration",
    "thermal",
    "nonlinear_contact",
    "drop_impact",
    "plastic_creep_fatigue",
    "injection_moulding_flow_warpage",
)


class WorkerContractError(ValueError):
    """Raised when an analysis job violates the trust boundary."""


def canonical_digest(value: dict[str, Any], *, without: str | None = None) -> str:
    material = dict(value)
    if without is not None:
        material.pop(without, None)
    encoded = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


def validate_approved_job(job: dict[str, Any]) -> None:
    if not isinstance(job, dict):
        raise WorkerContractError("analysis job must be an object")
    if job.get("schema") != "design-studio.mechanical-analysis-job/1":
        raise WorkerContractError("unsupported mechanical analysis job schema")
    for field in ("job_id", "candidate_id", "analysis_kind", "geometry", "approval", "solver"):
        if not isinstance(job.get(field), (str if field in {"job_id", "candidate_id", "analysis_kind"} else dict)):
            raise WorkerContractError(f"analysis job field {field} is missing or invalid")
    if job["analysis_kind"] not in ANALYSIS_KINDS:
        raise WorkerContractError(f"unsupported analysis kind: {job['analysis_kind']}")
    geometry = job["geometry"]
    if not _is_sha256(geometry.get("artifact_sha256")):
        raise WorkerContractError("geometry.artifact_sha256 must be a lowercase SHA-256 digest")
    if geometry.get("units") != "mm":
        raise WorkerContractError("mechanical geometry units must be mm")
    approval = job["approval"]
    if approval.get("status") != "approved":
        raise WorkerContractError("exact workers accept only explicitly approved geometry")
    if not approval.get("approved_by") or not approval.get("approval_digest"):
        raise WorkerContractError("approved geometry requires approver and approval digest")
    solver = job["solver"]
    if not solver.get("worker"):
        raise WorkerContractError("analysis job must select a worker")


class AnalysisWorker(Protocol):
    name: str
    version: str

    def run(self, job: dict[str, Any]) -> dict[str, Any]: ...


def _base_result(job: dict[str, Any], worker: AnalysisWorker) -> dict[str, Any]:
    return {
        "schema": "design-studio.mechanical-analysis-result/1",
        "job_id": job["job_id"],
        "candidate_id": job["candidate_id"],
        "analysis_kind": job["analysis_kind"],
        "status": "incomplete",
        "worker": {
            "name": worker.name,
            "version": worker.version,
            "backend": job.get("solver", {}).get("backend", "cpu"),
        },
        "metrics": {},
        "evidence": {
            "geometry_sha256": job["geometry"]["artifact_sha256"],
            "analysis_available": False,
        },
        "warnings": [],
        "release_eligible": False,
    }


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    result["result_digest"] = canonical_digest(result, without="result_digest")
    return result


class UnavailableWorker:
    """Explicit placeholder for a solver not installed in this deployment."""

    def __init__(self, analysis_kind: str, *, version: str = "contract-only"):
        self.name = f"unavailable:{analysis_kind}"
        self.version = version
        self.analysis_kind = analysis_kind

    def run(self, job: dict[str, Any]) -> dict[str, Any]:
        result = _base_result(job, self)
        result["status"] = "unavailable"
        result["warnings"] = [
            f"No exact {self.analysis_kind} worker is installed; no physics result was inferred."
        ]
        result["evidence"]["worker_status"] = "not_installed"
        return _finish(result)


class _Csr(ctypes.Structure):
    _fields_ = [
        ("abiVersion", ctypes.c_uint32),
        ("structSize", ctypes.c_uint32),
        ("rows", ctypes.c_int32),
        ("cols", ctypes.c_int32),
        ("nnz", ctypes.c_int32),
        ("rowOffsets", ctypes.POINTER(ctypes.c_int32)),
        ("columns", ctypes.POINTER(ctypes.c_int32)),
        ("values", ctypes.POINTER(ctypes.c_double)),
    ]


class _Options(ctypes.Structure):
    _fields_ = [
        ("abiVersion", ctypes.c_uint32),
        ("structSize", ctypes.c_uint32),
        ("backend", ctypes.c_int32),
        ("maxIterations", ctypes.c_int32),
        ("tolerance", ctypes.c_double),
        ("requireSpd", ctypes.c_int32),
        ("reserved", ctypes.c_int32),
    ]


class _Result(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_int32),
        ("backend", ctypes.c_int32),
        ("converged", ctypes.c_int32),
        ("iterations", ctypes.c_int32),
        ("residualNorm", ctypes.c_double),
        ("solutionCount", ctypes.c_int32),
        ("reserved", ctypes.c_int32),
        ("message", ctypes.c_char * 192),
    ]


class _UniformCantileverModalInput(ctypes.Structure):
    _fields_ = [
        ("abiVersion", ctypes.c_uint32),
        ("structSize", ctypes.c_uint32),
        ("lengthM", ctypes.c_double),
        ("youngsModulusPa", ctypes.c_double),
        ("secondMomentM4", ctypes.c_double),
        ("crossSectionAreaM2", ctypes.c_double),
        ("densityKgPerM3", ctypes.c_double),
        ("modeCount", ctypes.c_int32),
        ("shapeSampleCount", ctypes.c_int32),
    ]


class _ModalResult(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_int32),
        ("backend", ctypes.c_int32),
        ("converged", ctypes.c_int32),
        ("modeCount", ctypes.c_int32),
        ("shapeSampleCount", ctypes.c_int32),
        ("reserved", ctypes.c_int32),
        ("message", ctypes.c_char * 192),
    ]


def _load_native_library(library_path: Path | None):
    candidates: list[Path]
    if library_path:
        candidates = [library_path]
    else:
        configured = os.environ.get("DESIGNSTUDIO_CORE_LIBRARY")
        candidates = [Path(configured)] if configured else []
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            library = ctypes.CDLL(str(candidate))
        except OSError:
            continue
        library.dc_mechanical_create.restype = ctypes.c_void_p
        library.dc_mechanical_destroy.argtypes = [ctypes.c_void_p]
        library.dc_mechanical_set_system.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_Csr), ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_double), ctypes.c_int32,
        ]
        library.dc_mechanical_solve.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Options)]
        library.dc_mechanical_result.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Result)]
        library.dc_mechanical_get_solution.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_double), ctypes.c_int32,
        ]
        try:
            modal = library.dc_mechanical_uniform_cantilever_modal
        except AttributeError:
            pass
        else:
            modal.argtypes = [
                ctypes.POINTER(_UniformCantileverModalInput),
                ctypes.POINTER(_ModalResult),
                ctypes.POINTER(ctypes.c_double), ctypes.c_int32,
                ctypes.POINTER(ctypes.c_double), ctypes.c_int32,
            ]
            modal.restype = ctypes.c_int32
        return library
    return None


class NativeLinearStaticWorker:
    """Adapter for the DesignCore native linear-static ABI.

    It intentionally exposes only convergence and solver-vector evidence. A
    converged linear solve is not treated as a product safety approval; stress,
    contact, fatigue and manufacturing gates remain separate workers.
    """

    name = "designcore-linear-static"
    version = "1"

    def __init__(self, library_path: str | Path | None = None):
        self.library_path = Path(library_path) if library_path else None

    def _load(self):
        return _load_native_library(self.library_path)

    @staticmethod
    def _backend(value: Any) -> int:
        return {"auto": 0, "cpu": 1, "cuda": 2}.get(str(value).lower(), -1)

    def run(self, job: dict[str, Any]) -> dict[str, Any]:
        result = _base_result(job, self)
        solver_input = job.get("solver_input")
        if not isinstance(solver_input, dict):
            result["status"] = "incomplete"
            result["warnings"] = ["linear_static requires a canonical CSR solver_input"]
            return _finish(result)
        library = self._load()
        if library is None:
            result["status"] = "unavailable"
            result["warnings"] = [
                "DesignCore shared library is not installed or could not be loaded; no result was inferred."
            ]
            result["evidence"]["worker_status"] = "native_library_unavailable"
            return _finish(result)

        try:
            csr_data = solver_input["csr"]
            rows = int(csr_data["rows"])
            cols = int(csr_data["cols"])
            offsets = [int(value) for value in csr_data["row_offsets"]]
            columns = [int(value) for value in csr_data["columns"]]
            values = [float(value) for value in csr_data["values"]]
            rhs = [float(value) for value in solver_input["rhs"]]
            fixed_dofs = [int(value) for value in solver_input.get("fixed_dofs", [])]
            fixed_values = [float(value) for value in solver_input.get("fixed_values", [])]
            if len(offsets) != rows + 1 or len(columns) != len(values) or len(rhs) != rows:
                raise ValueError("CSR arrays are inconsistent")
            if len(fixed_dofs) != len(fixed_values):
                raise ValueError("fixed dof arrays are inconsistent")
            c_offsets = (ctypes.c_int32 * len(offsets))(*offsets)
            c_columns = (ctypes.c_int32 * len(columns))(*columns) if columns else None
            c_values = (ctypes.c_double * len(values))(*values) if values else None
            c_rhs = (ctypes.c_double * len(rhs))(*rhs)
            c_fixed = (ctypes.c_int32 * len(fixed_dofs))(*fixed_dofs) if fixed_dofs else None
            c_fixed_values = (ctypes.c_double * len(fixed_values))(*fixed_values) if fixed_values else None
            matrix = _Csr(1, ctypes.sizeof(_Csr), rows, cols, len(values),
                          c_offsets, c_columns, c_values)
            handle = library.dc_mechanical_create()
            if not handle:
                raise RuntimeError("dc_mechanical_create failed")
            try:
                set_status = library.dc_mechanical_set_system(
                    handle, ctypes.byref(matrix), c_rhs, c_fixed, c_fixed_values, len(fixed_dofs)
                )
                if set_status != 0:
                    raise RuntimeError(f"dc_mechanical_set_system returned {set_status}")
                backend = self._backend(job.get("solver", {}).get("backend", "auto"))
                if backend < 0:
                    raise ValueError("solver.backend must be auto, cpu, or cuda")
                options_data = job.get("solver", {})
                options = _Options(
                    1,
                    ctypes.sizeof(_Options),
                    backend,
                    int(options_data.get("max_iterations", 10_000)),
                    float(options_data.get("tolerance", 1.0e-9)),
                    1,
                    0,
                )
                solve_status = library.dc_mechanical_solve(handle, ctypes.byref(options))
                native_result = _Result()
                library.dc_mechanical_result(handle, ctypes.byref(native_result))
                message = bytes(native_result.message).split(b"\0", 1)[0].decode("utf-8", "replace")
                count = max(0, int(native_result.solutionCount))
                solution = (ctypes.c_double * count)() if count else None
                if solution is not None:
                    library.dc_mechanical_get_solution(handle, solution, count)
                values_out = list(solution) if solution is not None else []
                result["status"] = "pass" if solve_status == 0 and native_result.converged else "fail"
                result["metrics"] = {
                    "residual_norm": float(native_result.residualNorm),
                    "solution_l2_norm": sum(value * value for value in values_out) ** 0.5,
                    "solution_count": count,
                    "iterations": int(native_result.iterations),
                }
                result["evidence"].update({
                    "analysis_available": True,
                    "native_status": int(solve_status),
                    "solver_message": message,
                    "solution_sha256": hashlib.sha256(
                        json.dumps(values_out, separators=(",", ":")).encode("utf-8")
                    ).hexdigest(),
                })
                result["warnings"] = [
                    "Linear-static convergence alone does not satisfy stress, fatigue, contact, or manufacturing qualification."
                ]
                # The host qualification workflow must explicitly prove every
                # required metric. The native solve never grants acceptance itself.
                result["release_eligible"] = bool(
                    result["status"] == "pass"
                    and job.get("qualification_contract", {}).get(
                        "required_metrics_satisfied") is True
                )
                return _finish(result)
            finally:
                library.dc_mechanical_destroy(handle)
        except (KeyError, TypeError, ValueError, OverflowError, RuntimeError) as error:
            result["status"] = "fail"
            result["warnings"] = [f"native linear-static worker rejected the input: {error}"]
            result["evidence"]["worker_status"] = "input_or_runtime_error"
            return _finish(result)


class NativeUniformCantileverModalWorker:
    """Native analytical modal screening for one uniform cantilever beam."""

    name = "designcore-uniform-cantilever-modal"
    version = "1"

    def __init__(self, library_path: str | Path | None = None):
        self.library_path = Path(library_path) if library_path else None

    def run(self, job: dict[str, Any]) -> dict[str, Any]:
        result = _base_result(job, self)
        solver_input = job.get("solver_input")
        model = solver_input.get("uniform_cantilever") if isinstance(solver_input, dict) else None
        if not isinstance(model, dict):
            result["warnings"] = [
                "modal_vibration screening requires a canonical uniform_cantilever input"
            ]
            return _finish(result)
        if str(job.get("solver", {}).get("backend", "cpu")).lower() not in {"auto", "cpu"}:
            result["status"] = "fail"
            result["warnings"] = [
                "The analytical uniform-beam modal reference is CPU-only; use a mesh eigensolver "
                "worker before requesting GPU execution."
            ]
            return _finish(result)
        library = _load_native_library(self.library_path)
        if library is None or not hasattr(library, "dc_mechanical_uniform_cantilever_modal"):
            result["status"] = "unavailable"
            result["warnings"] = [
                "DesignCore modal ABI is unavailable; no vibration result was inferred."
            ]
            result["evidence"]["worker_status"] = "native_modal_abi_unavailable"
            return _finish(result)
        try:
            mode_count = int(model.get("mode_count", 3))
            sample_count = int(model.get("shape_sample_count", 21))
            native_input = _UniformCantileverModalInput(
                1,
                ctypes.sizeof(_UniformCantileverModalInput),
                float(model["length_m"]),
                float(model["youngs_modulus_pa"]),
                float(model["second_moment_m4"]),
                float(model["cross_section_area_m2"]),
                float(model["density_kg_per_m3"]),
                mode_count,
                sample_count,
            )
            frequencies = (ctypes.c_double * mode_count)()
            shapes = (ctypes.c_double * (mode_count * sample_count))()
            native_result = _ModalResult()
            status = library.dc_mechanical_uniform_cantilever_modal(
                ctypes.byref(native_input), ctypes.byref(native_result),
                frequencies, mode_count, shapes, mode_count * sample_count,
            )
            message = bytes(native_result.message).split(b"\0", 1)[0].decode(
                "utf-8", "replace")
            frequency_values = list(frequencies) if status == 0 else []
            shape_values = list(shapes) if status == 0 else []
            result["status"] = "pass" if status == 0 and native_result.converged else "fail"
            result["metrics"] = {
                "mode_count": len(frequency_values),
                "frequencies_hz": frequency_values,
                "first_mode_hz": frequency_values[0] if frequency_values else None,
                "shape_sample_count": sample_count if frequency_values else 0,
            }
            result["evidence"].update({
                "analysis_available": status == 0,
                "native_status": int(status),
                "solver_message": message,
                "model_sha256": canonical_digest(model),
                "mode_shapes_sha256": hashlib.sha256(
                    json.dumps(shape_values, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
                "formulation_scope": "uniform_slender_prismatic_euler_bernoulli_cantilever",
            })
            result["warnings"] = [
                "This analytical beam screen excludes robot joints, bearings, gearbox compliance, "
                "contacts, attached masses and pose-dependent assembly modes."
            ]
            result["release_eligible"] = False
            return _finish(result)
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            result["status"] = "fail"
            result["warnings"] = [f"native modal worker rejected the input: {error}"]
            result["evidence"]["worker_status"] = "input_or_runtime_error"
            return _finish(result)


class SolverRegistry:
    """Dispatches approved geometry to one exact worker per analysis kind."""

    def __init__(self, workers: dict[str, AnalysisWorker] | None = None):
        self.workers: dict[str, AnalysisWorker] = dict(workers or {})

    @classmethod
    def default(cls, library_path: str | Path | None = None) -> "SolverRegistry":
        registry = cls({
            "linear_static": NativeLinearStaticWorker(library_path),
            "modal_vibration": NativeUniformCantileverModalWorker(library_path),
        })
        for kind in ANALYSIS_KINDS:
            registry.workers.setdefault(kind, UnavailableWorker(kind))
        return registry

    def register(self, analysis_kind: str, worker: AnalysisWorker) -> None:
        if analysis_kind not in ANALYSIS_KINDS:
            raise WorkerContractError(f"unsupported analysis kind: {analysis_kind}")
        self.workers[analysis_kind] = worker

    def submit(self, job: dict[str, Any]) -> dict[str, Any]:
        validate_approved_job(job)
        worker = self.workers.get(job["analysis_kind"])
        if worker is None:
            raise WorkerContractError(f"no worker registered for {job['analysis_kind']}")
        return worker.run(job)


__all__ = [
    "ANALYSIS_KINDS",
    "AnalysisWorker",
    "NativeLinearStaticWorker",
    "NativeUniformCantileverModalWorker",
    "SolverRegistry",
    "UnavailableWorker",
    "WorkerContractError",
    "canonical_digest",
    "validate_approved_job",
]

#include "designcore/mechanical.h"
#include "mechanical_internal.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <utility>

namespace dc::mechanical {

bool CsrMatrix::valid(std::string* diagnostic) const {
    auto fail = [diagnostic](const char* message) {
        if (diagnostic) *diagnostic = message;
        return false;
    };
    if (rows <= 0 || cols <= 0) return fail("CSR dimensions must be positive");
    if (rowOffsets.size() != static_cast<std::size_t>(rows) + 1)
        return fail("CSR rowOffsets must contain rows + 1 entries");
    if (rowOffsets.front() != 0 || rowOffsets.back() != static_cast<std::int32_t>(values.size())
        || columns.size() != values.size())
        return fail("CSR offsets and value arrays are inconsistent");
    for (std::int32_t row = 0; row < rows; ++row) {
        const auto begin = rowOffsets[static_cast<std::size_t>(row)];
        const auto end = rowOffsets[static_cast<std::size_t>(row) + 1];
        if (begin < 0 || end < begin || end > static_cast<std::int32_t>(values.size()))
            return fail("CSR row offsets are not monotonic");
        for (std::int32_t k = begin; k < end; ++k) {
            const auto col = columns[static_cast<std::size_t>(k)];
            if (col < 0 || col >= cols) return fail("CSR column is outside matrix dimensions");
            if (!std::isfinite(values[static_cast<std::size_t>(k)]))
                return fail("CSR values must be finite");
        }
    }
    return true;
}

bool MechanicalEngine::cudaAvailable() { return detail::cudaAvailable(); }

SolveResult MechanicalEngine::solveLinearStatic(const LinearSystem& system,
                                                const SolverOptions& options) const {
    SolveResult invalid;
    invalid.backend = options.backend == SolverBackend::Cuda
        ? SolverBackend::Cuda : SolverBackend::Cpu;

    if (options.maxIterations <= 0 || !std::isfinite(options.tolerance)
        || options.tolerance <= 0.0) {
        invalid.diagnostic = "solver options require positive finite tolerance and iterations";
        return invalid;
    }
    if (!options.requireSpd) {
        invalid.diagnostic = "linear-static reference solver requires an SPD stiffness matrix";
        return invalid;
    }

    detail::PreparedSystem prepared;
    if (!detail::prepareSystem(system, prepared, invalid.diagnostic)) return invalid;

    if (options.backend == SolverBackend::Cuda) {
        if (!detail::cudaAvailable()) {
            invalid.backend = SolverBackend::Cuda;
            invalid.diagnostic = "CUDA backend requested but no CUDA device is available";
            return invalid;
        }
        return detail::solveCuda(prepared, options);
    }
    if (options.backend == SolverBackend::Auto && detail::cudaAvailable()) {
        SolveResult gpu = detail::solveCuda(prepared, options);
        // Auto is a performance preference, not a correctness dependency. A
        // runtime CUDA failure falls back to the deterministic CPU reference.
        if (gpu.converged) return gpu;
    }
    return detail::solveCpu(prepared, options);
}

} // namespace dc::mechanical


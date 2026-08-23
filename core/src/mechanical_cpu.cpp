#include "mechanical_internal.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <sstream>
#include <utility>

namespace dc::mechanical::detail {
namespace {

using Row = std::vector<std::pair<std::int32_t, double>>;

bool finiteVector(const std::vector<double>& values) {
    return std::all_of(values.begin(), values.end(), [](double value) {
        return std::isfinite(value);
    });
}

double dot(const std::vector<double>& a, const std::vector<double>& b) {
    double sum = 0.0;
    for (std::size_t i = 0; i < a.size(); ++i) sum += a[i] * b[i];
    return sum;
}

void multiply(const CsrMatrix& matrix, const std::vector<double>& x,
              std::vector<double>& y) {
    y.assign(static_cast<std::size_t>(matrix.rows), 0.0);
    for (std::int32_t row = 0; row < matrix.rows; ++row) {
        double sum = 0.0;
        const auto begin = matrix.rowOffsets[static_cast<std::size_t>(row)];
        const auto end = matrix.rowOffsets[static_cast<std::size_t>(row) + 1];
        for (std::int32_t k = begin; k < end; ++k) {
            sum += matrix.values[static_cast<std::size_t>(k)]
                * x[static_cast<std::size_t>(matrix.columns[static_cast<std::size_t>(k)])];
        }
        y[static_cast<std::size_t>(row)] = sum;
    }
}

} // namespace

bool prepareSystem(const LinearSystem& input, PreparedSystem& output,
                   std::string& diagnostic) {
    if (!input.stiffness.valid(&diagnostic)) return false;
    const auto n = input.stiffness.rows;
    if (input.stiffness.cols != n || input.rhs.size() != static_cast<std::size_t>(n)) {
        diagnostic = "linear-static system must be square and rhs-sized";
        return false;
    }
    if (!finiteVector(input.rhs)) {
        diagnostic = "linear-static rhs must contain finite values";
        return false;
    }
    if (input.fixedDofs.size() != input.fixedValues.size()) {
        diagnostic = "fixedDofs and fixedValues must have equal lengths";
        return false;
    }

    std::vector<bool> fixed(static_cast<std::size_t>(n), false);
    for (std::size_t i = 0; i < input.fixedDofs.size(); ++i) {
        const auto dof = input.fixedDofs[i];
        if (dof < 0 || dof >= n || fixed[static_cast<std::size_t>(dof)]
            || !std::isfinite(input.fixedValues[i])) {
            diagnostic = "fixed degree of freedom is invalid or duplicated";
            return false;
        }
        fixed[static_cast<std::size_t>(dof)] = true;
    }

    std::vector<Row> rows(static_cast<std::size_t>(n));
    for (std::int32_t row = 0; row < n; ++row) {
        const auto begin = input.stiffness.rowOffsets[static_cast<std::size_t>(row)];
        const auto end = input.stiffness.rowOffsets[static_cast<std::size_t>(row) + 1];
        rows[static_cast<std::size_t>(row)].reserve(static_cast<std::size_t>(end - begin));
        for (std::int32_t k = begin; k < end; ++k) {
            rows[static_cast<std::size_t>(row)].emplace_back(
                input.stiffness.columns[static_cast<std::size_t>(k)],
                input.stiffness.values[static_cast<std::size_t>(k)]);
        }
    }

    output.rhs = input.rhs;
    for (std::int32_t row = 0; row < n; ++row) {
        auto& current = rows[static_cast<std::size_t>(row)];
        if (fixed[static_cast<std::size_t>(row)]) {
            const auto position = std::find(input.fixedDofs.begin(), input.fixedDofs.end(), row);
            const auto index = static_cast<std::size_t>(position - input.fixedDofs.begin());
            current.clear();
            current.emplace_back(row, 1.0);
            output.rhs[static_cast<std::size_t>(row)] = input.fixedValues[index];
            continue;
        }

        Row filtered;
        filtered.reserve(current.size());
        for (const auto& entry : current) {
            const auto col = entry.first;
            if (fixed[static_cast<std::size_t>(col)]) {
                const auto position = std::find(input.fixedDofs.begin(), input.fixedDofs.end(), col);
                const auto index = static_cast<std::size_t>(position - input.fixedDofs.begin());
                output.rhs[static_cast<std::size_t>(row)] -= entry.second * input.fixedValues[index];
            } else {
                filtered.push_back(entry);
            }
        }
        current = std::move(filtered);
    }

    // Conjugate gradient is only valid for a symmetric system.  Check the
    // prepared matrix explicitly so an accidental nonsymmetric assembly does
    // not produce a plausible-looking but invalid displacement field.
    for (std::int32_t row = 0; row < n; ++row) {
        for (const auto& entry : rows[static_cast<std::size_t>(row)]) {
            const auto col = entry.first;
            const auto counterpart = std::find_if(
                rows[static_cast<std::size_t>(col)].begin(),
                rows[static_cast<std::size_t>(col)].end(),
                [row](const auto& value) { return value.first == row; });
            if (counterpart == rows[static_cast<std::size_t>(col)].end()
                || std::abs(entry.second - counterpart->second)
                    > 1.0e-10 * std::max({1.0, std::abs(entry.second), std::abs(counterpart->second)})) {
                diagnostic = "linear-static stiffness matrix must be symmetric";
                return false;
            }
        }
    }

    output.stiffness.rows = n;
    output.stiffness.cols = n;
    output.stiffness.columns.clear();
    output.stiffness.values.clear();
    output.stiffness.rowOffsets.assign(static_cast<std::size_t>(n) + 1, 0);
    for (std::int32_t row = 0; row < n; ++row) {
        output.stiffness.rowOffsets[static_cast<std::size_t>(row) + 1] =
            output.stiffness.rowOffsets[static_cast<std::size_t>(row)]
            + static_cast<std::int32_t>(rows[static_cast<std::size_t>(row)].size());
        for (const auto& entry : rows[static_cast<std::size_t>(row)]) {
            output.stiffness.columns.push_back(entry.first);
            output.stiffness.values.push_back(entry.second);
        }
    }
    return true;
}

SolveResult solveCpu(const PreparedSystem& system, const SolverOptions& options) {
    SolveResult result;
    result.backend = SolverBackend::Cpu;
    result.solution.assign(static_cast<std::size_t>(system.stiffness.rows), 0.0);

    const auto n = system.stiffness.rows;
    std::vector<double> residual = system.rhs;
    std::vector<double> direction = residual;
    std::vector<double> product;
    const double rhsNorm = std::sqrt(std::max(0.0, dot(system.rhs, system.rhs)));
    const double target = std::max(options.tolerance,
                                   options.tolerance * std::max(1.0, rhsNorm));
    double residualSquared = dot(residual, residual);
    result.residualNorm = std::sqrt(std::max(0.0, residualSquared));
    if (result.residualNorm <= target) {
        result.converged = true;
        result.diagnostic = "CPU reference solver converged at the zero initial guess";
        return result;
    }

    for (std::int32_t iteration = 1; iteration <= options.maxIterations; ++iteration) {
        multiply(system.stiffness, direction, product);
        const double denominator = dot(direction, product);
        if (!std::isfinite(denominator) || denominator <= std::numeric_limits<double>::epsilon()) {
            result.iterations = iteration - 1;
            result.diagnostic = "stiffness matrix is not positive definite for CG";
            return result;
        }
        const double alpha = residualSquared / denominator;
        for (std::int32_t i = 0; i < n; ++i) {
            result.solution[static_cast<std::size_t>(i)] += alpha * direction[static_cast<std::size_t>(i)];
            residual[static_cast<std::size_t>(i)] -= alpha * product[static_cast<std::size_t>(i)];
        }
        const double nextResidualSquared = dot(residual, residual);
        result.iterations = iteration;
        result.residualNorm = std::sqrt(std::max(0.0, nextResidualSquared));
        if (!std::isfinite(result.residualNorm)) {
            result.diagnostic = "CPU solver produced a non-finite residual";
            return result;
        }
        if (result.residualNorm <= target) {
            result.converged = true;
            result.diagnostic = "CPU reference conjugate-gradient solver converged";
            return result;
        }
        const double beta = nextResidualSquared / residualSquared;
        for (std::int32_t i = 0; i < n; ++i) {
            direction[static_cast<std::size_t>(i)] =
                residual[static_cast<std::size_t>(i)]
                + beta * direction[static_cast<std::size_t>(i)];
        }
        residualSquared = nextResidualSquared;
    }
    result.diagnostic = "CPU reference solver reached the iteration limit";
    return result;
}

#if !defined(DESIGNCORE_HAS_CUDA)
bool cudaAvailable() { return false; }

SolveResult solveCuda(const PreparedSystem&, const SolverOptions&) {
    SolveResult result;
    result.backend = SolverBackend::Cuda;
    result.diagnostic = "DesignCore was built without the optional CUDA backend";
    return result;
}
#endif

} // namespace dc::mechanical::detail

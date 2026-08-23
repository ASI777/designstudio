#include "mechanical_internal.h"

#include <cuda_runtime.h>
#include <cublas_v2.h>

#include <algorithm>
#include <cmath>
#include <string>

namespace dc::mechanical::detail {
namespace {

__global__ void csrSpmvKernel(int rows, const int* rowOffsets, const int* columns,
                              const double* values, const double* x, double* y) {
    const int row = blockIdx.x * blockDim.x + threadIdx.x;
    if (row >= rows) return;
    double sum = 0.0;
    for (int k = rowOffsets[row]; k < rowOffsets[row + 1]; ++k)
        sum += values[k] * x[columns[k]];
    y[row] = sum;
}

bool cudaOk(cudaError_t code) { return code == cudaSuccess; }
bool blasOk(cublasStatus_t code) { return code == CUBLAS_STATUS_SUCCESS; }

} // namespace

bool cudaAvailable() {
    int count = 0;
    const auto status = cudaGetDeviceCount(&count);
    if (status != cudaSuccess) {
        cudaGetLastError();
        return false;
    }
    return count > 0;
}

SolveResult solveCuda(const PreparedSystem& system, const SolverOptions& options) {
    SolveResult result;
    result.backend = SolverBackend::Cuda;
    const int n = system.stiffness.rows;
    result.solution.assign(static_cast<std::size_t>(n), 0.0);

    int* dRows = nullptr;
    int* dColumns = nullptr;
    double* dValues = nullptr;
    double* dRhs = nullptr;
    double* dResidual = nullptr;
    double* dDirection = nullptr;
    double* dProduct = nullptr;
    cublasHandle_t blas = nullptr;
    auto cleanup = [&]() {
        if (blas) cublasDestroy(blas);
        cudaFree(dRows);
        cudaFree(dColumns);
        cudaFree(dValues);
        cudaFree(dRhs);
        cudaFree(dResidual);
        cudaFree(dDirection);
        cudaFree(dProduct);
    };
    auto fail = [&](const char* message) {
        result.diagnostic = message;
        cleanup();
        return result;
    };

    const auto& matrix = system.stiffness;
    const auto nnz = static_cast<std::size_t>(matrix.values.size());
    if (!cudaOk(cudaMalloc(reinterpret_cast<void**>(&dRows),
                           matrix.rowOffsets.size() * sizeof(int)))
        || (nnz > 0 && (!cudaOk(cudaMalloc(reinterpret_cast<void**>(&dColumns), nnz * sizeof(int)))
                        || !cudaOk(cudaMalloc(reinterpret_cast<void**>(&dValues), nnz * sizeof(double)))))
        || !cudaOk(cudaMalloc(reinterpret_cast<void**>(&dRhs), static_cast<std::size_t>(n) * sizeof(double)))
        || !cudaOk(cudaMalloc(reinterpret_cast<void**>(&dResidual), static_cast<std::size_t>(n) * sizeof(double)))
        || !cudaOk(cudaMalloc(reinterpret_cast<void**>(&dDirection), static_cast<std::size_t>(n) * sizeof(double)))
        || !cudaOk(cudaMalloc(reinterpret_cast<void**>(&dProduct), static_cast<std::size_t>(n) * sizeof(double)))) {
        return fail("CUDA allocation failed for the sparse mechanical system");
    }
    if (!cudaOk(cudaMemcpy(dRows, matrix.rowOffsets.data(), matrix.rowOffsets.size() * sizeof(int), cudaMemcpyHostToDevice))
        || (nnz > 0 && (!cudaOk(cudaMemcpy(dColumns, matrix.columns.data(), nnz * sizeof(int), cudaMemcpyHostToDevice))
                        || !cudaOk(cudaMemcpy(dValues, matrix.values.data(), nnz * sizeof(double), cudaMemcpyHostToDevice))))
        || !cudaOk(cudaMemcpy(dRhs, system.rhs.data(), static_cast<std::size_t>(n) * sizeof(double), cudaMemcpyHostToDevice))
        || !cudaOk(cudaMemcpy(dResidual, system.rhs.data(), static_cast<std::size_t>(n) * sizeof(double), cudaMemcpyHostToDevice))
        || !cudaOk(cudaMemcpy(dDirection, system.rhs.data(), static_cast<std::size_t>(n) * sizeof(double), cudaMemcpyHostToDevice))) {
        return fail("CUDA upload failed for the sparse mechanical system");
    }
    if (!blasOk(cublasCreate(&blas))) return fail("cuBLAS initialization failed");

    double rhsSquared = 0.0;
    if (!blasOk(cublasDdot(blas, n, dRhs, 1, dRhs, 1, &rhsSquared)))
        return fail("cuBLAS rhs norm failed");
    const double rhsNorm = std::sqrt(std::max(0.0, rhsSquared));
    const double target = std::max(options.tolerance,
                                   options.tolerance * std::max(1.0, rhsNorm));
    // dRhs is reused as the solution buffer after the initial norm is known.
    // The initial displacement vector is zero, just as in the CPU reference.
    if (!cudaOk(cudaMemset(dRhs, 0, static_cast<std::size_t>(n) * sizeof(double))))
        return fail("CUDA solution initialization failed");
    double residualSquared = rhsSquared;
    result.residualNorm = std::sqrt(std::max(0.0, residualSquared));
    const int blockSize = 128;
    const int blockCount = (n + blockSize - 1) / blockSize;
    if (result.residualNorm <= target) {
        result.converged = true;
        result.diagnostic = "CUDA conjugate-gradient solver converged at the zero initial guess";
        cleanup();
        return result;
    }

    for (std::int32_t iteration = 1; iteration <= options.maxIterations; ++iteration) {
        csrSpmvKernel<<<blockCount, blockSize>>>(n, dRows, dColumns, dValues, dDirection, dProduct);
        if (!cudaOk(cudaGetLastError()) || !cudaOk(cudaDeviceSynchronize()))
            return fail("CUDA CSR matrix-vector product failed");
        double denominator = 0.0;
        if (!blasOk(cublasDdot(blas, n, dDirection, 1, dProduct, 1, &denominator))
            || !std::isfinite(denominator) || denominator <= 0.0) {
            result.iterations = iteration - 1;
            return fail("CUDA stiffness matrix is not positive definite for CG");
        }
        const double alpha = residualSquared / denominator;
        const double negativeAlpha = -alpha;
        if (!blasOk(cublasDaxpy(blas, n, &alpha, dDirection, 1, dRhs, 1))
            || !blasOk(cublasDaxpy(blas, n, &negativeAlpha, dProduct, 1, dResidual, 1)))
            return fail("CUDA vector update failed");

        double nextResidualSquared = 0.0;
        if (!blasOk(cublasDdot(blas, n, dResidual, 1, dResidual, 1, &nextResidualSquared)))
            return fail("CUDA residual norm failed");
        result.iterations = iteration;
        result.residualNorm = std::sqrt(std::max(0.0, nextResidualSquared));
        if (!std::isfinite(result.residualNorm)) return fail("CUDA solver produced a non-finite residual");
        if (result.residualNorm <= target) {
            result.converged = true;
            result.diagnostic = "CUDA conjugate-gradient solver converged";
            break;
        }
        const double beta = nextResidualSquared / residualSquared;
        const double one = 1.0;
        if (!blasOk(cublasDscal(blas, n, &beta, dDirection, 1))
            || !blasOk(cublasDaxpy(blas, n, &one, dResidual, 1, dDirection, 1))) {
            return fail("CUDA search-direction update failed");
        }
        residualSquared = nextResidualSquared;
    }
    if (!result.converged) result.diagnostic = "CUDA solver reached the iteration limit";

    if (!cudaOk(cudaMemcpy(result.solution.data(), dRhs,
                           static_cast<std::size_t>(n) * sizeof(double), cudaMemcpyDeviceToHost)))
        return fail("CUDA solution download failed");
    cleanup();
    return result;
}

} // namespace dc::mechanical::detail

#include "designcore/linalg.h"

#include <cmath>
#include <vector>

namespace dc {

// Doolittle LU with partial pivoting, in place on A (row-major). The pivot
// clamp (1e-300) matches MoM2D's managed LU so native and fallback agree to the
// bit on well-conditioned systems and degrade identically on singular ones.
bool dense_lu_solve(double* A, std::int32_t n, const double* B, std::int32_t rhs, double* X) {
    if (n <= 0 || rhs < 0 || A == nullptr || (rhs > 0 && (B == nullptr || X == nullptr))) {
        return false;
    }

    std::vector<std::int32_t> piv(static_cast<std::size_t>(n));
    for (std::int32_t i = 0; i < n; ++i) piv[static_cast<std::size_t>(i)] = i;

    auto at = [A, n](std::int32_t r, std::int32_t c) -> double& {
        return A[static_cast<std::size_t>(r) * static_cast<std::size_t>(n) + static_cast<std::size_t>(c)];
    };

    for (std::int32_t k = 0; k < n; ++k) {
        std::int32_t p = k;
        double maxv = std::fabs(at(k, k));
        for (std::int32_t i = k + 1; i < n; ++i) {
            double v = std::fabs(at(i, k));
            if (v > maxv) { maxv = v; p = i; }
        }
        if (p != k) {
            for (std::int32_t j = 0; j < n; ++j) {
                double tmp = at(k, j); at(k, j) = at(p, j); at(p, j) = tmp;
            }
            std::int32_t t = piv[static_cast<std::size_t>(k)];
            piv[static_cast<std::size_t>(k)] = piv[static_cast<std::size_t>(p)];
            piv[static_cast<std::size_t>(p)] = t;
        }
        double akk = at(k, k);
        if (std::fabs(akk) < 1e-300) akk = 1e-300;
        for (std::int32_t i = k + 1; i < n; ++i) {
            double f = at(i, k) / akk;
            at(i, k) = f;
            for (std::int32_t j = k + 1; j < n; ++j) at(i, j) -= f * at(k, j);
        }
    }

    std::vector<double> y(static_cast<std::size_t>(n));
    for (std::int32_t c = 0; c < rhs; ++c) {
        const double* b = B + static_cast<std::size_t>(c) * static_cast<std::size_t>(n);
        double* x = X + static_cast<std::size_t>(c) * static_cast<std::size_t>(n);
        // forward substitution with the pivoted RHS
        for (std::int32_t i = 0; i < n; ++i) {
            double s = b[piv[static_cast<std::size_t>(i)]];
            for (std::int32_t j = 0; j < i; ++j) s -= at(i, j) * y[static_cast<std::size_t>(j)];
            y[static_cast<std::size_t>(i)] = s;
        }
        // back substitution
        for (std::int32_t i = n - 1; i >= 0; --i) {
            double s = y[static_cast<std::size_t>(i)];
            for (std::int32_t j = i + 1; j < n; ++j) s -= at(i, j) * x[static_cast<std::size_t>(j)];
            double d = at(i, i);
            if (std::fabs(d) < 1e-300) d = 1e-300;
            x[static_cast<std::size_t>(i)] = s / d;
        }
    }
    return true;
}

} // namespace dc

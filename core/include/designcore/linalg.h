#pragma once
// Dense linear algebra for the SI hot path (E20). The Method-of-Moments RLGC
// extraction (MoM2D) and the FDM PDN solver spend almost all of their time in a
// dense LU factor + solve of an N×N system (N≈400 for a meshed cross-section),
// repeated per frequency × per net. This is the native, cache-friendly version
// of that inner solve; the managed DenseSolver falls back to its own LU when the
// native library isn't present, so results are identical either way.

#include <cstdint>

namespace dc {

// Solve A·X = B for `rhs` right-hand sides by LU with partial pivoting.
//   A   : n*n, row-major (A[row*n + col]); overwritten with its LU factors.
//   B   : n*rhs, column-major blocks (rhs c occupies B[c*n + i]).
//   X   : n*rhs, same layout as B; receives the solutions.
// Returns true on success, false on invalid sizes. A near-singular pivot is
// clamped (not failed) to mirror the managed reference exactly.
bool dense_lu_solve(double* A, std::int32_t n, const double* B, std::int32_t rhs, double* X);

} // namespace dc

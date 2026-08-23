#pragma once

#include "designcore/mechanical.h"

namespace dc::mechanical::detail {

struct PreparedSystem {
    CsrMatrix stiffness;
    std::vector<double> rhs;
};

bool prepareSystem(const LinearSystem& input, PreparedSystem& output,
                   std::string& diagnostic);

SolveResult solveCpu(const PreparedSystem& system, const SolverOptions& options);

bool cudaAvailable();
SolveResult solveCuda(const PreparedSystem& system, const SolverOptions& options);

} // namespace dc::mechanical::detail


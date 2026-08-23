#pragma once

// Backend-neutral mechanical analysis contracts.  The CPU implementation is
// the reference path used for deterministic validation; an optional CUDA
// implementation consumes the same prepared CSR system and returns the same
// result shape.  Higher-level CAD and AI services should depend on these
// contracts rather than on a particular vendor solver.

#include <cstdint>
#include <string>
#include <vector>

namespace dc::mechanical {

enum class SolverBackend : std::int32_t {
    Auto = 0,
    Cpu = 1,
    Cuda = 2,
};

enum class AnalysisKind : std::int32_t {
    LinearStatic = 0,
    Modal = 1,
    Thermal = 2,
    NonlinearContact = 3,
    DropImpact = 4,
    PlasticCreepFatigue = 5,
    InjectionMoulding = 6,
};

struct CsrMatrix {
    std::int32_t rows = 0;
    std::int32_t cols = 0;
    std::vector<std::int32_t> rowOffsets;
    std::vector<std::int32_t> columns;
    std::vector<double> values;

    bool valid(std::string* diagnostic = nullptr) const;
};

struct LinearSystem {
    CsrMatrix stiffness;
    std::vector<double> rhs;
    // Dirichlet constraints are applied symmetrically before solving.  This
    // keeps the reference linear-static path suitable for FEA stiffness
    // matrices without requiring the caller to mutate its source matrix.
    std::vector<std::int32_t> fixedDofs;
    std::vector<double> fixedValues;
};

struct SolverOptions {
    SolverBackend backend = SolverBackend::Auto;
    std::int32_t maxIterations = 10'000;
    double tolerance = 1.0e-9;
    bool requireSpd = true;
};

struct SolveResult {
    bool converged = false;
    SolverBackend backend = SolverBackend::Cpu;
    std::int32_t iterations = 0;
    double residualNorm = 0.0;
    std::vector<double> solution;
    std::string diagnostic;
};

struct AxialBarElement {
    std::int32_t firstNode = 0;
    std::int32_t secondNode = 0;
    double lengthM = 0.0;
    double areaM2 = 0.0;
    double youngsModulusPa = 0.0;
};

struct AxialBarModel {
    std::int32_t nodeCount = 0;
    std::vector<AxialBarElement> elements;
    std::vector<double> nodalForcesN;
    std::vector<std::int32_t> fixedNodes;
    std::vector<double> fixedDisplacementsM;
};

struct AxialBarResult {
    bool converged = false;
    SolverBackend backend = SolverBackend::Cpu;
    std::vector<double> displacementsM;
    std::vector<double> reactionsN;
    std::vector<double> elementStrain;
    std::vector<double> elementStressPa;
    double strainEnergyJ = 0.0;
    std::string diagnostic;
};

struct CantileverBeamModel {
    double lengthM = 0.0;
    double youngsModulusPa = 0.0;
    double secondMomentM4 = 0.0;
    std::int32_t elementCount = 0;
    double tipForceN = 0.0;
    double tipMomentNm = 0.0;
};

struct CantileverBeamResult {
    bool converged = false;
    SolverBackend backend = SolverBackend::Cpu;
    std::vector<double> transverseDisplacementsM;
    std::vector<double> rotationsRad;
    double rootReactionForceN = 0.0;
    double rootReactionMomentNm = 0.0;
    double strainEnergyJ = 0.0;
    std::string diagnostic;
};

// Uniform Euler-Bernoulli cantilever screening model.  This is an exact
// analytical reference for slender prismatic beams, not a substitute for a
// full robot assembly modal model with joints, bearings and contacts.
struct UniformCantileverModalModel {
    double lengthM = 0.0;
    double youngsModulusPa = 0.0;
    double secondMomentM4 = 0.0;
    double crossSectionAreaM2 = 0.0;
    double densityKgPerM3 = 0.0;
    std::int32_t modeCount = 3;
    std::int32_t shapeSampleCount = 21;
};

struct UniformCantileverModalResult {
    bool converged = false;
    SolverBackend backend = SolverBackend::Cpu;
    std::vector<double> dimensionlessRoots;
    std::vector<double> angularFrequenciesRadPerS;
    std::vector<double> frequenciesHz;
    std::vector<double> samplePositionsNormalized;
    std::vector<std::vector<double>> normalizedModeShapes;
    std::string diagnostic;
};

class MechanicalEngine {
public:
    static bool cudaAvailable();

    SolveResult solveLinearStatic(const LinearSystem& system,
                                  const SolverOptions& options = {}) const;

    AxialBarResult solveAxialBar(const AxialBarModel& model,
                                const SolverOptions& options = {}) const;

    CantileverBeamResult solveEulerBernoulliCantilever(
        const CantileverBeamModel& model,
        const SolverOptions& options = {}) const;

    UniformCantileverModalResult solveUniformCantileverModes(
        const UniformCantileverModalModel& model,
        const SolverOptions& options = {}) const;
};

} // namespace dc::mechanical

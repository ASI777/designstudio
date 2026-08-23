#include "designcore/mechanical.h"

#include <algorithm>
#include <cmath>
#include <map>
#include <string>
#include <utility>
#include <vector>

namespace dc::mechanical {
namespace {

using SparseRows = std::vector<std::map<std::int32_t, double>>;

bool finite(double value) { return std::isfinite(value); }

void add(SparseRows& rows, std::int32_t row, std::int32_t column, double value) {
    rows[static_cast<std::size_t>(row)][column] += value;
}

CsrMatrix toCsr(const SparseRows& rows) {
    CsrMatrix result;
    result.rows = static_cast<std::int32_t>(rows.size());
    result.cols = result.rows;
    result.rowOffsets.reserve(rows.size() + 1);
    result.rowOffsets.push_back(0);
    for (const auto& row : rows) {
        for (const auto& [column, value] : row) {
            if (value != 0.0) {
                result.columns.push_back(column);
                result.values.push_back(value);
            }
        }
        result.rowOffsets.push_back(static_cast<std::int32_t>(result.values.size()));
    }
    return result;
}

std::vector<double> residual(const SparseRows& stiffness,
                             const std::vector<double>& solution,
                             const std::vector<double>& loads) {
    std::vector<double> result(stiffness.size(), 0.0);
    for (std::size_t row = 0; row < stiffness.size(); ++row) {
        double internal = 0.0;
        for (const auto& [column, value] : stiffness[row]) {
            internal += value * solution[static_cast<std::size_t>(column)];
        }
        result[row] = internal - loads[row];
    }
    return result;
}

double strainEnergy(const SparseRows& stiffness, const std::vector<double>& solution) {
    double result = 0.0;
    for (std::size_t row = 0; row < stiffness.size(); ++row) {
        for (const auto& [column, value] : stiffness[row]) {
            result += 0.5 * solution[row] * value
                * solution[static_cast<std::size_t>(column)];
        }
    }
    return result;
}

} // namespace

AxialBarResult MechanicalEngine::solveAxialBar(const AxialBarModel& model,
                                                const SolverOptions& options) const {
    AxialBarResult result;
    result.backend = options.backend == SolverBackend::Cuda
        ? SolverBackend::Cuda : SolverBackend::Cpu;
    if (model.nodeCount < 2
        || model.nodalForcesN.size() != static_cast<std::size_t>(model.nodeCount)
        || model.fixedNodes.size() != model.fixedDisplacementsM.size()
        || model.elements.empty()) {
        result.diagnostic = "axial-bar model has inconsistent node, load or constraint counts";
        return result;
    }
    if (!std::all_of(model.nodalForcesN.begin(), model.nodalForcesN.end(), finite)) {
        result.diagnostic = "axial-bar nodal forces must be finite";
        return result;
    }

    SparseRows stiffness(static_cast<std::size_t>(model.nodeCount));
    for (const auto& element : model.elements) {
        if (element.firstNode < 0 || element.secondNode < 0
            || element.firstNode >= model.nodeCount || element.secondNode >= model.nodeCount
            || element.firstNode == element.secondNode || !finite(element.lengthM)
            || !finite(element.areaM2) || !finite(element.youngsModulusPa)
            || element.lengthM <= 0.0 || element.areaM2 <= 0.0
            || element.youngsModulusPa <= 0.0) {
            result.diagnostic = "axial-bar element indices and properties must be valid and positive";
            return result;
        }
        const double stiffnessNPerM = element.youngsModulusPa * element.areaM2
            / element.lengthM;
        add(stiffness, element.firstNode, element.firstNode, stiffnessNPerM);
        add(stiffness, element.firstNode, element.secondNode, -stiffnessNPerM);
        add(stiffness, element.secondNode, element.firstNode, -stiffnessNPerM);
        add(stiffness, element.secondNode, element.secondNode, stiffnessNPerM);
    }

    LinearSystem system;
    system.stiffness = toCsr(stiffness);
    system.rhs = model.nodalForcesN;
    system.fixedDofs = model.fixedNodes;
    system.fixedValues = model.fixedDisplacementsM;
    const auto solved = solveLinearStatic(system, options);
    result.backend = solved.backend;
    result.diagnostic = solved.diagnostic;
    if (!solved.converged) return result;

    result.converged = true;
    result.displacementsM = solved.solution;
    result.reactionsN = residual(stiffness, solved.solution, model.nodalForcesN);
    result.elementStrain.reserve(model.elements.size());
    result.elementStressPa.reserve(model.elements.size());
    for (const auto& element : model.elements) {
        const double strain = (
            solved.solution[static_cast<std::size_t>(element.secondNode)]
            - solved.solution[static_cast<std::size_t>(element.firstNode)]) / element.lengthM;
        result.elementStrain.push_back(strain);
        result.elementStressPa.push_back(element.youngsModulusPa * strain);
    }
    result.strainEnergyJ = strainEnergy(stiffness, solved.solution);
    return result;
}

CantileverBeamResult MechanicalEngine::solveEulerBernoulliCantilever(
    const CantileverBeamModel& model, const SolverOptions& options) const {
    CantileverBeamResult result;
    result.backend = options.backend == SolverBackend::Cuda
        ? SolverBackend::Cuda : SolverBackend::Cpu;
    if (!finite(model.lengthM) || !finite(model.youngsModulusPa)
        || !finite(model.secondMomentM4) || !finite(model.tipForceN)
        || !finite(model.tipMomentNm) || model.lengthM <= 0.0
        || model.youngsModulusPa <= 0.0 || model.secondMomentM4 <= 0.0
        || model.elementCount < 1 || model.elementCount > 100'000) {
        result.diagnostic = "cantilever properties must be finite and positive with a valid mesh count";
        return result;
    }

    const std::int32_t nodeCount = model.elementCount + 1;
    const std::int32_t dofCount = nodeCount * 2;
    const double elementLength = model.lengthM / static_cast<double>(model.elementCount);
    const double factor = model.youngsModulusPa * model.secondMomentM4
        / (elementLength * elementLength * elementLength);
    const double length = elementLength;
    const double local[4][4] = {
        {12.0, 6.0 * length, -12.0, 6.0 * length},
        {6.0 * length, 4.0 * length * length, -6.0 * length, 2.0 * length * length},
        {-12.0, -6.0 * length, 12.0, -6.0 * length},
        {6.0 * length, 2.0 * length * length, -6.0 * length, 4.0 * length * length},
    };
    SparseRows stiffness(static_cast<std::size_t>(dofCount));
    for (std::int32_t element = 0; element < model.elementCount; ++element) {
        const std::int32_t dofs[4] = {
            2 * element, 2 * element + 1, 2 * element + 2, 2 * element + 3,
        };
        for (std::int32_t row = 0; row < 4; ++row) {
            for (std::int32_t column = 0; column < 4; ++column) {
                add(stiffness, dofs[row], dofs[column], factor * local[row][column]);
            }
        }
    }

    std::vector<double> loads(static_cast<std::size_t>(dofCount), 0.0);
    loads[static_cast<std::size_t>(dofCount - 2)] = model.tipForceN;
    loads[static_cast<std::size_t>(dofCount - 1)] = model.tipMomentNm;
    LinearSystem system;
    system.stiffness = toCsr(stiffness);
    system.rhs = loads;
    system.fixedDofs = {0, 1};
    system.fixedValues = {0.0, 0.0};
    const auto solved = solveLinearStatic(system, options);
    result.backend = solved.backend;
    result.diagnostic = solved.diagnostic;
    if (!solved.converged) return result;

    result.converged = true;
    result.transverseDisplacementsM.reserve(static_cast<std::size_t>(nodeCount));
    result.rotationsRad.reserve(static_cast<std::size_t>(nodeCount));
    for (std::int32_t node = 0; node < nodeCount; ++node) {
        result.transverseDisplacementsM.push_back(
            solved.solution[static_cast<std::size_t>(2 * node)]);
        result.rotationsRad.push_back(
            solved.solution[static_cast<std::size_t>(2 * node + 1)]);
    }
    const auto reactions = residual(stiffness, solved.solution, loads);
    result.rootReactionForceN = reactions[0];
    result.rootReactionMomentNm = reactions[1];
    result.strainEnergyJ = strainEnergy(stiffness, solved.solution);
    return result;
}

UniformCantileverModalResult MechanicalEngine::solveUniformCantileverModes(
    const UniformCantileverModalModel& model, const SolverOptions& options) const {
    UniformCantileverModalResult result;
    result.backend = SolverBackend::Cpu;
    if (options.backend == SolverBackend::Cuda) {
        result.backend = SolverBackend::Cuda;
        result.diagnostic = "uniform-beam analytical modal reference runs on CPU only";
        return result;
    }
    if (!finite(model.lengthM) || !finite(model.youngsModulusPa)
        || !finite(model.secondMomentM4) || !finite(model.crossSectionAreaM2)
        || !finite(model.densityKgPerM3) || model.lengthM <= 0.0
        || model.youngsModulusPa <= 0.0 || model.secondMomentM4 <= 0.0
        || model.crossSectionAreaM2 <= 0.0 || model.densityKgPerM3 <= 0.0
        || model.modeCount < 1 || model.modeCount > 5
        || model.shapeSampleCount < 3 || model.shapeSampleCount > 10'001) {
        result.diagnostic = "uniform-cantilever modal properties and sample counts must be valid";
        return result;
    }

    constexpr double roots[] = {
        1.875104068711961,
        4.694091132974175,
        7.854757438237613,
        10.995540734875467,
        14.137168391046471,
    };
    constexpr double pi = 3.141592653589793238462643383279502884;
    const double frequencyScale = std::sqrt(
        model.youngsModulusPa * model.secondMomentM4
        / (model.densityKgPerM3 * model.crossSectionAreaM2))
        / (model.lengthM * model.lengthM);
    result.samplePositionsNormalized.reserve(
        static_cast<std::size_t>(model.shapeSampleCount));
    for (std::int32_t sample = 0; sample < model.shapeSampleCount; ++sample) {
        result.samplePositionsNormalized.push_back(
            static_cast<double>(sample) / static_cast<double>(model.shapeSampleCount - 1));
    }
    for (std::int32_t mode = 0; mode < model.modeCount; ++mode) {
        const double root = roots[mode];
        const double omega = root * root * frequencyScale;
        result.dimensionlessRoots.push_back(root);
        result.angularFrequenciesRadPerS.push_back(omega);
        result.frequenciesHz.push_back(omega / (2.0 * pi));

        const double coefficient = (std::cosh(root) + std::cos(root))
            / (std::sinh(root) + std::sin(root));
        std::vector<double> shape;
        shape.reserve(static_cast<std::size_t>(model.shapeSampleCount));
        for (double normalizedPosition : result.samplePositionsNormalized) {
            const double coordinate = root * normalizedPosition;
            shape.push_back(std::cosh(coordinate) - std::cos(coordinate)
                - coefficient * (std::sinh(coordinate) - std::sin(coordinate)));
        }
        const double tip = shape.back();
        if (!finite(tip) || std::abs(tip) <= 1.0e-14) {
            result.diagnostic = "uniform-cantilever modal shape normalization failed";
            return result;
        }
        for (double& value : shape) value /= tip;
        result.normalizedModeShapes.push_back(std::move(shape));
    }
    result.converged = true;
    result.diagnostic = "analytical Euler-Bernoulli cantilever modes evaluated on CPU";
    return result;
}

} // namespace dc::mechanical

#include "designcore/c_api.h"
#include "designcore/mechanical.h"

#include <cassert>
#include <cmath>
#include <cstdint>
#include <iostream>

namespace {

void testCppReference() {
    dc::mechanical::LinearSystem system;
    system.stiffness.rows = 3;
    system.stiffness.cols = 3;
    system.stiffness.rowOffsets = {0, 2, 5, 7};
    system.stiffness.columns = {0, 1, 0, 1, 2, 1, 2};
    system.stiffness.values = {4.0, -1.0, -1.0, 4.0, -1.0, -1.0, 3.0};
    system.rhs = {0.0, 0.0, 5.0};
    system.fixedDofs = {0};
    system.fixedValues = {0.0};

    dc::mechanical::SolverOptions options;
    options.backend = dc::mechanical::SolverBackend::Cpu;
    options.tolerance = 1.0e-11;
    const auto result = dc::mechanical::MechanicalEngine().solveLinearStatic(system, options);
    assert(result.converged);
    assert(result.backend == dc::mechanical::SolverBackend::Cpu);
    assert(result.solution.size() == 3);
    assert(std::abs(result.solution[0]) < 1.0e-12);
    assert(result.solution[1] > 0.0 && result.solution[2] > result.solution[1]);
    assert(result.residualNorm < 1.0e-8);
}

void testCApi() {
    const std::int32_t rowOffsets[] = {0, 2, 5, 7};
    const std::int32_t columns[] = {0, 1, 0, 1, 2, 1, 2};
    const double values[] = {4.0, -1.0, -1.0, 4.0, -1.0, -1.0, 3.0};
    const double rhs[] = {0.0, 0.0, 5.0};
    const std::int32_t fixed[] = {0};
    const double fixedValues[] = {0.0};
    DcMechanicalCsr matrix{1, sizeof(DcMechanicalCsr), 3, 3, 7,
                           rowOffsets, columns, values};
    DcMechanicalHandle handle = dc_mechanical_create();
    assert(handle);
    assert(dc_mechanical_set_system(handle, &matrix, rhs, fixed, fixedValues, 1) == DC_OK);
    DcMechanicalOptions options{1, sizeof(DcMechanicalOptions), DC_MECHANICAL_CPU,
                                500, 1.0e-11, 1, 0};
    assert(dc_mechanical_solve(handle, &options) == DC_OK);
    DcMechanicalResult result{};
    assert(dc_mechanical_result(handle, &result) == DC_OK);
    assert(result.converged == 1);
    assert(result.solutionCount == 3);
    double solution[3]{};
    assert(dc_mechanical_get_solution(handle, solution, 3) == 3);
    assert(std::abs(solution[0]) < 1.0e-12);
    dc_mechanical_destroy(handle);
}

void testRejectsNonSymmetricSystem() {
    dc::mechanical::LinearSystem system;
    system.stiffness.rows = 2;
    system.stiffness.cols = 2;
    system.stiffness.rowOffsets = {0, 2, 4};
    system.stiffness.columns = {0, 1, 0, 1};
    system.stiffness.values = {2.0, -1.0, -0.5, 2.0};
    system.rhs = {1.0, 1.0};
    const auto result = dc::mechanical::MechanicalEngine().solveLinearStatic(system);
    assert(!result.converged);
    assert(result.diagnostic.find("symmetric") != std::string::npos);
}

void testAxialBarMatchesClosedForm() {
    constexpr double length = 2.0;
    constexpr double area = 4.0e-4;
    constexpr double youngsModulus = 70.0e9;
    constexpr double force = 12'000.0;
    dc::mechanical::AxialBarModel model;
    model.nodeCount = 3;
    model.elements = {
        {0, 1, length / 2.0, area, youngsModulus},
        {1, 2, length / 2.0, area, youngsModulus},
    };
    model.nodalForcesN = {0.0, 0.0, force};
    model.fixedNodes = {0};
    model.fixedDisplacementsM = {0.0};
    dc::mechanical::SolverOptions options;
    options.backend = dc::mechanical::SolverBackend::Cpu;
    options.tolerance = 1.0e-12;
    const auto result = dc::mechanical::MechanicalEngine().solveAxialBar(model, options);
    assert(result.converged);
    const double expectedDisplacement = force * length / (youngsModulus * area);
    const double expectedStress = force / area;
    const double expectedEnergy = force * expectedDisplacement / 2.0;
    assert(std::abs(result.displacementsM.back() - expectedDisplacement)
           < expectedDisplacement * 1.0e-9);
    assert(std::abs(result.reactionsN.front() + force) < force * 1.0e-10);
    assert(result.elementStressPa.size() == 2);
    assert(std::abs(result.elementStressPa[0] - expectedStress) < expectedStress * 1.0e-9);
    assert(std::abs(result.strainEnergyJ - expectedEnergy) < expectedEnergy * 1.0e-9);
}

void testCantileverMatchesClosedForm() {
    constexpr double length = 0.8;
    constexpr double youngsModulus = 69.0e9;
    constexpr double secondMoment = 2.1e-8;
    constexpr double force = -350.0;
    dc::mechanical::CantileverBeamModel model;
    model.lengthM = length;
    model.youngsModulusPa = youngsModulus;
    model.secondMomentM4 = secondMoment;
    model.elementCount = 8;
    model.tipForceN = force;
    dc::mechanical::SolverOptions options;
    options.backend = dc::mechanical::SolverBackend::Cpu;
    options.tolerance = 1.0e-11;
    const auto result = dc::mechanical::MechanicalEngine()
        .solveEulerBernoulliCantilever(model, options);
    assert(result.converged);
    const double expectedDisplacement = force * length * length * length
        / (3.0 * youngsModulus * secondMoment);
    const double expectedRotation = force * length * length
        / (2.0 * youngsModulus * secondMoment);
    assert(std::abs(result.transverseDisplacementsM.back() - expectedDisplacement)
           < std::abs(expectedDisplacement) * 1.0e-8);
    assert(std::abs(result.rotationsRad.back() - expectedRotation)
           < std::abs(expectedRotation) * 1.0e-8);
    assert(std::abs(result.rootReactionForceN + force) < std::abs(force) * 1.0e-8);
    assert(std::abs(result.rootReactionMomentNm + force * length)
           < std::abs(force * length) * 1.0e-8);
    assert(result.strainEnergyJ > 0.0);
}

void testMechanicalFormulationsRejectInvalidProperties() {
    dc::mechanical::AxialBarModel bar;
    bar.nodeCount = 2;
    bar.elements = {{0, 1, 1.0, -1.0, 70.0e9}};
    bar.nodalForcesN = {0.0, 1.0};
    bar.fixedNodes = {0};
    bar.fixedDisplacementsM = {0.0};
    assert(!dc::mechanical::MechanicalEngine().solveAxialBar(bar).converged);

    dc::mechanical::CantileverBeamModel beam;
    beam.lengthM = 1.0;
    beam.youngsModulusPa = 70.0e9;
    beam.secondMomentM4 = 1.0e-8;
    beam.elementCount = 0;
    assert(!dc::mechanical::MechanicalEngine()
        .solveEulerBernoulliCantilever(beam).converged);
}

void testUniformCantileverModesMatchAnalyticalReference() {
    dc::mechanical::UniformCantileverModalModel model;
    model.lengthM = 1.0;
    model.youngsModulusPa = 70.0e9;
    model.secondMomentM4 = 1.0e-8;
    model.crossSectionAreaM2 = 1.0e-3;
    model.densityKgPerM3 = 2700.0;
    model.modeCount = 3;
    model.shapeSampleCount = 21;
    dc::mechanical::SolverOptions options;
    options.backend = dc::mechanical::SolverBackend::Cpu;
    const auto result = dc::mechanical::MechanicalEngine()
        .solveUniformCantileverModes(model, options);
    assert(result.converged);
    assert(result.backend == dc::mechanical::SolverBackend::Cpu);
    assert(result.frequenciesHz.size() == 3);
    constexpr double pi = 3.141592653589793238462643383279502884;
    constexpr double firstRoot = 1.875104068711961;
    const double expected = firstRoot * firstRoot / (2.0 * pi)
        * std::sqrt(model.youngsModulusPa * model.secondMomentM4
                    / (model.densityKgPerM3 * model.crossSectionAreaM2))
        / (model.lengthM * model.lengthM);
    assert(std::abs(result.frequenciesHz.front() - expected) < expected * 1.0e-12);
    assert(std::abs(result.frequenciesHz[1] / result.frequenciesHz[0] - 6.2668930258)
           < 1.0e-9);
    assert(result.normalizedModeShapes.size() == 3);
    assert(result.samplePositionsNormalized.size() == 21);
    for (const auto& shape : result.normalizedModeShapes) {
        assert(shape.size() == 21);
        assert(std::abs(shape.front()) < 1.0e-12);
        assert(std::abs(shape.back() - 1.0) < 1.0e-12);
    }

    options.backend = dc::mechanical::SolverBackend::Cuda;
    const auto rejected = dc::mechanical::MechanicalEngine()
        .solveUniformCantileverModes(model, options);
    assert(!rejected.converged);
    assert(rejected.diagnostic.find("CPU only") != std::string::npos);

    model.densityKgPerM3 = 0.0;
    assert(!dc::mechanical::MechanicalEngine()
        .solveUniformCantileverModes(model).converged);
}

void testUniformCantileverModalCApi() {
    DcMechanicalUniformCantileverModalInput input{
        1,
        sizeof(DcMechanicalUniformCantileverModalInput),
        1.0,
        70.0e9,
        1.0e-8,
        1.0e-3,
        2700.0,
        3,
        21,
    };
    DcMechanicalModalResult result{};
    double frequencies[3]{};
    double shapes[63]{};
    assert(dc_mechanical_uniform_cantilever_modal(
        &input, &result, frequencies, 3, shapes, 63) == DC_OK);
    assert(result.converged == 1);
    assert(result.modeCount == 3);
    assert(result.shapeSampleCount == 21);
    assert(frequencies[0] > 0.0 && frequencies[1] > frequencies[0]);
    assert(std::abs(shapes[0]) < 1.0e-12);
    assert(std::abs(shapes[20] - 1.0) < 1.0e-12);
    assert(dc_mechanical_uniform_cantilever_modal(
        &input, &result, frequencies, 2, shapes, 63) == DC_ERR_SOLVER_INVALID);
}

} // namespace

int main() {
    testCppReference();
    testCApi();
    testRejectsNonSymmetricSystem();
    testAxialBarMatchesClosedForm();
    testCantileverMatchesClosedForm();
    testMechanicalFormulationsRejectInvalidProperties();
    testUniformCantileverModesMatchAnalyticalReference();
    testUniformCantileverModalCApi();
    std::cout << "MECHANICAL_ENGINE_OK\n";
    return 0;
}

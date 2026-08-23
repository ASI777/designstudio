#include "designcore/balancing_sphere.h"
#include "designcore/c_api.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <string>

using namespace dc::mechatronics;
namespace fs = std::filesystem;

namespace {
int failures = 0;
int checks = 0;

#define CHECK(condition, message)                                               \
    do {                                                                         \
        ++checks;                                                                \
        if (!(condition)) {                                                      \
            std::printf("FAIL %s:%d %s\n", __FILE__, __LINE__, message);        \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

bool near(double actual, double expected, double tolerance)
{
    return std::abs(actual - expected) <= tolerance;
}

std::size_t lineCount(const fs::path& path)
{
    std::ifstream input(path);
    return static_cast<std::size_t>(std::count(std::istreambuf_iterator<char>(input),
                                               std::istreambuf_iterator<char>(), '\n'));
}

void testController()
{
    BalancingSphereConfig config;
    std::string error;
    CHECK(validateBalancingSphereConfig(config, error), "default configuration validates");

    BalancingSphereConfig invalid = config;
    invalid.wallThicknessMm = invalid.outerRadiusMm;
    CHECK(!validateBalancingSphereConfig(invalid, error), "solid-filled sphere rejects missing interior");

    const BalanceCommand flat = computeBalanceCommand(config, 0, 0, 0, 0);
    CHECK(near(flat.targetXmm, 0, 1e-12) && near(flat.targetYmm, 0, 1e-12),
          "flat stationary surface centers ballast");

    const BalanceCommand pitched = computeBalanceCommand(config, 0, 3, 0, 0);
    CHECK(pitched.targetXmm > 10 && pitched.targetXmm < config.railTravelMm,
          "positive pitch moves ballast uphill on X");
    const BalanceCommand rateOnly = computeBalanceCommand(config, 0, 0, 0, 30);
    CHECK(rateOnly.targetXmm > 0, "angular velocity contributes damping displacement");

    const BalanceCommand steep = computeBalanceCommand(config, 20, -20, 0, 0);
    CHECK(steep.saturatedX && steep.saturatedY, "unachievable slope reports both saturations");
    CHECK(near(std::abs(steep.targetXmm), config.railTravelMm, 1e-9),
          "saturated command obeys rail travel");
    CHECK(pitched.maximumStaticAngleDeg > 5 && pitched.maximumStaticAngleDeg < 10,
          "maximum static angle reflects mass and travel limits");

    const auto response = simulateBallastResponse(config, 2.0, 0.01);
    CHECK(response.size() == 201, "controller simulation has deterministic sample count");
    CHECK(!response.empty()
              && near(response.back().state.xMm, response.back().targetXmm, 0.05)
              && near(response.back().state.yMm, response.back().targetYmm, 0.05),
          "actuator simulation reaches both commanded axes");
    CHECK(std::all_of(response.begin(), response.end(), [&config](const BalanceSample& sample) {
              return std::abs(sample.state.xMm) <= config.railTravelMm + 1e-9
                  && std::abs(sample.state.yMm) <= config.railTravelMm + 1e-9
                  && std::abs(sample.state.velocityXmmS) <= config.maxBallastSpeedMmS + 1e-9
                  && std::abs(sample.state.velocityYmmS) <= config.maxBallastSpeedMmS + 1e-9;
          }), "simulation respects travel and speed constraints");
}

void testGeneratedPackage(const fs::path& output)
{
    if (!balancingSphereCadAvailable()) {
        std::printf("SKIP CAD package: DesignCore built without Open CASCADE\n");
        return;
    }
    std::error_code ec;
    fs::remove_all(output, ec);
    BalancingSphereConfig config;
    BalancingSpherePackageResult result;
    std::string error;
    CHECK(generateBalancingSpherePackage(config, output.string(), result, error),
          error.empty() ? "package generation failed" : error.c_str());
    if (!error.empty()) std::printf("generation detail: %s\n", error.c_str());
    if (result.assemblyStepPath.empty()) return;

    CHECK(result.partCount >= 24 && result.validatedSolidCount == result.partCount,
          "mechanical, electronic and electrical parts are valid solids");
    CHECK(near(result.totalMassKg, config.fixedMassKg + config.ballastMassKg, 1e-9),
          "package preserves configured total mass");
    CHECK(fs::file_size(result.assemblyStepPath) > 10'000,
          "assembly STEP is non-trivial");
    CHECK(fs::file_size(result.cutawayStepPath) > 10'000,
          "cutaway STEP is non-trivial");
    CHECK(lineCount(result.controllerCsvPath) == 202,
          "controller response contains header plus 201 samples");
    CHECK(fs::exists(output / "parts" / "ballast.step")
              && fs::exists(output / "parts" / "imu.step")
              && fs::exists(output / "parts" / "battery.step"),
          "package contains mechanical, electronic and electrical STEP parts");

    DcShapeHandle shape = nullptr;
    CHECK(dc_brep_load_step(result.assemblyStepPath.c_str(), &shape) == DC_OK && shape,
          "generated STEP reloads through the public B-Rep API");
    if (shape) {
        DcMesh mesh{};
        CHECK(dc_brep_tessellate(shape, 0.8, &mesh) == DC_OK,
              "generated STEP tessellates through the public API");
        CHECK(mesh.nverts > 100 && mesh.ntris > 100 && mesh.xyz && mesh.idx,
              "tessellated sphere has non-empty indexed geometry");
        bool nonDegenerate = true;
        if (mesh.xyz && mesh.idx) {
            for (std::int64_t triangle = 0; triangle < mesh.ntris; ++triangle) {
                const std::int32_t a = mesh.idx[triangle * 3];
                const std::int32_t b = mesh.idx[triangle * 3 + 1];
                const std::int32_t c = mesh.idx[triangle * 3 + 2];
                const double abx = mesh.xyz[b * 3] - mesh.xyz[a * 3];
                const double aby = mesh.xyz[b * 3 + 1] - mesh.xyz[a * 3 + 1];
                const double abz = mesh.xyz[b * 3 + 2] - mesh.xyz[a * 3 + 2];
                const double acx = mesh.xyz[c * 3] - mesh.xyz[a * 3];
                const double acy = mesh.xyz[c * 3 + 1] - mesh.xyz[a * 3 + 1];
                const double acz = mesh.xyz[c * 3 + 2] - mesh.xyz[a * 3 + 2];
                const double cx = aby * acz - abz * acy;
                const double cy = abz * acx - abx * acz;
                const double cz = abx * acy - aby * acx;
                if (cx * cx + cy * cy + cz * cz <= 1.0e-12) {
                    nonDegenerate = false;
                    break;
                }
            }
        }
        CHECK(nonDegenerate, "public B-Rep tessellation filters degenerate triangles");
        if (mesh.xyz && mesh.nverts > 0) {
            double minX = mesh.xyz[0], maxX = mesh.xyz[0];
            for (std::int64_t i = 1; i < mesh.nverts; ++i) {
                minX = std::min(minX, double(mesh.xyz[i * 3]));
                maxX = std::max(maxX, double(mesh.xyz[i * 3]));
            }
            CHECK(near(minX, -config.outerRadiusMm, 0.1)
                      && near(maxX, config.outerRadiusMm, 0.1),
                  "tessellated assembly preserves sphere diameter");
        }
        dc_shape_destroy(shape);
    }

    DcDocHandle document = nullptr;
    CHECK(dc_doc_open(result.projectPath.c_str(), &document) == DC_OK && document,
          "mechatronic project reloads through document boundary");
    if (document) {
        CHECK(dc_doc_version(document) == 3, "generated mechatronic project is schema v3");
        dc_doc_destroy(document);
    }
}

} // namespace

int main(int argc, char** argv)
{
    if (argc != 2) return 2;
    testController();
    testGeneratedPackage(fs::path(argv[1]));
    std::printf("balancing sphere: %d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}

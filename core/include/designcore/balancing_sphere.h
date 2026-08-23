#pragma once

#include <string>
#include <vector>

namespace dc::mechatronics {

// Parametric reference design used to prove the mechanical + electronics +
// control path. Linear dimensions are millimetres, masses kilograms, angles
// degrees and angular rates degrees/second.
struct BalancingSphereConfig {
    double outerRadiusMm = 80.0;
    double wallThicknessMm = 3.0;
    double assemblyClearanceMm = 1.5;
    double railTravelMm = 35.0;
    double ballastMassKg = 0.35;
    double fixedMassKg = 0.89;
    double maxBallastSpeedMmS = 80.0;
    double maxBallastAccelerationMmS2 = 300.0;
    double rateDampingMmPerRadS = 18.0;
    double targetSafetyFactor = 2.0;

    double surfaceRollDeg = -2.0;
    double surfacePitchDeg = 3.0;
    double rollRateDegS = -6.0;
    double pitchRateDegS = 8.0;
};

struct BalanceCommand {
    double targetXmm = 0.0; // pitch compensation, +X is uphill
    double targetYmm = 0.0; // roll compensation, +Y is uphill
    double maximumStaticAngleDeg = 0.0;
    bool saturatedX = false;
    bool saturatedY = false;
};

struct BallastState {
    double xMm = 0.0;
    double yMm = 0.0;
    double velocityXmmS = 0.0;
    double velocityYmmS = 0.0;
};

struct BalanceSample {
    double timeS = 0.0;
    double targetXmm = 0.0;
    double targetYmm = 0.0;
    BallastState state;
};

struct BalancingSpherePackageResult {
    std::string assemblyStepPath;
    std::string cutawayStepPath;
    std::string projectPath;
    std::string controllerCsvPath;
    int partCount = 0;
    int validatedSolidCount = 0;
    double totalMassKg = 0.0;
    double maximumStaticAngleDeg = 0.0;
    BalanceCommand initialCommand;
};

bool validateBalancingSphereConfig(const BalancingSphereConfig& config,
                                   std::string& error);
BalanceCommand computeBalanceCommand(const BalancingSphereConfig& config,
                                     double rollDeg,
                                     double pitchDeg,
                                     double rollRateDegS,
                                     double pitchRateDegS);
void stepBallast(const BalancingSphereConfig& config,
                 const BalanceCommand& command,
                 double dtSeconds,
                 BallastState& state);
std::vector<BalanceSample> simulateBallastResponse(const BalancingSphereConfig& config,
                                                    double durationSeconds = 2.0,
                                                    double dtSeconds = 0.01);

bool balancingSphereCadAvailable() noexcept;
bool generateBalancingSpherePackage(const BalancingSphereConfig& config,
                                    const std::string& outputDirectory,
                                    BalancingSpherePackageResult& result,
                                    std::string& error);

} // namespace dc::mechatronics

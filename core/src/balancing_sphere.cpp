#include "designcore/balancing_sphere.h"

#include <algorithm>
#include <cmath>
#include <limits>

namespace dc::mechatronics {
namespace {

constexpr double Pi = 3.14159265358979323846;

double radians(double degrees) { return degrees * Pi / 180.0; }

double clampAxis(double value, double travel, bool& saturated)
{
    saturated = std::abs(value) > travel;
    return std::clamp(value, -travel, travel);
}

void stepAxis(double target, double dt, double maxSpeed, double maxAcceleration,
              double travel, double& position, double& velocity)
{
    const double error = target - position;
    const double stoppingSpeed = std::sqrt(std::max(0.0, 2.0 * maxAcceleration * std::abs(error)));
    const double requestedVelocity = std::copysign(std::min(maxSpeed, stoppingSpeed), error);
    const double velocityDelta = std::clamp(requestedVelocity - velocity,
                                             -maxAcceleration * dt,
                                             maxAcceleration * dt);
    velocity += velocityDelta;
    const double previousError = error;
    position = std::clamp(position + velocity * dt, -travel, travel);
    if ((target - position) * previousError <= 0.0) {
        position = target;
        velocity = 0.0;
    }
    if ((position <= -travel && velocity < 0.0)
        || (position >= travel && velocity > 0.0)) velocity = 0.0;
}

bool finite(double value) { return std::isfinite(value); }

} // namespace

bool validateBalancingSphereConfig(const BalancingSphereConfig& c, std::string& error)
{
    const double values[] = {
        c.outerRadiusMm, c.wallThicknessMm, c.assemblyClearanceMm, c.railTravelMm,
        c.ballastMassKg, c.fixedMassKg, c.maxBallastSpeedMmS,
        c.maxBallastAccelerationMmS2, c.rateDampingMmPerRadS, c.targetSafetyFactor,
        c.surfaceRollDeg, c.surfacePitchDeg, c.rollRateDegS, c.pitchRateDegS,
    };
    if (!std::all_of(std::begin(values), std::end(values), finite)) {
        error = "all balancing-sphere parameters must be finite";
        return false;
    }
    if (c.outerRadiusMm < 75.0 || c.outerRadiusMm > 500.0) {
        error = "outer radius must be between 75 and 500 mm for this internal architecture";
        return false;
    }
    if (c.wallThicknessMm < 1.0 || c.wallThicknessMm > c.outerRadiusMm * 0.15) {
        error = "wall thickness must be at least 1 mm and below 15% of radius";
        return false;
    }
    if (c.assemblyClearanceMm < 0.5 || c.assemblyClearanceMm > 10.0) {
        error = "assembly clearance must be between 0.5 and 10 mm";
        return false;
    }
    const double usableRadius = c.outerRadiusMm - c.wallThicknessMm - c.assemblyClearanceMm;
    if (usableRadius < 70.0 || c.railTravelMm <= 0.0
        || c.railTravelMm + 35.0 >= usableRadius) {
        error = "frame, actuator and ballast travel envelopes do not fit inside the shell";
        return false;
    }
    if (c.ballastMassKg <= 0.0 || c.fixedMassKg <= 0.0) {
        error = "ballast and fixed masses must be positive";
        return false;
    }
    if (c.maxBallastSpeedMmS <= 0.0 || c.maxBallastAccelerationMmS2 <= 0.0) {
        error = "actuator speed and acceleration limits must be positive";
        return false;
    }
    if (c.targetSafetyFactor < 1.0 || c.targetSafetyFactor > 10.0) {
        error = "target safety factor must be between 1 and 10";
        return false;
    }
    error.clear();
    return true;
}

BalanceCommand computeBalanceCommand(const BalancingSphereConfig& c,
                                     double rollDeg, double pitchDeg,
                                     double rollRateDegS, double pitchRateDegS)
{
    BalanceCommand command;
    const double totalMass = c.fixedMassKg + c.ballastMassKg;
    const double massRatio = totalMass / c.ballastMassKg;
    const double staticX = massRatio * c.outerRadiusMm * std::tan(radians(pitchDeg));
    const double staticY = massRatio * c.outerRadiusMm * std::tan(radians(rollDeg));
    const double dampingX = c.rateDampingMmPerRadS * radians(pitchRateDegS);
    const double dampingY = c.rateDampingMmPerRadS * radians(rollRateDegS);
    command.targetXmm = clampAxis(staticX + dampingX, c.railTravelMm, command.saturatedX);
    command.targetYmm = clampAxis(staticY + dampingY, c.railTravelMm, command.saturatedY);
    command.maximumStaticAngleDeg = std::atan(
        c.ballastMassKg * c.railTravelMm / (totalMass * c.outerRadiusMm)) * 180.0 / Pi;
    return command;
}

void stepBallast(const BalancingSphereConfig& c, const BalanceCommand& command,
                 double dtSeconds, BallastState& state)
{
    if (!finite(dtSeconds) || dtSeconds <= 0.0 || dtSeconds > 0.1) return;
    stepAxis(command.targetXmm, dtSeconds, c.maxBallastSpeedMmS,
             c.maxBallastAccelerationMmS2, c.railTravelMm,
             state.xMm, state.velocityXmmS);
    stepAxis(command.targetYmm, dtSeconds, c.maxBallastSpeedMmS,
             c.maxBallastAccelerationMmS2, c.railTravelMm,
             state.yMm, state.velocityYmmS);
}

std::vector<BalanceSample> simulateBallastResponse(const BalancingSphereConfig& c,
                                                    double durationSeconds,
                                                    double dtSeconds)
{
    std::vector<BalanceSample> samples;
    std::string error;
    if (!validateBalancingSphereConfig(c, error) || !finite(durationSeconds)
        || !finite(dtSeconds) || durationSeconds <= 0.0 || dtSeconds <= 0.0
        || dtSeconds > 0.1 || durationSeconds > 60.0) return samples;
    const BalanceCommand command = computeBalanceCommand(
        c, c.surfaceRollDeg, c.surfacePitchDeg, c.rollRateDegS, c.pitchRateDegS);
    BallastState state;
    const int steps = static_cast<int>(std::ceil(durationSeconds / dtSeconds));
    samples.reserve(steps + 1);
    samples.push_back({0.0, command.targetXmm, command.targetYmm, state});
    for (int step = 1; step <= steps; ++step) {
        stepBallast(c, command, dtSeconds, state);
        samples.push_back({std::min(durationSeconds, step * dtSeconds),
                           command.targetXmm, command.targetYmm, state});
    }
    return samples;
}

#ifndef HAS_OCCT
bool balancingSphereCadAvailable() noexcept { return false; }

bool generateBalancingSpherePackage(const BalancingSphereConfig&,
                                    const std::string&,
                                    BalancingSpherePackageResult& result,
                                    std::string& error)
{
    result = {};
    error = "DesignCore was built without the Open CASCADE B-Rep kernel";
    return false;
}
#endif

} // namespace dc::mechatronics

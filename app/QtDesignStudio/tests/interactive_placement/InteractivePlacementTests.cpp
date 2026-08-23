#include "InteractivePlacementModel.h"

#include <QCoreApplication>
#include <QJsonArray>
#include <QJsonObject>

#include <cmath>
#include <iostream>

namespace {

bool close(double left, double right, double tolerance = 1.0e-4)
{
    return std::abs(left - right) <= tolerance;
}

bool check(bool condition, const char* message)
{
    if (condition) return true;
    std::cerr << "FAIL: " << message << '\n';
    return false;
}

} // namespace

int main(int argc, char** argv)
{
    QCoreApplication application(argc, argv);
    using designstudio::InteractivePlacementModel;

    InteractivePlacementModel model = InteractivePlacementModel::create(
        QStringLiteral("component.encoder"),
        QStringLiteral("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"),
        QStringLiteral("keyboard-v1"));
    model.documentRevision = 7;
    model.documentSha256 =
        QStringLiteral("abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789");
    model.coordinateSystem = InteractivePlacementModel::CoordinateSystem::EnclosureLocal;
    model.snap.symmetry = true;
    model.locks.surfaceAnchor = true;
    if (!check(model.setTranslationMm(QVector3D(12.5f, -4.0f, 31.25f)),
               "valid translation rejected")
        || !check(model.setEulerDegrees(QVector3D(15.0f, 30.0f, 45.0f)),
                  "valid Euler rotation rejected")
        || !check(model.setClearanceMm(1.25), "valid clearance rejected")) return 1;

    QString error;
    if (!check(model.validate(&error), qPrintable(error))) return 1;
    const QJsonObject encoded = model.toJson();
    InteractivePlacementModel decoded;
    if (!check(InteractivePlacementModel::fromJson(encoded, &decoded, &error),
               qPrintable(error))
        || !check(decoded.placementId == model.placementId, "placement UUID changed")
        || !check(decoded.componentId == model.componentId, "component ID changed")
        || !check(decoded.coordinateSystem
                      == InteractivePlacementModel::CoordinateSystem::EnclosureLocal,
                  "coordinate system changed")
        || !check(decoded.snap.symmetry, "snap state changed")
        || !check(decoded.locks.surfaceAnchor, "lock state changed")
        || !check(close(decoded.translationMm.x(), 12.5), "translation changed")
        || !check(close(decoded.clearanceMm, 1.25), "clearance changed")) return 1;

    QJsonObject unknown = encoded;
    unknown.insert(QStringLiteral("untrusted_host_override"), true);
    if (!check(!InteractivePlacementModel::fromJson(unknown, &decoded, &error),
               "unknown mutation field was accepted")) return 1;

    QJsonObject invalidRotation = encoded;
    QJsonObject transform = invalidRotation.value(QStringLiteral("transform")).toObject();
    transform.insert(QStringLiteral("rotation_xyzw"), QJsonArray{0.0, 0.0, 0.0, 0.0});
    invalidRotation.insert(QStringLiteral("transform"), transform);
    if (!check(!InteractivePlacementModel::fromJson(invalidRotation, &decoded, &error),
               "zero quaternion was accepted")) return 1;

    std::cout << "INTERACTIVE_PLACEMENT_OK\n";
    return 0;
}

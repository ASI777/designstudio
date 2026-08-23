#pragma once

#include <QJsonObject>
#include <QMatrix4x4>
#include <QQuaternion>
#include <QString>
#include <QVector3D>

namespace designstudio {

class InteractivePlacementModel final {
public:
    enum class CoordinateSystem { World, EnclosureLocal, PcbLocal, SurfaceLocal };

    struct Snap {
        bool grid = true;
        bool surface = false;
        bool axis = true;
        bool symmetry = false;
        bool clearance = true;
    };

    struct Locks {
        bool position = false;
        bool orientation = false;
        bool surfaceAnchor = false;
    };

    static InteractivePlacementModel create(const QString& componentId,
                                            const QString& componentSha256,
                                            const QString& documentId = QStringLiteral("unbound"));
    static bool fromJson(const QJsonObject& object, InteractivePlacementModel* result,
                         QString* errorMessage = nullptr);

    QJsonObject toJson() const;
    QMatrix4x4 transform() const;
    QVector3D eulerDegrees() const;
    bool setEulerDegrees(const QVector3D& degrees, QString* errorMessage = nullptr);
    bool setTranslationMm(const QVector3D& translation, QString* errorMessage = nullptr);
    bool setRotation(const QQuaternion& rotation, QString* errorMessage = nullptr);
    bool setClearanceMm(double clearance, QString* errorMessage = nullptr);
    bool validate(QString* errorMessage = nullptr) const;

    QString placementId;
    QString componentId;
    QString componentSha256;
    QString sourcePath;
    QString documentId = QStringLiteral("unbound");
    qint64 documentRevision = 0;
    QString documentSha256 = QString(64, QLatin1Char('0'));
    QVector3D translationMm;
    QQuaternion rotation;
    CoordinateSystem coordinateSystem = CoordinateSystem::World;
    Snap snap;
    Locks locks;
    double clearanceMm = 1.0;

    static QString coordinateSystemName(CoordinateSystem value);
    static bool coordinateSystemFromName(const QString& value, CoordinateSystem* result);
};

} // namespace designstudio

#include "InteractivePlacementModel.h"

#include <QJsonArray>
#include <QRegularExpression>
#include <QSet>
#include <QStringList>
#include <QUuid>

#include <algorithm>
#include <cmath>

namespace designstudio {
namespace {

constexpr double MaxCoordinateMm = 1.0e6;

bool finiteVector(const QVector3D& value)
{
    return std::isfinite(value.x()) && std::isfinite(value.y()) && std::isfinite(value.z());
}

QJsonArray pointJson(const QVector3D& value)
{
    return {double(value.x()), double(value.y()), double(value.z())};
}

bool readPoint(const QJsonValue& value, QVector3D* result, const QString& path,
               QString* errorMessage)
{
    const QJsonArray array = value.toArray();
    if (!value.isArray() || array.size() != 3) {
        if (errorMessage) *errorMessage = path + QStringLiteral(" must contain three numbers");
        return false;
    }
    for (const QJsonValue& coordinate : array) {
        if (!coordinate.isDouble() || !std::isfinite(coordinate.toDouble())) {
            if (errorMessage) *errorMessage = path + QStringLiteral(" must contain finite numbers");
            return false;
        }
    }
    *result = QVector3D(float(array[0].toDouble()), float(array[1].toDouble()),
                        float(array[2].toDouble()));
    return true;
}

bool readBoolean(const QJsonObject& object, const QString& key, bool* result,
                 const QString& path, QString* errorMessage)
{
    const QJsonValue value = object.value(key);
    if (!value.isBool()) {
        if (errorMessage) *errorMessage = path + QLatin1Char('.') + key
            + QStringLiteral(" must be boolean");
        return false;
    }
    *result = value.toBool();
    return true;
}

bool exactKeys(const QJsonObject& object, const QSet<QString>& expected,
               const QString& path, QString* errorMessage)
{
    QSet<QString> actual;
    for (auto it = object.constBegin(); it != object.constEnd(); ++it) actual.insert(it.key());
    if (actual == expected) return true;
    if (errorMessage) {
        QStringList missing;
        QStringList unknown;
        for (const QString& key : expected - actual) missing.push_back(key);
        for (const QString& key : actual - expected) unknown.push_back(key);
        *errorMessage = QStringLiteral("%1 has invalid fields; missing=[%2], unknown=[%3]")
            .arg(path, missing.join(QStringLiteral(", ")), unknown.join(QStringLiteral(", ")));
    }
    return false;
}

} // namespace

InteractivePlacementModel InteractivePlacementModel::create(
    const QString& requestedComponentId, const QString& requestedComponentSha256,
    const QString& requestedDocumentId)
{
    InteractivePlacementModel result;
    result.placementId = QUuid::createUuid().toString(QUuid::WithoutBraces);
    result.componentId = requestedComponentId;
    result.componentSha256 = requestedComponentSha256;
    result.documentId = requestedDocumentId;
    result.rotation = QQuaternion();
    return result;
}

QString InteractivePlacementModel::coordinateSystemName(CoordinateSystem value)
{
    switch (value) {
    case CoordinateSystem::World: return QStringLiteral("world");
    case CoordinateSystem::EnclosureLocal: return QStringLiteral("enclosure_local");
    case CoordinateSystem::PcbLocal: return QStringLiteral("pcb_local");
    case CoordinateSystem::SurfaceLocal: return QStringLiteral("surface_local");
    }
    return QStringLiteral("world");
}

bool InteractivePlacementModel::coordinateSystemFromName(
    const QString& value, CoordinateSystem* result)
{
    if (value == QStringLiteral("world")) *result = CoordinateSystem::World;
    else if (value == QStringLiteral("enclosure_local"))
        *result = CoordinateSystem::EnclosureLocal;
    else if (value == QStringLiteral("pcb_local")) *result = CoordinateSystem::PcbLocal;
    else if (value == QStringLiteral("surface_local"))
        *result = CoordinateSystem::SurfaceLocal;
    else return false;
    return true;
}

QVector3D InteractivePlacementModel::eulerDegrees() const
{
    return rotation.toEulerAngles();
}

bool InteractivePlacementModel::setTranslationMm(const QVector3D& translation,
                                                  QString* errorMessage)
{
    if (!finiteVector(translation)
        || std::max({std::abs(double(translation.x())), std::abs(double(translation.y())),
                     std::abs(double(translation.z()))}) > MaxCoordinateMm) {
        if (errorMessage) *errorMessage = QStringLiteral(
            "translation must contain finite coordinates within ±1,000,000 mm");
        return false;
    }
    translationMm = translation;
    if (errorMessage) errorMessage->clear();
    return true;
}

bool InteractivePlacementModel::setRotation(const QQuaternion& requestedRotation,
                                             QString* errorMessage)
{
    const double normSquared = requestedRotation.lengthSquared();
    if (!std::isfinite(normSquared) || normSquared < 1.0e-12) {
        if (errorMessage) *errorMessage = QStringLiteral("rotation quaternion is invalid");
        return false;
    }
    rotation = requestedRotation.normalized();
    if (errorMessage) errorMessage->clear();
    return true;
}

bool InteractivePlacementModel::setEulerDegrees(const QVector3D& degrees,
                                                 QString* errorMessage)
{
    if (!finiteVector(degrees)) {
        if (errorMessage) *errorMessage = QStringLiteral("Euler angles must be finite");
        return false;
    }
    return setRotation(QQuaternion::fromEulerAngles(degrees), errorMessage);
}

bool InteractivePlacementModel::setClearanceMm(double clearance, QString* errorMessage)
{
    if (!std::isfinite(clearance) || clearance < 0.0 || clearance > 1000.0) {
        if (errorMessage) *errorMessage = QStringLiteral(
            "clearance must be within 0–1000 mm");
        return false;
    }
    clearanceMm = clearance;
    if (errorMessage) errorMessage->clear();
    return true;
}

QMatrix4x4 InteractivePlacementModel::transform() const
{
    QMatrix4x4 matrix;
    matrix.translate(translationMm);
    matrix.rotate(rotation);
    return matrix;
}

bool InteractivePlacementModel::validate(QString* errorMessage) const
{
    static const QRegularExpression stableId(
        QStringLiteral("^[A-Za-z][A-Za-z0-9._:-]{0,127}$"));
    static const QRegularExpression digest(QStringLiteral("^[0-9a-f]{64}$"));
    const QUuid uuid(placementId);
    if (uuid.isNull() || uuid.toString(QUuid::WithoutBraces) != placementId) {
        if (errorMessage) *errorMessage = QStringLiteral("placement_id must be a canonical UUID");
        return false;
    }
    if (!stableId.match(componentId).hasMatch()) {
        if (errorMessage) *errorMessage = QStringLiteral("component_id is not stable");
        return false;
    }
    if (!digest.match(componentSha256).hasMatch()
        || !digest.match(documentSha256).hasMatch()) {
        if (errorMessage) *errorMessage = QStringLiteral("component and document digests must be SHA-256");
        return false;
    }
    if (documentId.isEmpty() || documentId.size() > 128 || documentRevision < 0) {
        if (errorMessage) *errorMessage = QStringLiteral("document binding is invalid");
        return false;
    }
    InteractivePlacementModel copy = *this;
    if (!copy.setTranslationMm(translationMm, errorMessage)
        || !copy.setRotation(rotation, errorMessage)
        || !copy.setClearanceMm(clearanceMm, errorMessage)) return false;
    if (errorMessage) errorMessage->clear();
    return true;
}

QJsonObject InteractivePlacementModel::toJson() const
{
    const QQuaternion normalized = rotation.normalized();
    return {
        {QStringLiteral("schema"), QStringLiteral("design-studio.component-placement/1")},
        {QStringLiteral("placement_id"), placementId},
        {QStringLiteral("component_id"), componentId},
        {QStringLiteral("component_sha256"), componentSha256},
        {QStringLiteral("document"), QJsonObject{
            {QStringLiteral("document_id"), documentId},
            {QStringLiteral("revision"), double(documentRevision)},
            {QStringLiteral("sha256"), documentSha256},
        }},
        {QStringLiteral("transform"), QJsonObject{
            {QStringLiteral("translation_mm"), pointJson(translationMm)},
            {QStringLiteral("rotation_xyzw"), QJsonArray{
                double(normalized.x()), double(normalized.y()), double(normalized.z()),
                double(normalized.scalar())}},
        }},
        {QStringLiteral("coordinate_system"), coordinateSystemName(coordinateSystem)},
        {QStringLiteral("snap"), QJsonObject{
            {QStringLiteral("grid"), snap.grid},
            {QStringLiteral("surface"), snap.surface},
            {QStringLiteral("axis"), snap.axis},
            {QStringLiteral("symmetry"), snap.symmetry},
            {QStringLiteral("clearance"), snap.clearance},
        }},
        {QStringLiteral("locks"), QJsonObject{
            {QStringLiteral("position"), locks.position},
            {QStringLiteral("orientation"), locks.orientation},
            {QStringLiteral("surface_anchor"), locks.surfaceAnchor},
        }},
        {QStringLiteral("clearance_mm"), clearanceMm},
    };
}

bool InteractivePlacementModel::fromJson(const QJsonObject& object,
                                         InteractivePlacementModel* result,
                                         QString* errorMessage)
{
    if (!result) {
        if (errorMessage) *errorMessage = QStringLiteral("result pointer is required");
        return false;
    }
    const QSet<QString> keys = {
        QStringLiteral("schema"), QStringLiteral("placement_id"),
        QStringLiteral("component_id"), QStringLiteral("component_sha256"),
        QStringLiteral("document"), QStringLiteral("transform"),
        QStringLiteral("coordinate_system"), QStringLiteral("snap"),
        QStringLiteral("locks"), QStringLiteral("clearance_mm"),
    };
    if (!exactKeys(object, keys, QStringLiteral("placement"), errorMessage)) return false;
    if (object.value(QStringLiteral("schema")).toString()
        != QStringLiteral("design-studio.component-placement/1")) {
        if (errorMessage) *errorMessage = QStringLiteral("unsupported placement schema");
        return false;
    }

    InteractivePlacementModel parsed;
    parsed.placementId = object.value(QStringLiteral("placement_id")).toString();
    parsed.componentId = object.value(QStringLiteral("component_id")).toString();
    parsed.componentSha256 = object.value(QStringLiteral("component_sha256")).toString();

    const QJsonObject document = object.value(QStringLiteral("document")).toObject();
    if (!object.value(QStringLiteral("document")).isObject()
        || !exactKeys(document, {QStringLiteral("document_id"), QStringLiteral("revision"),
                                 QStringLiteral("sha256")},
                      QStringLiteral("placement.document"), errorMessage)) return false;
    parsed.documentId = document.value(QStringLiteral("document_id")).toString();
    const QJsonValue revision = document.value(QStringLiteral("revision"));
    if (!revision.isDouble() || revision.toDouble() < 0.0
        || std::floor(revision.toDouble()) != revision.toDouble()) {
        if (errorMessage) *errorMessage = QStringLiteral("document.revision must be a non-negative integer");
        return false;
    }
    parsed.documentRevision = qint64(revision.toDouble());
    parsed.documentSha256 = document.value(QStringLiteral("sha256")).toString();

    const QJsonObject transformObject = object.value(QStringLiteral("transform")).toObject();
    if (!object.value(QStringLiteral("transform")).isObject()
        || !exactKeys(transformObject,
                      {QStringLiteral("translation_mm"), QStringLiteral("rotation_xyzw")},
                      QStringLiteral("placement.transform"), errorMessage)) return false;
    if (!readPoint(transformObject.value(QStringLiteral("translation_mm")),
                   &parsed.translationMm, QStringLiteral("transform.translation_mm"),
                   errorMessage)) return false;
    const QJsonArray quaternion = transformObject.value(QStringLiteral("rotation_xyzw")).toArray();
    if (!transformObject.value(QStringLiteral("rotation_xyzw")).isArray()
        || quaternion.size() != 4) {
        if (errorMessage) *errorMessage = QStringLiteral("rotation_xyzw must contain four numbers");
        return false;
    }
    for (const QJsonValue& coordinate : quaternion) {
        if (!coordinate.isDouble() || !std::isfinite(coordinate.toDouble())) {
            if (errorMessage) *errorMessage = QStringLiteral("rotation_xyzw must be finite");
            return false;
        }
    }
    if (!parsed.setRotation(QQuaternion(
            float(quaternion[3].toDouble()), float(quaternion[0].toDouble()),
            float(quaternion[1].toDouble()), float(quaternion[2].toDouble())),
            errorMessage)) return false;

    if (!coordinateSystemFromName(
            object.value(QStringLiteral("coordinate_system")).toString(),
            &parsed.coordinateSystem)) {
        if (errorMessage) *errorMessage = QStringLiteral("coordinate_system is invalid");
        return false;
    }

    const QJsonObject snap = object.value(QStringLiteral("snap")).toObject();
    if (!object.value(QStringLiteral("snap")).isObject()
        || !exactKeys(snap, {QStringLiteral("grid"), QStringLiteral("surface"),
                             QStringLiteral("axis"), QStringLiteral("symmetry"),
                             QStringLiteral("clearance")},
                      QStringLiteral("placement.snap"), errorMessage)
        || !readBoolean(snap, QStringLiteral("grid"), &parsed.snap.grid,
                        QStringLiteral("placement.snap"), errorMessage)
        || !readBoolean(snap, QStringLiteral("surface"), &parsed.snap.surface,
                        QStringLiteral("placement.snap"), errorMessage)
        || !readBoolean(snap, QStringLiteral("axis"), &parsed.snap.axis,
                        QStringLiteral("placement.snap"), errorMessage)
        || !readBoolean(snap, QStringLiteral("symmetry"), &parsed.snap.symmetry,
                        QStringLiteral("placement.snap"), errorMessage)
        || !readBoolean(snap, QStringLiteral("clearance"), &parsed.snap.clearance,
                        QStringLiteral("placement.snap"), errorMessage)) return false;

    const QJsonObject locks = object.value(QStringLiteral("locks")).toObject();
    if (!object.value(QStringLiteral("locks")).isObject()
        || !exactKeys(locks, {QStringLiteral("position"), QStringLiteral("orientation"),
                              QStringLiteral("surface_anchor")},
                      QStringLiteral("placement.locks"), errorMessage)
        || !readBoolean(locks, QStringLiteral("position"), &parsed.locks.position,
                        QStringLiteral("placement.locks"), errorMessage)
        || !readBoolean(locks, QStringLiteral("orientation"), &parsed.locks.orientation,
                        QStringLiteral("placement.locks"), errorMessage)
        || !readBoolean(locks, QStringLiteral("surface_anchor"), &parsed.locks.surfaceAnchor,
                        QStringLiteral("placement.locks"), errorMessage)) return false;

    if (!object.value(QStringLiteral("clearance_mm")).isDouble()
        || !parsed.setClearanceMm(object.value(QStringLiteral("clearance_mm")).toDouble(),
                                  errorMessage)
        || !parsed.validate(errorMessage)) return false;
    *result = parsed;
    if (errorMessage) errorMessage->clear();
    return true;
}

} // namespace designstudio

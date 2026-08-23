#include "SemanticAssembly.h"

#include <QFile>
#include <QFileInfo>
#include <QDataStream>
#include <QCryptographicHash>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <QSet>

#include <algorithm>
#include <cmath>

namespace designstudio {
namespace {

bool finite(double value) { return std::isfinite(value); }

bool digest(const QString& value)
{
    if (value.size() != 64) return false;
    for (const QChar character : value)
        if (!character.isDigit() && (character < QLatin1Char('a')
                                     || character > QLatin1Char('f')))
            return false;
    return true;
}

bool id(const QString& value)
{
    if (value.isEmpty() || value.size() > 128
        || !(value.at(0).isLetter() || value.at(0) == QLatin1Char('_')))
        return false;
    for (const QChar character : value)
        if (!(character.isLetterOrNumber() || character == QLatin1Char('_')
              || character == QLatin1Char('.') || character == QLatin1Char('-')))
            return false;
    return true;
}

bool finiteArray(const QJsonValue& value, int count)
{
    if (!value.isArray() || value.toArray().size() != count) return false;
    for (const QJsonValue& item : value.toArray())
        if (!item.isDouble() || !finite(item.toDouble())) return false;
    return true;
}

QColor color(const QJsonValue& value, bool* valid)
{
    *valid = finiteArray(value, 4);
    if (!*valid) return {};
    const QJsonArray values = value.toArray();
    const auto channel = [](double value) {
        return int(std::clamp(value, 0.0, 1.0) * 255.0 + 0.5);
    };
    return QColor(channel(values.at(0).toDouble()), channel(values.at(1).toDouble()),
                  channel(values.at(2).toDouble()), channel(values.at(3).toDouble()));
}

QMatrix4x4 placement(const QJsonValue& value, bool* valid)
{
    *valid = finiteArray(value, 16);
    QMatrix4x4 matrix;
    matrix.setToIdentity();
    if (!*valid) return matrix;
    const QJsonArray values = value.toArray();
    for (int row = 0; row < 4; ++row)
        for (int column = 0; column < 4; ++column)
            matrix(row, column) = float(values.at(row * 4 + column).toDouble());
    return matrix;
}

std::shared_ptr<const SemanticAssembly> legacy(qsizetype triangleCount,
                                               const QString& sourceSha256)
{
    auto result = std::make_shared<SemanticAssembly>();
    result->schema = QStringLiteral("design-studio.semantic-assembly/2");
    result->sourceFormat = QStringLiteral("STEP");
    result->sourceStepSha256 = sourceSha256;
    result->identityAvailable = false;
    result->legacyFlattened = true;
    SemanticAssemblyComponent component;
    component.semanticId = QStringLiteral("legacy-flat-step");
    component.name = QStringLiteral("Flattened STEP (identity unavailable)");
    component.material = QStringLiteral("unknown");
    component.triangleCount = triangleCount;
    component.color = QColor(QStringLiteral("#7f8c99"));
    component.placement.setToIdentity();
    result->components.append(component);
    return result;
}

} // namespace

QString SemanticAssembly::componentForTriangle(qsizetype triangleIndex) const
{
    for (const SemanticAssemblyComponent& component : components)
        if (triangleIndex >= component.triangleStart
            && triangleIndex < component.triangleStart + component.triangleCount)
            return component.semanticId;
    return {};
}

QString SemanticAssembly::componentForVertex(quint32 vertexIndex,
                                             const QVector<quint32>& triangleIndices) const
{
    for (qsizetype triangle = 0; triangle * 3 + 2 < triangleIndices.size(); ++triangle) {
        if (triangleIndices.at(triangle * 3) == vertexIndex
            || triangleIndices.at(triangle * 3 + 1) == vertexIndex
            || triangleIndices.at(triangle * 3 + 2) == vertexIndex)
            return componentForTriangle(triangle);
    }
    return {};
}

int SemanticAssembly::componentIndex(const QString& semanticId) const
{
    for (int index = 0; index < components.size(); ++index)
        if (components.at(index).semanticId == semanticId) return index;
    return -1;
}

QColor SemanticAssembly::colorForComponent(const QString& semanticId) const
{
    const int index = componentIndex(semanticId);
    return index < 0 ? QColor(Qt::gray) : components.at(index).color;
}

QString SemanticAssemblyReader::sidecarPath(const QString& stepPath)
{
    const QFileInfo source(stepPath);
    const QString adjacent = source.absolutePath() + QLatin1Char('/')
        + source.fileName() + QStringLiteral(".assembly.json");
    if (QFileInfo::exists(adjacent)) return adjacent;
    return source.absolutePath() + QLatin1Char('/')
        + source.completeBaseName() + QStringLiteral(".assembly.json");
}

SemanticAssemblyLoadResult SemanticAssemblyReader::loadForStep(
    const QString& stepPath, const QString& sourceSha256, qsizetype triangleCount,
    const QString& expectedTessellationSha256)
{
    const QString path = sidecarPath(stepPath);
    QFile file(path);
    if (!file.exists()) return {legacy(triangleCount, sourceSha256), {}};
    if (!file.open(QIODevice::ReadOnly))
        return {{}, QStringLiteral("Could not read semantic assembly sidecar: %1")
                            .arg(file.errorString())};
    return fromJson(file.readAll(), sourceSha256, triangleCount, expectedTessellationSha256);
}

SemanticAssemblyLoadResult SemanticAssemblyReader::fromJson(
    const QByteArray& bytes, const QString& sourceSha256, qsizetype triangleCount,
    const QString& expectedTessellationSha256)
{
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(bytes, &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject())
        return {{}, QStringLiteral("semantic assembly is not valid JSON: %1")
                            .arg(parseError.errorString())};
    const QJsonObject root = document.object();
    if (root.value(QStringLiteral("schema")).toString()
            != QStringLiteral("design-studio.semantic-assembly/2"))
        return {{}, QStringLiteral("semantic assembly schema must be design-studio.semantic-assembly/2")};
    if (root.value(QStringLiteral("source_step_sha256")).toString() != sourceSha256)
        return {{}, QStringLiteral("semantic assembly source STEP digest does not match")};
    if (!digest(sourceSha256))
        return {{}, QStringLiteral("source STEP digest is not a SHA-256 value")};
    if (!root.value(QStringLiteral("identity_available")).toBool(false))
        return {{}, QStringLiteral("identity-bearing sidecar must set identity_available=true")};
    if (root.value(QStringLiteral("legacy_flattened")).toBool(true))
        return {{}, QStringLiteral("identity-bearing sidecar must set legacy_flattened=false")};
    if (root.value(QStringLiteral("source_format")).toString() != QStringLiteral("AP242"))
        return {{}, QStringLiteral("identity-bearing sidecar must identify AP242 source")};
    const QString tessellationSha = root.value(QStringLiteral("tessellation_sha256")).toString();
    if (!digest(tessellationSha))
        return {{}, QStringLiteral("tessellation digest is not a SHA-256 value")};
    if (!expectedTessellationSha256.isEmpty()
        && tessellationSha != expectedTessellationSha256)
        return {{}, QStringLiteral("semantic assembly tessellation digest does not match")};
    const QJsonArray array = root.value(QStringLiteral("components")).toArray();
    if (array.isEmpty() || array.size() > 100000)
        return {{}, QStringLiteral("semantic assembly must contain components")};

    auto result = std::make_shared<SemanticAssembly>();
    result->schema = root.value(QStringLiteral("schema")).toString();
    result->sourceFormat = root.value(QStringLiteral("source_format")).toString();
    result->sourceStepSha256 = sourceSha256;
    result->tessellationSha256 = tessellationSha;
    result->identityAvailable = true;
    result->legacyFlattened = false;
    qsizetype nextTriangle = 0;
    QSet<QString> ids;
    for (int index = 0; index < array.size(); ++index) {
        const QJsonObject object = array.at(index).toObject();
        SemanticAssemblyComponent component;
        component.semanticId = object.value(QStringLiteral("semantic_id")).toString();
        component.parentId = object.value(QStringLiteral("parent_id")).toString();
        component.name = object.value(QStringLiteral("name")).toString();
        component.referenceDesignator = object.value(QStringLiteral("reference_designator")).toString();
        // The AP242 preview prefixes member names to keep STEP product labels
        // globally unique (for example Component_U1).  Expose the electrical
        // reference designator to the UI as U1 while retaining the full
        // semantic/name identity for traceability and selection.
        if (component.referenceDesignator.startsWith(QStringLiteral("Component_")))
            component.referenceDesignator.remove(0, QStringLiteral("Component_").size());
        component.material = object.value(QStringLiteral("material")).toString();
        if (!id(component.semanticId) || ids.contains(component.semanticId)
            || !id(component.parentId)
            || component.parentId == component.semanticId
            || component.name.isEmpty() || component.material.isEmpty())
            return {{}, QStringLiteral("semantic assembly component %1 has invalid identity fields").arg(index)};
        bool validColor = false;
        component.color = color(object.value(QStringLiteral("color_rgba")), &validColor);
        bool validPlacement = false;
        component.placement = placement(object.value(QStringLiteral("placement")), &validPlacement);
        const QJsonValue start = object.value(QStringLiteral("triangle_start"));
        const QJsonValue count = object.value(QStringLiteral("triangle_count"));
        if (!validColor || !validPlacement || !start.isDouble() || !count.isDouble()
            || start.toDouble() < 0 || count.toDouble() <= 0
            || std::floor(start.toDouble()) != start.toDouble()
            || std::floor(count.toDouble()) != count.toDouble())
            return {{}, QStringLiteral("semantic assembly component %1 has invalid mesh range").arg(index)};
        component.triangleStart = qsizetype(start.toDouble());
        component.triangleCount = qsizetype(count.toDouble());
        if (component.triangleStart != nextTriangle
            || component.triangleStart + component.triangleCount > triangleCount)
            return {{}, QStringLiteral("semantic assembly mesh ranges must be ordered and cover the source mesh")};
        const QJsonArray faces = object.value(QStringLiteral("face_ids")).toArray();
        for (const QJsonValue& face : faces) {
            if (!face.isString() || face.toString().isEmpty())
                return {{}, QStringLiteral("semantic assembly face IDs are invalid")};
            component.faceIds.append(face.toString());
        }
        ids.insert(component.semanticId);
        nextTriangle += component.triangleCount;
        result->components.append(component);
    }
    if (nextTriangle != triangleCount)
        return {{}, QStringLiteral("semantic assembly mesh ranges do not cover all triangles")};
    return {std::move(result), {}};
}

QString SemanticAssemblyReader::tessellationDigest(
    const QVector<QVector3D>& verticesMm, const QVector<quint32>& triangleIndices)
{
    QByteArray bytes;
    QDataStream stream(&bytes, QIODevice::WriteOnly);
    stream.setVersion(QDataStream::Qt_6_0);
    stream.setByteOrder(QDataStream::LittleEndian);
    stream << quint64(verticesMm.size()) << quint64(triangleIndices.size());
    for (const QVector3D& point : verticesMm)
        stream << point.x() << point.y() << point.z();
    for (quint32 index : triangleIndices) stream << index;
    return QString::fromLatin1(QCryptographicHash::hash(bytes,
                                                         QCryptographicHash::Sha256).toHex());
}

} // namespace designstudio

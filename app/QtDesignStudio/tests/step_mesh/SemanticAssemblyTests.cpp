#include "SemanticAssembly.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>

#include <iostream>

using namespace designstudio;

namespace {

QJsonObject component(const char* id, const char* ref, int start)
{
    QJsonArray placement;
    for (int index = 0; index < 16; ++index) {
        const bool diagonal = index % 5 == 0;
        const bool translation = index == 3;
        placement.append(translation ? (start == 0 ? -8.0 : 8.0)
                                     : (diagonal ? 1.0 : 0.0));
    }
    return QJsonObject{
        {"semantic_id", id}, {"parent_id", "root"}, {"name", QString("Part %1").arg(ref)},
        {"reference_designator", ref}, {"material", "FR4"},
        {"color_rgba", QJsonArray{start == 0 ? 0.1 : 0.8, 0.2, 0.4, 1.0}},
        {"placement", placement}, {"triangle_start", start}, {"triangle_count", 1},
        {"face_ids", QJsonArray{QString("%1:Face1").arg(id)}}};
}

QByteArray fixture()
{
    const QJsonObject root{
        {"schema", "design-studio.semantic-assembly/2"}, {"source_format", "AP242"},
        {"source_step_sha256", QString(64, QLatin1Char('a'))},
        {"tessellation_sha256", QString(64, QLatin1Char('b'))},
        {"identity_available", true}, {"legacy_flattened", false},
        {"components", QJsonArray{component("u1", "U1", 0), component("j1", "J1", 1)}}};
    return QJsonDocument(root).toJson(QJsonDocument::Compact);
}

bool check(bool value, const char* message)
{
    if (!value) std::cerr << "FAIL: " << message << '\n';
    return value;
}

} // namespace

int main()
{
    const QString source(64, QLatin1Char('a'));
    const auto loaded = SemanticAssemblyReader::fromJson(fixture(), source, 2);
    bool ok = check(loaded.ok(), "valid AP242 semantic assembly rejected");
    if (loaded.ok()) {
        ok = check(loaded.assembly->identityAvailable, "identity flag lost") && ok;
        ok = check(loaded.assembly->components.size() == 2, "component count mismatch") && ok;
        ok = check(loaded.assembly->componentForTriangle(0) == QStringLiteral("u1"),
                   "first triangle ownership mismatch") && ok;
        ok = check(loaded.assembly->componentForTriangle(1) == QStringLiteral("j1"),
                   "second triangle ownership mismatch") && ok;
        ok = check(loaded.assembly->componentForVertex(4, QVector<quint32>{0, 1, 2, 2, 3, 4})
                       == QStringLiteral("j1"), "vertex selection did not map to component") && ok;
        ok = check(loaded.assembly->components.at(0).referenceDesignator == QStringLiteral("U1"),
                   "reference designator was not retained") && ok;
        ok = check(loaded.assembly->components.at(0).placement(0, 3) == -8.0f
                       && loaded.assembly->components.at(1).placement(0, 3) == 8.0f,
                   "component placements were not retained") && ok;
        ok = check(loaded.assembly->components.at(0).material == QStringLiteral("FR4")
                       && loaded.assembly->components.at(0).faceIds
                              == QStringList{QStringLiteral("u1:Face1")},
                   "component material or face identity was not retained") && ok;
    }
    const auto mismatch = SemanticAssemblyReader::fromJson(
        fixture(), QString(64, QLatin1Char('c')), 2);
    ok = check(!mismatch.ok() && mismatch.error.contains(QStringLiteral("digest")),
               "stale semantic assembly digest accepted") && ok;
    const auto gap = SemanticAssemblyReader::fromJson(fixture(), source, 3);
    ok = check(!gap.ok() && gap.error.contains(QStringLiteral("ranges")),
               "uncovered triangle range accepted") && ok;
    const auto tessellationMismatch = SemanticAssemblyReader::fromJson(
        fixture(), source, 2, QString(64, QLatin1Char('c')));
    ok = check(!tessellationMismatch.ok()
                   && tessellationMismatch.error.contains(QStringLiteral("tessellation")),
               "stale tessellation digest accepted") && ok;
    return ok ? 0 : 1;
}

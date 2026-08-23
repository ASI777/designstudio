#include "GlbMeshReader.h"
#include "MeshProjectionView.h"
#include "SemanticAssembly.h"
#include "StepMeshReader.h"

#include <QApplication>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>

#include <iostream>

using namespace designstudio;

namespace {

QJsonObject component(const QString& id, const QString& reference,
                      qsizetype start, qsizetype count, const QColor& color,
                      double xTranslation)
{
    QJsonArray matrix;
    for (int index = 0; index < 16; ++index) {
        const bool diagonal = index % 5 == 0;
        const bool translation = index == 3;
        matrix.append(translation ? xTranslation : (diagonal ? 1.0 : 0.0));
    }
    QJsonArray rgba{color.redF(), color.greenF(), color.blueF(), color.alphaF()};
    return {
        {"semantic_id", id},
        {"parent_id", "assembly-root"},
        {"name", QStringLiteral("AP242 %1").arg(reference)},
        {"reference_designator", reference},
        {"material", reference == QStringLiteral("U1") ? "silicon" : "FR4"},
        {"color_rgba", rgba},
        {"placement", matrix},
        {"triangle_start", qint64(start)},
        {"triangle_count", qint64(count)},
        {"face_ids", QJsonArray{QStringLiteral("%1:Face1").arg(id)}}
    };
}

bool writeSidecar(const QString& path, const GlbMesh& mesh)
{
    const QString sourceSha = mesh.sha256();
    const qsizetype triangles = mesh.triangleIndices().size() / 3;
    if (triangles < 2) return false;
    const qsizetype first = triangles / 2;
    const QJsonObject root{
        {"schema", "design-studio.semantic-assembly/2"},
        {"source_format", "AP242"},
        {"source_step_sha256", sourceSha},
        {"tessellation_sha256", SemanticAssemblyReader::tessellationDigest(
                                    mesh.verticesMm(), mesh.triangleIndices())},
        {"identity_available", true},
        {"legacy_flattened", false},
        {"components", QJsonArray{
            component("u1", "U1", 0, first, QColor("#d97706"), -8.0),
            component("j1", "J1", first, triangles - first, QColor("#2563eb"), 8.0)}}
    };
    QFile file(path);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) return false;
    const QByteArray bytes = QJsonDocument(root).toJson(QJsonDocument::Compact);
    const bool written = file.write(bytes) == bytes.size();
    file.close();
    return written;
}

bool check(bool value, const char* message)
{
    if (!value) std::cerr << "FAIL: " << message << '\n';
    return value;
}

} // namespace

int main(int argc, char** argv)
{
    QApplication application(argc, argv);
    if (application.arguments().size() != 2) return 2;
    const QString stepPath = application.arguments().at(1);
    const QString sidecarPath = SemanticAssemblyReader::sidecarPath(stepPath);
    QFile::remove(sidecarPath);

    const auto baseline = StepMeshReader::loadFile(stepPath);
    bool ok = check(baseline.ok(), "AP242 fixture did not load without sidecar");
    if (!baseline.ok()) {
        std::cerr << baseline.error.toStdString() << '\n';
        return 1;
    }
    ok = check(baseline.mesh->assembly()->identityAvailable,
               "native XCAF AP242 identity was not extracted") && ok;
    const qsizetype triangles = baseline.mesh->triangleIndices().size() / 3;
    ok = check(baseline.mesh->assembly()->components.size() == 2,
               "native XCAF component count was not retained") && ok;
    if (baseline.mesh->assembly()->components.size() == 2) {
        const auto& first = baseline.mesh->assembly()->components.at(0);
        const auto& second = baseline.mesh->assembly()->components.at(1);
        ok = check(first.referenceDesignator == QStringLiteral("U1")
                       && second.referenceDesignator == QStringLiteral("J1"),
                   "native XCAF reference designators were not retained") && ok;
        ok = check(first.color != second.color && first.color != QColor("#808080"),
                   "native XCAF source colors were not retained") && ok;
        ok = check(first.material == QStringLiteral("silicon")
                       && second.material == QStringLiteral("FR4"),
                   "native XCAF materials were not retained") && ok;
        ok = check(baseline.mesh->assembly()->componentForTriangle(triangles - 1)
                       == second.semanticId,
                   "native XCAF triangle ownership was not retained") && ok;
    }

    // A second no-sidecar load must consume the raw native JSON stored in the
    // cache, proving the cache does not flatten AP242 identity.
    const auto nativeCached = StepMeshReader::loadFile(stepPath);
    ok = check(nativeCached.ok() && nativeCached.mesh->assembly()->identityAvailable
                   && nativeCached.mesh->assembly()->components.size() == 2,
               "native semantic identity was lost after raw cache reload") && ok;

    ok = check(writeSidecar(sidecarPath, *baseline.mesh),
               "could not create AP242 identity sidecar") && ok;

    const auto loaded = StepMeshReader::loadFile(stepPath);
    ok = check(loaded.ok(), "AP242 sidecar load failed") && ok;
    if (loaded.ok()) {
        ok = check(loaded.mesh->assembly()->identityAvailable,
                   "AP242 identity was not exposed") && ok;
        ok = check(loaded.mesh->assembly()->components.size() == 2,
                   "AP242 component count was not retained") && ok;
        ok = check(loaded.mesh->assembly()->components.at(0).referenceDesignator
                       == QStringLiteral("U1"),
                   "AP242 reference designator was not retained") && ok;
        ok = check(loaded.mesh->assembly()->colorForComponent(QStringLiteral("j1"))
                       == QColor("#2563eb"),
                   "AP242 source color was not retained") && ok;
        ok = check(loaded.mesh->assembly()->componentForTriangle(triangles - 1)
                       == QStringLiteral("j1"),
                   "AP242 triangle ownership was not retained") && ok;
    }

    // The second load must use the sidecar-aware cache key and preserve the
    // same identities, not silently fall back to the flattened representation.
    const auto cached = StepMeshReader::loadFile(stepPath);
    ok = check(cached.ok() && cached.mesh->assembly()->identityAvailable,
               "semantic identity was lost after cache reload") && ok;
    if (cached.ok()) {
        ok = check(cached.mesh->assembly()->components.at(0).placement(0, 3) == -8.0f
                       && cached.mesh->assembly()->components.at(1).placement(0, 3) == 8.0f,
                   "component placements were not retained after cache reload") && ok;
        MeshProjectionView view;
        view.setMesh(cached.mesh);
        view.setSelectedComponentReference(QStringLiteral("J1"));
        ok = check(view.selectedComponent() == QStringLiteral("j1"),
                   "viewport selection did not resolve the cached reference") && ok;
        view.setSelectedComponent(QStringLiteral("u1"));
        ok = check(view.selectedComponent() == QStringLiteral("u1"),
                   "viewport selection did not resolve the cached semantic ID") && ok;
    }

    QFile::remove(sidecarPath);
    const auto nativeAgain = StepMeshReader::loadFile(stepPath);
    ok = check(nativeAgain.ok() && nativeAgain.mesh->assembly()->identityAvailable,
               "native AP242 identity was lost after sidecar removal") && ok;
    return ok ? 0 : 1;
}

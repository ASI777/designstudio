#include "StepMeshReader.h"

#include "GlbMeshReader.h"
#include "SemanticAssembly.h"
#include "designcore/c_api.h"

#include <QCryptographicHash>
#include <QDataStream>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QSaveFile>
#include <QStandardPaths>
#include <QVector>
#include <QVector3D>

#include <limits>

namespace designstudio {
namespace {
constexpr quint32 CacheVersion = 2;
constexpr quint32 MaxCachedVertices = 5'000'000;
constexpr quint32 MaxCachedTriangles = 5'000'000;

QString cachePathFor(const QString& key)
{
    return QDir(StepMeshReader::cacheDirectory()).filePath(key + QStringLiteral(".dsm"));
}

std::shared_ptr<const GlbMesh> readCache(const QString& key,
                                         const QFileInfo& source,
                                         QString* error)
{
    QFile file(cachePathFor(key));
    if (!file.open(QIODevice::ReadOnly)) return {};
    QDataStream stream(&file);
    stream.setVersion(QDataStream::Qt_6_5);
    QByteArray magic;
    quint32 version = 0;
    qint64 sourceBytes = -1;
    QString sourceName;
    QString sourceSha;
    QVector3D minimum, maximum;
    quint32 vertexCount = 0, triangleCount = 0;
    QByteArray semanticAssemblyJson;
    stream >> magic >> version >> sourceBytes >> sourceName >> sourceSha
           >> minimum >> maximum >> vertexCount >> triangleCount >> semanticAssemblyJson;
    if (stream.status() != QDataStream::Ok || magic != QByteArrayLiteral("DS_STEP_MESH")
        || version != CacheVersion || sourceBytes != source.size()
        || vertexCount == 0 || vertexCount > MaxCachedVertices
        || triangleCount == 0 || triangleCount > MaxCachedTriangles) {
        if (error) *error = QStringLiteral("invalid STEP mesh cache");
        return {};
    }
    QVector<QVector3D> vertices;
    QVector<quint32> indices;
    vertices.reserve(vertexCount);
    indices.reserve(qsizetype(triangleCount) * 3);
    for (quint32 i = 0; i < vertexCount; ++i) {
        float x = 0, y = 0, z = 0;
        stream >> x >> y >> z;
        vertices.append(QVector3D(x, y, z));
    }
    for (quint64 i = 0; i < quint64(triangleCount) * 3; ++i) {
        quint32 index = 0;
        stream >> index;
        if (index >= vertexCount) {
            if (error) *error = QStringLiteral("STEP mesh cache has an invalid index");
            return {};
        }
        indices.append(index);
    }
    if (stream.status() != QDataStream::Ok) {
        if (error) *error = QStringLiteral("truncated STEP mesh cache");
        return {};
    }
    return std::make_shared<const GlbMesh>(std::move(vertices), std::move(indices),
                                           minimum, maximum, std::move(sourceSha),
                                           std::move(sourceName), sourceBytes, nullptr,
                                           std::move(semanticAssemblyJson));
}

void writeCache(const QString& key, const GlbMesh& mesh)
{
    const QString directory = StepMeshReader::cacheDirectory();
    if (directory.isEmpty()) return;
    QDir().mkpath(directory);
    QSaveFile file(cachePathFor(key));
    if (!file.open(QIODevice::WriteOnly)) return;
    QDataStream stream(&file);
    stream.setVersion(QDataStream::Qt_6_5);
    stream << QByteArrayLiteral("DS_STEP_MESH") << CacheVersion
           << mesh.sourceBytes() << mesh.sourceName() << mesh.sha256()
           << mesh.boundsMinMm() << mesh.boundsMaxMm()
           << quint32(mesh.verticesMm().size())
           << quint32(mesh.triangleIndices().size() / 3)
           << mesh.semanticAssemblyJson();
    for (const QVector3D& point : mesh.verticesMm())
        stream << point.x() << point.y() << point.z();
    for (quint32 index : mesh.triangleIndices()) stream << index;
    if (stream.status() == QDataStream::Ok) file.commit();
}
} // namespace

QString StepMeshReader::cacheDirectory()
{
    // Use the generic cache root rather than the host application's
    // CacheLocation.  The same tessellation must be reusable when a STEP is
    // first inspected from the FreeCAD-embedded launcher and later from the
    // standalone DesignStudio diagnostics window.
    const QString base = QStandardPaths::writableLocation(
        QStandardPaths::GenericCacheLocation);
    return base.isEmpty() ? QDir::temp().filePath(QStringLiteral("DesignStudio/step-mesh"))
                          : QDir(base).filePath(QStringLiteral("DesignStudio/step-mesh"));
}

QString StepMeshReader::cacheKey(const QString& path, double deflectionMm)
{
    const QFileInfo source(path);
    const QString canonical = source.exists() ? source.canonicalFilePath()
                                               : source.absoluteFilePath();
    const QFileInfo sidecar(SemanticAssemblyReader::sidecarPath(path));
    const QByteArray identity = QStringLiteral("%1|%2|%3|%4|%5|%6")
        .arg(canonical).arg(source.size()).arg(source.lastModified().toMSecsSinceEpoch())
        .arg(deflectionMm, 0, 'g', 17)
        .arg(sidecar.exists() ? sidecar.size() : -1)
        .arg(sidecar.exists() ? sidecar.lastModified().toMSecsSinceEpoch() : -1).toUtf8();
    return QString::fromLatin1(QCryptographicHash::hash(identity,
                                                         QCryptographicHash::Sha256).toHex());
}

StepMeshLoadResult StepMeshReader::loadFile(const QString& path, double deflectionMm)
{
    QFile source(path);
    if (!source.open(QIODevice::ReadOnly))
        return {{}, QStringLiteral("Could not read STEP file: %1").arg(source.errorString())};
    constexpr qint64 MaxStepBytes = 1024LL * 1024LL * 1024LL;
    if (source.size() <= 0 || source.size() > MaxStepBytes)
        return {{}, QStringLiteral("STEP file size is empty or exceeds the 1 GiB preview limit")};
    const QFileInfo sourceInfo(path);
    const QString key = cacheKey(path, deflectionMm);
    QString cacheError;
    if (const auto cached = readCache(key, sourceInfo, &cacheError))
    {
        const QString tessellationSha = SemanticAssemblyReader::tessellationDigest(
            cached->verticesMm(), cached->triangleIndices());
        const auto assembly = cached->semanticAssemblyJson().isEmpty()
            ? SemanticAssemblyReader::loadForStep(
                path, cached->sha256(), cached->triangleIndices().size() / 3,
                tessellationSha)
            : SemanticAssemblyReader::fromJson(
                cached->semanticAssemblyJson(), cached->sha256(),
                cached->triangleIndices().size() / 3, tessellationSha);
        if (!assembly.ok()) return {{}, assembly.error};
        return {std::make_shared<const GlbMesh>(
                    cached->verticesMm(), cached->triangleIndices(),
                    cached->boundsMinMm(), cached->boundsMaxMm(), cached->sha256(),
                    cached->sourceName(), cached->sourceBytes(), assembly.assembly,
                    cached->semanticAssemblyJson()), {}};
    }
    const QByteArray sourceBytes = source.readAll();
    source.close();

    DcShapeHandle shape = nullptr;
    const QByteArray encodedPath = QFile::encodeName(QFileInfo(path).absoluteFilePath());
    const std::int32_t loadStatus = dc_brep_load_step(encodedPath.constData(), &shape);
    if (loadStatus == DC_ERR_UNSUPPORTED)
        return {{}, QStringLiteral("This DesignStudio build has no Open CASCADE STEP support")};
    if (loadStatus != DC_OK || !shape)
        return {{}, QStringLiteral("DesignCore rejected the STEP B-Rep (status %1)")
                        .arg(loadStatus)};

    DcMesh raw{};
    const std::int32_t meshStatus = dc_brep_tessellate(shape, deflectionMm, &raw);
    if (meshStatus != DC_OK || raw.nverts <= 0 || raw.ntris <= 0
        || !raw.xyz || !raw.idx) {
        dc_shape_destroy(shape);
        return {{}, QStringLiteral("Could not tessellate STEP geometry (status %1)")
                        .arg(meshStatus)};
    }
    if (raw.nverts > 5'000'000 || raw.ntris > 5'000'000) {
        dc_shape_destroy(shape);
        return {{}, QStringLiteral("STEP preview exceeds the 5 million vertex/triangle limit")};
    }

    QVector<QVector3D> vertices;
    QVector<quint32> indices;
    vertices.reserve(qsizetype(raw.nverts));
    indices.reserve(qsizetype(raw.ntris * 3));
    QVector3D minimum(std::numeric_limits<float>::max(),
                      std::numeric_limits<float>::max(),
                      std::numeric_limits<float>::max());
    QVector3D maximum(std::numeric_limits<float>::lowest(),
                      std::numeric_limits<float>::lowest(),
                      std::numeric_limits<float>::lowest());
    for (std::int64_t i = 0; i < raw.nverts; ++i) {
        const QVector3D point(raw.xyz[i * 3], raw.xyz[i * 3 + 1], raw.xyz[i * 3 + 2]);
        vertices.push_back(point);
        minimum.setX(qMin(minimum.x(), point.x()));
        minimum.setY(qMin(minimum.y(), point.y()));
        minimum.setZ(qMin(minimum.z(), point.z()));
        maximum.setX(qMax(maximum.x(), point.x()));
        maximum.setY(qMax(maximum.y(), point.y()));
        maximum.setZ(qMax(maximum.z(), point.z()));
    }
    for (std::int64_t i = 0; i < raw.ntris * 3; ++i) {
        if (raw.idx[i] < 0 || raw.idx[i] >= raw.nverts) {
            dc_shape_destroy(shape);
            return {{}, QStringLiteral("STEP tessellator returned an invalid triangle index")};
        }
        indices.push_back(quint32(raw.idx[i]));
    }
    const QString sourceSha = QString::fromLatin1(QCryptographicHash::hash(
        sourceBytes, QCryptographicHash::Sha256).toHex());
    const QString tessellationSha = SemanticAssemblyReader::tessellationDigest(vertices, indices);
    QByteArray semanticJson;
    const QFileInfo semanticSidecar(SemanticAssemblyReader::sidecarPath(path));
    SemanticAssemblyLoadResult assembly;
    if (semanticSidecar.exists()) {
        QFile sidecar(semanticSidecar.absoluteFilePath());
        if (!sidecar.open(QIODevice::ReadOnly)) {
            dc_shape_destroy(shape);
            return {{}, QStringLiteral("Could not read semantic assembly sidecar: %1")
                            .arg(sidecar.errorString())};
        }
        semanticJson = sidecar.readAll();
        assembly = SemanticAssemblyReader::fromJson(
            semanticJson, sourceSha, indices.size() / 3, tessellationSha);
    } else {
        DcSemanticAssemblyJson nativeAssembly{};
        const std::int32_t nativeStatus = dc_brep_semantic_assembly(
            shape, sourceSha.toUtf8().constData(), tessellationSha.toUtf8().constData(),
            &nativeAssembly);
        if (nativeStatus == DC_OK && nativeAssembly.json && nativeAssembly.size > 0) {
            semanticJson = QByteArray(nativeAssembly.json,
                                      static_cast<qsizetype>(nativeAssembly.size));
            assembly = SemanticAssemblyReader::fromJson(
                semanticJson, sourceSha, indices.size() / 3, tessellationSha);
        } else {
            assembly = {nullptr, {}};
            assembly.assembly = std::make_shared<const SemanticAssembly>();
            // Legacy flattened STEP is an intentional, visible fallback when
            // the AP242/XCAF reader is unavailable or has no component map.
            auto legacy = std::make_shared<SemanticAssembly>();
            legacy->schema = QStringLiteral("design-studio.semantic-assembly/2");
            legacy->sourceFormat = QStringLiteral("STEP");
            legacy->sourceStepSha256 = sourceSha;
            legacy->identityAvailable = false;
            legacy->legacyFlattened = true;
            SemanticAssemblyComponent component;
            component.semanticId = QStringLiteral("legacy-flat-step");
            component.name = QStringLiteral("Flattened STEP (identity unavailable)");
            component.material = QStringLiteral("unknown");
            component.triangleCount = indices.size() / 3;
            component.color = QColor(QStringLiteral("#7f8c99"));
            component.placement.setToIdentity();
            legacy->components.append(component);
            assembly.assembly = std::move(legacy);
        }
    }
    dc_shape_destroy(shape);
    if (!assembly.ok()) return {{}, assembly.error};
    auto mesh = std::make_shared<GlbMesh>(
        std::move(vertices), std::move(indices), minimum, maximum,
        sourceSha, QFileInfo(path).fileName(), sourceBytes.size(), assembly.assembly,
        std::move(semanticJson));
    writeCache(key, *mesh);
    return {std::move(mesh), {}};
}

} // namespace designstudio

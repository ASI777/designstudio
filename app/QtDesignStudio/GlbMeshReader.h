#pragma once

#include <QByteArray>
#include <QString>
#include <QVector>
#include <QVector3D>

#include <memory>

namespace designstudio {

class SemanticAssembly;

struct GlbValidationIssue {
    QString path;
    QString message;
};

class GlbMesh final {
public:
    GlbMesh(QVector<QVector3D> verticesMm,
            QVector<quint32> triangleIndices,
            const QVector3D& boundsMinMm,
            const QVector3D& boundsMaxMm,
            QString sha256,
            QString sourceName,
            qint64 sourceBytes);
    GlbMesh(QVector<QVector3D> verticesMm,
            QVector<quint32> triangleIndices,
            const QVector3D& boundsMinMm,
            const QVector3D& boundsMaxMm,
            QString sha256,
            QString sourceName,
            qint64 sourceBytes,
            std::shared_ptr<const SemanticAssembly> assembly);
    GlbMesh(QVector<QVector3D> verticesMm,
            QVector<quint32> triangleIndices,
            const QVector3D& boundsMinMm,
            const QVector3D& boundsMaxMm,
            QString sha256,
            QString sourceName,
            qint64 sourceBytes,
            std::shared_ptr<const SemanticAssembly> assembly,
            QByteArray semanticAssemblyJson);

    const QVector<QVector3D>& verticesMm() const noexcept { return verticesMm_; }
    const QVector<quint32>& triangleIndices() const noexcept { return triangleIndices_; }
    const QVector3D& boundsMinMm() const noexcept { return boundsMinMm_; }
    const QVector3D& boundsMaxMm() const noexcept { return boundsMaxMm_; }
    QVector3D sizeMm() const noexcept { return boundsMaxMm_ - boundsMinMm_; }
    const QString& sha256() const noexcept { return sha256_; }
    const QString& sourceName() const noexcept { return sourceName_; }
    qint64 sourceBytes() const noexcept { return sourceBytes_; }
    const std::shared_ptr<const SemanticAssembly>& assembly() const noexcept { return assembly_; }
    const QByteArray& semanticAssemblyJson() const noexcept { return semanticAssemblyJson_; }

private:
    QVector<QVector3D> verticesMm_;
    QVector<quint32> triangleIndices_;
    QVector3D boundsMinMm_;
    QVector3D boundsMaxMm_;
    QString sha256_;
    QString sourceName_;
    qint64 sourceBytes_ = 0;
    std::shared_ptr<const SemanticAssembly> assembly_;
    QByteArray semanticAssemblyJson_;
};

struct GlbLoadResult {
    std::shared_ptr<const GlbMesh> mesh;
    QVector<GlbValidationIssue> issues;

    bool ok() const noexcept { return mesh != nullptr && issues.isEmpty(); }
    QString errorSummary() const;
};

class GlbMeshReader final {
public:
    static constexpr qint64 MaxInputBytes = 256LL * 1024LL * 1024LL;
    static constexpr qsizetype MaxVertices = 5'000'000;
    static constexpr qsizetype MaxTriangles = 5'000'000;

    static GlbLoadResult loadFile(const QString& path);
    static GlbLoadResult loadBytes(const QByteArray& bytes,
                                   const QString& sourceName = QStringLiteral("<memory>"));

private:
    GlbMeshReader() = delete;
};

} // namespace designstudio

#pragma once

#include <QColor>
#include <QByteArray>
#include <QMatrix4x4>
#include <QString>
#include <QStringList>
#include <QVector>

#include <memory>

namespace designstudio {

struct SemanticAssemblyComponent final {
    QString semanticId;
    QString parentId;
    QString name;
    QString referenceDesignator;
    QString material;
    QColor color{Qt::gray};
    QMatrix4x4 placement;
    qsizetype triangleStart{0};
    qsizetype triangleCount{0};
    QStringList faceIds;
};

class SemanticAssembly final {
public:
    QString schema;
    QString sourceFormat;
    QString sourceStepSha256;
    QString tessellationSha256;
    bool identityAvailable{false};
    bool legacyFlattened{true};
    QVector<SemanticAssemblyComponent> components;

    QString componentForTriangle(qsizetype triangleIndex) const;
    QString componentForVertex(quint32 vertexIndex,
                               const QVector<quint32>& triangleIndices) const;
    int componentIndex(const QString& semanticId) const;
    QColor colorForComponent(const QString& semanticId) const;
};

struct SemanticAssemblyLoadResult final {
    std::shared_ptr<const SemanticAssembly> assembly;
    QString error;

    bool ok() const noexcept { return assembly != nullptr && error.isEmpty(); }
};

// Reads the authoritative semantic-assembly/2 sidecar emitted by the
// FreeCAD/XCAF bridge. The sidecar is digest-bound to the STEP and to the
// tessellation; it is never inferred from screen pixels or triangle position.
class SemanticAssemblyReader final {
public:
    static SemanticAssemblyLoadResult loadForStep(const QString& stepPath,
                                                   const QString& sourceSha256,
                                                   qsizetype triangleCount,
                                                   const QString& expectedTessellationSha256 = {});
    static SemanticAssemblyLoadResult fromJson(const QByteArray& bytes,
                                                const QString& sourceSha256,
                                                qsizetype triangleCount,
                                                const QString& expectedTessellationSha256 = {});
    static QString tessellationDigest(const QVector<QVector3D>& verticesMm,
                                      const QVector<quint32>& triangleIndices);
    static QString sidecarPath(const QString& stepPath);

private:
    SemanticAssemblyReader() = delete;
};

} // namespace designstudio

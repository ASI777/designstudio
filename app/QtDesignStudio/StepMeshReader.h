#pragma once

#include <QString>

#include <memory>

namespace designstudio {
class GlbMesh;

struct StepMeshLoadResult {
    std::shared_ptr<const GlbMesh> mesh;
    QString error;

    bool ok() const noexcept { return mesh != nullptr && error.isEmpty(); }
};

// Converts an AP203/AP214/AP242 STEP B-Rep to immutable display triangles by
// using DesignCore's public OCCT boundary. The STEP file remains the authority;
// this mesh is display-only and never written back into the project.
class StepMeshReader final {
public:
    static StepMeshLoadResult loadFile(const QString& path,
                                       double deflectionMm = 0.15);

    // The cache contains display-only tessellation.  It is keyed by the
    // canonical STEP path, file size/mtime and tessellation deflection, so a
    // changed source is never silently reused.  Cache operations are safe to
    // call from a worker thread and never change the authoritative STEP file.
    static QString cacheKey(const QString& path, double deflectionMm = 0.15);
    static QString cacheDirectory();

private:
    StepMeshReader() = delete;
};

} // namespace designstudio

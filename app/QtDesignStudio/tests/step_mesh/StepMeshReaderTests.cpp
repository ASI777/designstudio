#include "GlbMeshReader.h"
#include "StepMeshReader.h"

#include <QCoreApplication>

#include <cmath>
#include <iostream>

int main(int argc, char** argv)
{
    QCoreApplication application(argc, argv);
    if (application.arguments().size() != 2) return 2;
    const auto loaded = designstudio::StepMeshReader::loadFile(application.arguments().at(1));
    if (!loaded.ok()) {
        std::cerr << loaded.error.toStdString() << '\n';
        return 1;
    }
    const QVector3D size = loaded.mesh->sizeMm();
    const bool correctBounds = std::abs(size.x() - 20.0f) < 0.01f
        && std::abs(size.y() - 10.0f) < 0.01f
        && std::abs(size.z() - 1.6f) < 0.01f;
    const bool validMesh = !loaded.mesh->verticesMm().isEmpty()
        && !loaded.mesh->triangleIndices().isEmpty()
        && loaded.mesh->triangleIndices().size() % 3 == 0;
    if (!correctBounds || !validMesh) {
        std::cerr << "STEP display mesh has incorrect bounds or topology\n";
        return 1;
    }
    return 0;
}

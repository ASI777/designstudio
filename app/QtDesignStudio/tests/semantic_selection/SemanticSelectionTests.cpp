#include "SemanticSelectionModel.h"

#include <QCoreApplication>
#include <QObject>

#include <iostream>

int main(int argc, char** argv)
{
    QCoreApplication app(argc, argv);
    designstudio::SemanticSelectionModel model;
    int changes = 0;
    QString lastId;
    QString lastReference;
    QString lastSource;
    QObject::connect(&model, &designstudio::SemanticSelectionModel::selectionChanged,
                     [&](const QString& id, const QString& reference, const QString& source) {
        ++changes;
        lastId = id;
        lastReference = reference;
        lastSource = source;
    });
    model.select(QStringLiteral("step_u1"), QStringLiteral("U1"), QStringLiteral("3D AP242"));
    if (changes != 1 || !model.hasSelection() || lastId != QStringLiteral("step_u1")
        || lastReference != QStringLiteral("U1") || lastSource != QStringLiteral("3D AP242")) {
        std::cerr << "semantic selection publication failed\n";
        return 1;
    }
    model.select(QStringLiteral("step_u1"), QStringLiteral("U1"), QStringLiteral("3D AP242"));
    if (changes != 1) {
        std::cerr << "semantic selection deduplication failed\n";
        return 1;
    }
    model.clear(QStringLiteral("PCB canvas"));
    if (changes != 2 || model.hasSelection() || lastSource != QStringLiteral("PCB canvas")) {
        std::cerr << "semantic selection clear failed\n";
        return 1;
    }
    return 0;
}

#pragma once

#include <QObject>
#include <QString>

namespace designstudio {

// One selection state shared by every product surface.  Views publish a
// stable semantic identity (and optional PCB reference); consumers subscribe
// without directly coupling the 3D, PCB, BOM, graph, or property widgets.
class SemanticSelectionModel final : public QObject {
    Q_OBJECT
public:
    explicit SemanticSelectionModel(QObject* parent = nullptr) : QObject(parent) {}

    QString semanticId() const noexcept { return semanticId_; }
    QString referenceDesignator() const noexcept { return referenceDesignator_; }
    QString source() const noexcept { return source_; }
    bool hasSelection() const noexcept { return !semanticId_.isEmpty() || !referenceDesignator_.isEmpty(); }

public slots:
    void select(const QString& semanticId, const QString& referenceDesignator,
                const QString& source = {});
    void clear(const QString& source = {});

signals:
    void selectionChanged(const QString& semanticId, const QString& referenceDesignator,
                          const QString& source);

private:
    QString semanticId_;
    QString referenceDesignator_;
    QString source_;
};

} // namespace designstudio

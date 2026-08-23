#pragma once

#include <QJsonObject>
#include <QHash>
#include <QList>
#include <QWidget>

class QLabel;
class QTabWidget;
class QTreeWidget;

namespace designstudio {

// Read-only semantic assembly browser.  Each tab is a different product
// surface over the same validated AP242 identity map; selection is published
// through MainWindow's shared SemanticSelectionModel.
class SemanticAssemblyPanel final : public QWidget {
    Q_OBJECT
public:
    explicit SemanticAssemblyPanel(QWidget* parent = nullptr);

    void setAssemblyJson(const QByteArray& json);
    void selectSemanticObject(const QString& semanticId,
                              const QString& referenceDesignator);
    bool hasAssembly() const noexcept { return !components_.isEmpty(); }

signals:
    void semanticObjectActivated(const QString& semanticId,
                                 const QString& referenceDesignator);

private:
    void rebuildViews();
    void updateProperty(const QJsonObject& object);

    QTabWidget* tabs_{};
    QLabel* propertyLabel_{};
    QList<QTreeWidget*> views_;
    QList<QJsonObject> components_;
    QHash<QString, QJsonObject> byId_;
};

} // namespace designstudio

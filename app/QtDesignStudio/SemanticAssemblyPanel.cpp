#include "SemanticAssemblyPanel.h"

#include <QFormLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLabel>
#include <QTabWidget>
#include <QTreeWidget>
#include <QTreeWidgetItem>
#include <QVBoxLayout>

namespace designstudio {

SemanticAssemblyPanel::SemanticAssemblyPanel(QWidget* parent) : QWidget(parent)
{
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(8, 8, 8, 8);
    auto* heading = new QLabel(QStringLiteral("No AP242 semantic assembly loaded."), this);
    heading->setObjectName(QStringLiteral("semanticAssemblyHeading"));
    heading->setWordWrap(true);
    layout->addWidget(heading);

    tabs_ = new QTabWidget(this);
    const QStringList names{QStringLiteral("BOM"), QStringLiteral("Product graph"),
                            QStringLiteral("FreeCAD tree"), QStringLiteral("Properties")};
    for (const QString& name : names) {
        auto* tree = new QTreeWidget(tabs_);
        tree->setColumnCount(4);
        tree->setHeaderLabels({QStringLiteral("Object"), QStringLiteral("Reference"),
                               QStringLiteral("Material"), QStringLiteral("Source")});
        tree->setRootIsDecorated(false);
        tree->setSelectionMode(QAbstractItemView::SingleSelection);
        connect(tree, &QTreeWidget::itemActivated, this,
                [this](QTreeWidgetItem* item, int) {
            if (!item) return;
            emit semanticObjectActivated(item->data(0, Qt::UserRole).toString(),
                                         item->text(1));
            updateProperty(byId_.value(item->data(0, Qt::UserRole).toString()));
        });
        views_.append(tree);
        tabs_->addTab(tree, name);
    }
    layout->addWidget(tabs_, 1);

    propertyLabel_ = new QLabel(QStringLiteral("Select an object to inspect its authoritative identity."), this);
    propertyLabel_->setWordWrap(true);
    propertyLabel_->setTextInteractionFlags(Qt::TextSelectableByMouse);
    layout->addWidget(propertyLabel_);
}

void SemanticAssemblyPanel::setAssemblyJson(const QByteArray& json)
{
    components_.clear();
    byId_.clear();
    QJsonParseError error;
    const QJsonDocument document = QJsonDocument::fromJson(json, &error);
    if (!json.isEmpty() && error.error == QJsonParseError::NoError
        && document.isObject()) {
        const QJsonArray values = document.object().value(QStringLiteral("components")).toArray();
        for (const QJsonValue& value : values) {
            const QJsonObject object = value.toObject();
            const QString id = object.value(QStringLiteral("semantic_id")).toString();
            if (id.isEmpty() || byId_.contains(id)) continue;
            components_.append(object);
            byId_.insert(id, object);
        }
    }
    auto* heading = findChild<QLabel*>(QStringLiteral("semanticAssemblyHeading"));
    if (heading) {
        heading->setText(components_.isEmpty()
            ? QStringLiteral("No AP242 semantic assembly loaded.")
            : QStringLiteral("AP242 semantic assembly · %1 authoritative objects")
                  .arg(components_.size()));
    }
    rebuildViews();
}

void SemanticAssemblyPanel::rebuildViews()
{
    for (QTreeWidget* tree : views_) {
        tree->clear();
        for (const QJsonObject& object : components_) {
            auto* item = new QTreeWidgetItem(tree);
            item->setText(0, object.value(QStringLiteral("name")).toString(
                             object.value(QStringLiteral("semantic_id")).toString()));
            item->setText(1, object.value(QStringLiteral("reference_designator")).toString());
            item->setText(2, object.value(QStringLiteral("material")).toString());
            item->setText(3, QStringLiteral("AP242 / FreeCAD XCAF"));
            item->setData(0, Qt::UserRole,
                          object.value(QStringLiteral("semantic_id")).toString());
        }
    }
    propertyLabel_->setText(QStringLiteral("Select an object to inspect its authoritative identity."));
}

void SemanticAssemblyPanel::selectSemanticObject(const QString& semanticId,
                                                 const QString& referenceDesignator)
{
    const QJsonObject object = byId_.value(semanticId);
    const QString id = object.isEmpty() && !referenceDesignator.isEmpty()
        ? [&] {
            for (const QJsonObject& candidate : components_)
                if (candidate.value(QStringLiteral("reference_designator")).toString()
                    == referenceDesignator)
                    return candidate.value(QStringLiteral("semantic_id")).toString();
            return QString{};
        }() : semanticId;
    for (QTreeWidget* tree : views_) {
        tree->clearSelection();
        for (int index = 0; index < tree->topLevelItemCount(); ++index) {
            auto* item = tree->topLevelItem(index);
            if (item->data(0, Qt::UserRole).toString() == id) {
                tree->setCurrentItem(item);
                tree->scrollToItem(item);
                break;
            }
        }
    }
    updateProperty(byId_.value(id));
}

void SemanticAssemblyPanel::updateProperty(const QJsonObject& object)
{
    if (object.isEmpty()) {
        propertyLabel_->setText(QStringLiteral("Select an object to inspect its authoritative identity."));
        return;
    }
    const QJsonArray placement = object.value(QStringLiteral("placement")).toArray();
    const QJsonArray color = object.value(QStringLiteral("color_rgba")).toArray();
    propertyLabel_->setText(QStringLiteral(
        "ID: %1 · Ref: %2 · Parent: %3\nMaterial: %4 · Color RGBA: %5\n"
        "Placement entries: %6 · Triangle range: %7 + %8 · Face IDs: %9")
        .arg(object.value(QStringLiteral("semantic_id")).toString(),
             object.value(QStringLiteral("reference_designator")).toString(),
             object.value(QStringLiteral("parent_id")).toString(),
             object.value(QStringLiteral("material")).toString(),
             QString::fromUtf8(QJsonDocument(color).toJson(QJsonDocument::Compact)))
        .arg(placement.size())
        .arg(object.value(QStringLiteral("triangle_start")).toInteger())
        .arg(object.value(QStringLiteral("triangle_count")).toInteger())
        .arg(object.value(QStringLiteral("face_ids")).toArray().size()));
}

} // namespace designstudio

#pragma once
#include <QWidget>
class QTreeWidget;
class QTreeWidgetItem;
class QLabel;
class ProjectModel;

class ComponentPanel : public QWidget {
    Q_OBJECT
public:
    explicit ComponentPanel(ProjectModel* model, QWidget* parent = nullptr);
    void refresh();
    void selectComponent(const QString& ref);

signals:
    void componentFocused(const QString& ref);
    // User asked to add a datasheet PDF and extract/generate its footprint.
    void extractDatasheetRequested();

private:
    ProjectModel*  m_model;
    QTreeWidget*   m_tree;
    QLabel*        m_countLabel;

    void onItemActivated(QTreeWidgetItem* item, int col);
    void onSolveFromSvg();   // extract + solve a footprint from a datasheet layout SVG
};

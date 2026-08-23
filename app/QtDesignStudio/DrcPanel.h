#pragma once
#include <QWidget>
#include "CoreBridge.h"
class QTreeWidget;
class QLabel;
class QPushButton;
class QTimer;
class ProjectModel;
template <typename T> class QFutureWatcher;

class DrcPanel : public QWidget {
    Q_OBJECT
public:
    explicit DrcPanel(ProjectModel* model, CoreBridge* core, QWidget* parent = nullptr);
    ~DrcPanel() override;
    void setCore(CoreBridge* core) { m_core = core; }
    void runDrc();
    void clearResults();

signals:
    void violationSelected(double x_mm, double y_mm);

private:
    ProjectModel* m_model;
    CoreBridge*   m_core;
    QTreeWidget*  m_tree;
    QLabel*       m_summary;
    QPushButton*  m_runBtn;
    QPushButton*  m_exportBtn;
    QTimer*       m_autoTimer;
    QFutureWatcher<QJsonObject>* m_watcher{};
    bool          m_running{false};   // guard against re-entrant runs
    bool          m_pendingRerun{false};

    void populate(const QVector<DrcViolation>& violations);
    void populateVerification(const QJsonObject& report);
    QVector<DrcViolation> runOfflineChecks() const;
    void exportVerification();
};

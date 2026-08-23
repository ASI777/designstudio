#pragma once

#include <QJsonObject>
#include <QString>
#include <QWidget>

class AgentdClient;
class QLabel;
class QLineEdit;
class QPushButton;
class QVBoxLayout;

// User-facing view of the digest-bound hardware work-package graph. The panel
// only invokes typed agentd methods; it cannot execute shell commands or start
// remote/GPU infrastructure.
class BuildMapPanel final : public QWidget {
    Q_OBJECT
public:
    explicit BuildMapPanel(QWidget* parent = nullptr);
    void setClient(AgentdClient* client);

signals:
    void statusMessage(const QString& message);

private:
    AgentdClient* m_client{};
    QLineEdit* m_sourceRoot{};
    QLabel* m_state{};
    QLabel* m_budget{};
    QLabel* m_gpuGate{};
    QVBoxLayout* m_packages{};
    QPushButton* m_compile{};
    QPushButton* m_approve{};
    QPushButton* m_runNext{};
    QPushButton* m_runAll{};
    QPushButton* m_cancel{};

    QJsonObject m_graph;
    QJsonObject m_workflow;
    QString m_productDigest;
    QString m_graphDigest;
    QString m_workflowId;
    QString m_approvalDigest;
    bool m_runAllRequested{};

    void discoverSourceRoot();
    void compileBuildMap();
    void approveArchitecture();
    void advanceOne();
    void cancelWorkflow();
    void handleResponse(const QString& method, const QJsonObject& result);
    void rebuildPackages();
    void updateActions();
    void clearWorkflow();
    bool createIntent(QJsonObject* intent, QString* errorMessage) const;
};

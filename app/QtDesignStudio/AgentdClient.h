#pragma once

#include <QJsonObject>
#include <QHash>
#include <QObject>
#include <QProcess>
#include <QString>

class QTimer;

// Supervised, typed JSON-RPC boundary for the local Rust control plane.  The
// daemon is deliberately a child process: the authoritative documents stay in
// the desktop process while semantic/configuration state can recover without
// taking the UI down with it.
class AgentdClient final : public QObject {
    Q_OBJECT
public:
    enum class State { Stopped, Starting, Recovering, Ready, Failed };
    Q_ENUM(State)

    struct WorkspaceDocuments {
        QString manifestPath;
        QString rootPath;
        QString mechanicalPath;
        QString electronicsPath;
        QString workspaceId;
        qint64 workspaceRevision{};
    };

    explicit AgentdClient(QObject* parent = nullptr);
    ~AgentdClient() override;

    static bool resolveWorkspace(const QString& path,
                                 WorkspaceDocuments* documents,
                                 QString* errorMessage = nullptr);
    static QString executablePath();

    bool openWorkspace(const QString& path, QString* errorMessage = nullptr);
    qint64 invoke(const QString& method,
                  const QJsonObject& params,
                  const QString& permission);
    void stop();

    State state() const noexcept { return m_state; }
    QString stateText() const;
    QString executable() const { return m_executable; }
    WorkspaceDocuments workspace() const { return m_workspace; }
    qint64 graphRevision() const noexcept { return m_graphRevision; }
    qint64 daemonRevision() const noexcept { return m_daemonRevision; }

signals:
    void stateChanged(AgentdClient::State state, const QString& detail);
    void workspaceOpened(const QString& workspaceId,
                         qint64 workspaceRevision,
                         qint64 graphRevision,
                         qint64 daemonRevision,
                         bool recovered);
    void requestFailed(const QString& method, const QString& message);
    void responseReceived(const QString& method, const QJsonObject& result);

private:
    struct PendingRequest {
        QString method;
        bool recoveryOpen{};
    };

    QProcess m_process;
    QByteArray m_stdoutBuffer;
    QHash<qint64, PendingRequest> m_pending;
    WorkspaceDocuments m_workspace;
    QString m_executable;
    State m_state{State::Stopped};
    qint64 m_nextRequestId{1};
    qint64 m_graphRevision{};
    qint64 m_daemonRevision{};
    int m_restartAttempts{};
    bool m_stopping{};
    bool m_recoveryOpen{};

    void setState(State state, const QString& detail);
    void startProcess(bool recovering);
    void sendProjectOpen(bool recovering);
    qint64 sendRequest(const QString& method,
                       const QJsonObject& params,
                       const QString& permission,
                       bool recoveryOpen = false);
    void consumeStdout();
    void handleResponse(const QJsonObject& response);
    void scheduleRestart(const QString& reason);
    static QString findExecutable();
};

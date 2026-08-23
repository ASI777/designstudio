#pragma once

#include <QHash>
#include <QJsonArray>
#include <QJsonObject>
#include <QObject>
#include <QProcess>
#include <QQueue>
#include <QString>
#include <QTimer>

// Supervised JSON-RPC client for `codex app-server --stdio`.  DesignStudio is
// the host: Codex receives only the strict dynamic tools supplied at thread
// creation and never gets a Python/FreeCAD execution surface.
class CodexAppServerClient final : public QObject {
    Q_OBJECT
public:
    enum class State { Stopped, Starting, Initializing, Ready, Running, Failed };
    Q_ENUM(State)

    explicit CodexAppServerClient(QObject* parent = nullptr);
    ~CodexAppServerClient() override;

    void start(const QString& workingDirectory,
               const QJsonArray& dynamicTools,
               const QString& developerInstructions);
    void setOptions(const QString& model, const QString& effort,
                    const QString& serviceTier);
    void requestModelList();
    void submit(const QString& prompt);
    // Submit text plus optional protocol-native local image inputs.  PDFs are
    // rendered by the DesignStudio host before this call because the Codex
    // app-server UserInput schema accepts localImage, not local PDF files.
    void submit(const QString& prompt, const QJsonArray& inputItems);
    void newThread();
    void interrupt();
    void stop();

    void completeToolCall(const QString& token, bool success,
                          const QJsonArray& contentItems);
    void completeToolCall(const QString& token, bool success,
                          const QString& text);

    State state() const noexcept { return m_state; }
    QString stateText() const;
    QString threadId() const { return m_threadId; }
    QString executable() const { return m_executable; }

signals:
    void stateChanged(CodexAppServerClient::State state, const QString& detail);
    void assistantDelta(const QString& text);
    void reasoningDelta(const QString& text);
    void activity(const QString& text);
    void modelsAvailable(const QJsonArray& models);
    void toolCallRequested(const QString& token, const QString& tool,
                           const QJsonObject& arguments);
    void turnFinished(bool ok, const QString& detail);

private:
    QProcess m_process;
    QByteArray m_stdoutBuffer;
    State m_state{State::Stopped};
    qint64 m_nextId{1};
    qint64 m_initializeId{};
    qint64 m_modelListId{};
    qint64 m_threadStartId{};
    qint64 m_turnStartId{};
    QString m_executable;
    QString m_workingDirectory;
    QString m_developerInstructions;
    QString m_threadId;
    QString m_activeTurnId;
    QJsonArray m_dynamicTools;
    struct PendingTurn {
        QString prompt;
        QJsonArray inputItems;
    };
    QQueue<PendingTurn> m_pendingPrompts;
    QHash<QString, QJsonValue> m_toolRequestIds;
    QTimer m_restartTimer;
    bool m_supervise{false};
    bool m_turnInFlight{false};
    int m_restartAttempts{0};
    QString m_selectedModel;
    QString m_selectedEffort;
    QString m_selectedServiceTier;

    void setState(State state, const QString& detail);
    qint64 sendRequest(const QString& method, const QJsonObject& params);
    void sendNotification(const QString& method, const QJsonObject& params = {});
    void sendObject(const QJsonObject& object);
    void consumeStdout();
    void handleMessage(const QJsonObject& message);
    void handleResponse(qint64 id, const QJsonObject& result,
                        const QJsonObject& error);
    void handleNotification(const QString& method, const QJsonObject& params);
    void handleServerRequest(const QJsonValue& id, const QString& method,
                             const QJsonObject& params);
    void scheduleRestart(const QString& reason);
    void applySelectedOptions(QJsonObject& params, bool threadStart) const;
    void startThread();
    void startNextTurn();
    static QString findCodex();
};

#include "CodexAppServerClient.h"

#include <QCoreApplication>
#include <QDebug>
#include <QDir>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QProcessEnvironment>
#include <QStandardPaths>
#include <QUuid>

namespace {
QString errorText(const QJsonObject& error)
{
    const QString message = error.value(QStringLiteral("message")).toString();
    return message.isEmpty() ? QStringLiteral("Codex returned an unknown protocol error") : message;
}

QString statusText(const QJsonValue& value)
{
    if (value.isString()) return value.toString();
    if (value.isObject()) return QString::fromUtf8(
        QJsonDocument(value.toObject()).toJson(QJsonDocument::Compact));
    return {};
}
}

CodexAppServerClient::CodexAppServerClient(QObject* parent) : QObject(parent)
{
    m_restartTimer.setSingleShot(true);
    connect(&m_restartTimer, &QTimer::timeout, this, [this] {
        if (!m_supervise || m_process.state() != QProcess::NotRunning) return;
        start(m_workingDirectory, m_dynamicTools, m_developerInstructions);
    });

    m_process.setProcessChannelMode(QProcess::SeparateChannels);
    connect(&m_process, &QProcess::started, this, [this] {
        setState(State::Initializing, QStringLiteral("Codex app-server started"));
        m_initializeId = sendRequest(QStringLiteral("initialize"), QJsonObject{
            {QStringLiteral("clientInfo"), QJsonObject{
                {QStringLiteral("name"), QStringLiteral("DesignStudio")},
                {QStringLiteral("title"), QStringLiteral("DesignStudio Industrial Product Agent")},
                {QStringLiteral("version"), QStringLiteral("1.0")}}},
            {QStringLiteral("capabilities"), QJsonObject{
                {QStringLiteral("experimentalApi"), true}}}
        });
    });
    connect(&m_process, &QProcess::readyReadStandardOutput,
            this, &CodexAppServerClient::consumeStdout);
    connect(&m_process, &QProcess::readyReadStandardError, this, [this] {
        const QString text = QString::fromUtf8(m_process.readAllStandardError()).trimmed();
        if (!text.isEmpty()) {
            qWarning().noquote() << "CODEX_APP_SERVER:" << text;
            emit activity(text);
        }
    });
    connect(&m_process, &QProcess::errorOccurred, this,
            [this](QProcess::ProcessError) {
        if (m_state != State::Stopped && m_supervise)
            scheduleRestart(m_process.errorString());
    });
    connect(&m_process,
            qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this,
            [this](int code, QProcess::ExitStatus status) {
        if (m_state == State::Stopped || !m_supervise) return;
        const bool interruptedTurn = m_turnInFlight;
        const QString detail = status == QProcess::CrashExit
            ? QStringLiteral("Codex app-server crashed")
            : QStringLiteral("Codex app-server exited with code %1").arg(code);
        m_threadId.clear();
        m_activeTurnId.clear();
        m_initializeId = 0;
        m_modelListId = 0;
        m_threadStartId = 0;
        m_turnStartId = 0;
        m_toolRequestIds.clear();
        m_turnInFlight = false;
        if (interruptedTurn) emit turnFinished(false, detail);
        scheduleRestart(detail);
    });
}

CodexAppServerClient::~CodexAppServerClient()
{
    blockSignals(true);
    stop();
}

void CodexAppServerClient::start(const QString& workingDirectory,
                                 const QJsonArray& dynamicTools,
                                 const QString& developerInstructions)
{
    m_supervise = true;
    m_restartTimer.stop();
    m_workingDirectory = QDir(workingDirectory).absolutePath();
    m_dynamicTools = dynamicTools;
    m_developerInstructions = developerInstructions;
    m_modelListId = 0;
    if (m_process.state() != QProcess::NotRunning) return;
    m_executable = findCodex();
    if (m_executable.isEmpty()) {
        m_supervise = false;
        setState(State::Failed, QStringLiteral("Codex CLI was not found in PATH or ~/.local/bin"));
        return;
    }
    setState(State::Starting, QStringLiteral("Starting Codex App Server"));
    m_process.setProgram(m_executable);
    // Let the installed Codex configuration select its supported default model.
    // Model choice belongs to Codex configuration, not a compiled UI string.
    m_process.setArguments({QStringLiteral("app-server"), QStringLiteral("--stdio")});
    m_process.setWorkingDirectory(m_workingDirectory);
    QProcessEnvironment environment = QProcessEnvironment::systemEnvironment();
    environment.insert(QStringLiteral("NO_COLOR"), QStringLiteral("1"));
    m_process.setProcessEnvironment(environment);
    m_process.start();
}

void CodexAppServerClient::setOptions(const QString& model, const QString& effort,
                                      const QString& serviceTier)
{
    m_selectedModel = model.trimmed();
    m_selectedEffort = effort.trimmed();
    m_selectedServiceTier = serviceTier.trimmed();
}

void CodexAppServerClient::requestModelList()
{
    if (m_process.state() != QProcess::Running) return;
    m_modelListId = sendRequest(QStringLiteral("model/list"), QJsonObject{
        {QStringLiteral("limit"), 100},
        {QStringLiteral("includeHidden"), false}
    });
}

void CodexAppServerClient::submit(const QString& prompt)
{
    submit(prompt, QJsonArray{});
}

void CodexAppServerClient::submit(const QString& prompt, const QJsonArray& inputItems)
{
    if (prompt.trimmed().isEmpty()) return;
    m_pendingPrompts.enqueue(PendingTurn{prompt.trimmed(), inputItems});
    if (m_state == State::Stopped || m_state == State::Failed) {
        start(m_workingDirectory, m_dynamicTools, m_developerInstructions);
        return;
    }
    startNextTurn();
}

void CodexAppServerClient::newThread()
{
    if (m_state == State::Running) interrupt();
    m_threadId.clear();
    m_activeTurnId.clear();
    m_turnInFlight = false;
    if (m_process.state() == QProcess::Running) startThread();
}

void CodexAppServerClient::interrupt()
{
    if (m_threadId.isEmpty() || m_activeTurnId.isEmpty()) return;
    sendRequest(QStringLiteral("turn/interrupt"), QJsonObject{
        {QStringLiteral("threadId"), m_threadId},
        {QStringLiteral("turnId"), m_activeTurnId}
    });
}

void CodexAppServerClient::stop()
{
    m_supervise = false;
    m_restartTimer.stop();

    // An explicit user stop is a cancellation boundary.  Do not leave a
    // prompt queued for the next start: that would make a later message
    // appear to "resume" work the user deliberately stopped.  Clear all
    // protocol state before terminating the child so late stdout/finished
    // signals cannot resurrect the thread or schedule a restart.
    m_pendingPrompts.clear();
    m_turnInFlight = false;
    m_threadId.clear();
    m_activeTurnId.clear();
    m_initializeId = 0;
    m_modelListId = 0;
    m_threadStartId = 0;
    m_turnStartId = 0;
    m_toolRequestIds.clear();
    m_stdoutBuffer.clear();
    m_restartAttempts = 0;

    // Set the state before terminating.  QProcess can emit finished/error
    // synchronously while terminate/kill is being called; those callbacks
    // must observe the explicit-stop state and never invoke supervision.
    if (m_state != State::Stopped)
        setState(State::Stopped, QStringLiteral("Stopped by user"));

    if (m_process.state() == QProcess::NotRunning) return;
    m_process.terminate();
    if (!m_process.waitForFinished(300)) {
        m_process.kill();
        m_process.waitForFinished(1000);
    }
}

void CodexAppServerClient::completeToolCall(const QString& token, bool success,
                                            const QJsonArray& contentItems)
{
    if (!m_toolRequestIds.contains(token)) return;
    const QJsonValue id = m_toolRequestIds.take(token);
    sendObject(QJsonObject{
        {QStringLiteral("jsonrpc"), QStringLiteral("2.0")},
        {QStringLiteral("id"), id},
        {QStringLiteral("result"), QJsonObject{
            {QStringLiteral("success"), success},
            {QStringLiteral("contentItems"), contentItems}}}
    });
}

void CodexAppServerClient::completeToolCall(const QString& token, bool success,
                                            const QString& text)
{
    completeToolCall(token, success, QJsonArray{QJsonObject{
        {QStringLiteral("type"), QStringLiteral("inputText")},
        {QStringLiteral("text"), text}
    }});
}

QString CodexAppServerClient::stateText() const
{
    switch (m_state) {
    case State::Stopped: return QStringLiteral("offline");
    case State::Starting: return QStringLiteral("starting");
    case State::Initializing: return QStringLiteral("initializing");
    case State::Ready: return QStringLiteral("ready");
    case State::Running: return QStringLiteral("running");
    case State::Failed: return QStringLiteral("failed");
    }
    return QStringLiteral("unknown");
}

void CodexAppServerClient::setState(State state, const QString& detail)
{
    m_state = state;
    if (state == State::Failed)
        qWarning().noquote() << "CODEX_STATE: failed:" << detail;
    else
        qInfo().noquote() << "CODEX_STATE:" << stateText() << detail;
    emit stateChanged(state, detail);
}

qint64 CodexAppServerClient::sendRequest(const QString& method, const QJsonObject& params)
{
    const qint64 id = m_nextId++;
    sendObject(QJsonObject{
        {QStringLiteral("jsonrpc"), QStringLiteral("2.0")},
        {QStringLiteral("id"), id},
        {QStringLiteral("method"), method},
        {QStringLiteral("params"), params}
    });
    return id;
}

void CodexAppServerClient::sendNotification(const QString& method, const QJsonObject& params)
{
    sendObject(QJsonObject{
        {QStringLiteral("jsonrpc"), QStringLiteral("2.0")},
        {QStringLiteral("method"), method},
        {QStringLiteral("params"), params}
    });
}

void CodexAppServerClient::sendObject(const QJsonObject& object)
{
    if (m_process.state() != QProcess::Running) return;
    m_process.write(QJsonDocument(object).toJson(QJsonDocument::Compact));
    m_process.write("\n");
}

void CodexAppServerClient::consumeStdout()
{
    m_stdoutBuffer += m_process.readAllStandardOutput();
    for (;;) {
        const qsizetype newline = m_stdoutBuffer.indexOf('\n');
        if (newline < 0) break;
        const QByteArray line = m_stdoutBuffer.left(newline).trimmed();
        m_stdoutBuffer.remove(0, newline + 1);
        if (line.isEmpty()) continue;
        QJsonParseError parseError;
        const QJsonDocument document = QJsonDocument::fromJson(line, &parseError);
        if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
            emit activity(QStringLiteral("Ignored invalid Codex protocol line: %1")
                              .arg(parseError.errorString()));
            continue;
        }
        handleMessage(document.object());
    }
}

void CodexAppServerClient::handleMessage(const QJsonObject& message)
{
    const QString method = message.value(QStringLiteral("method")).toString();
    const QJsonValue id = message.value(QStringLiteral("id"));
    if (!method.isEmpty() && !id.isUndefined()) {
        handleServerRequest(id, method, message.value(QStringLiteral("params")).toObject());
        return;
    }
    if (!method.isEmpty()) {
        handleNotification(method, message.value(QStringLiteral("params")).toObject());
        return;
    }
    if (!id.isDouble()) return;
    handleResponse(qint64(id.toDouble()), message.value(QStringLiteral("result")).toObject(),
                   message.value(QStringLiteral("error")).toObject());
}

void CodexAppServerClient::handleResponse(qint64 id, const QJsonObject& result,
                                          const QJsonObject& error)
{
    if (!error.isEmpty()) {
        const QString detail = errorText(error);
        if (id == m_initializeId || id == m_threadStartId) {
            scheduleRestart(detail);
        } else {
            emit activity(detail);
            if (id == m_turnStartId) {
                m_turnInFlight = false;
                emit turnFinished(false, detail);
            }
        }
        return;
    }
    if (id == m_initializeId) {
        sendNotification(QStringLiteral("initialized"));
        requestModelList();
        startThread();
    } else if (id == m_modelListId) {
        emit modelsAvailable(result.value(QStringLiteral("data")).toArray());
    } else if (id == m_threadStartId) {
        m_threadId = result.value(QStringLiteral("thread")).toObject()
                         .value(QStringLiteral("id")).toString();
        if (m_threadId.isEmpty()) {
            scheduleRestart(QStringLiteral("Codex did not return a thread id"));
            return;
        }
        m_restartAttempts = 0;
        setState(State::Ready, QStringLiteral("Codex connected"));
        startNextTurn();
    } else if (id == m_turnStartId) {
        m_activeTurnId = result.value(QStringLiteral("turn")).toObject()
                             .value(QStringLiteral("id")).toString();
        setState(State::Running, QStringLiteral("Codex is designing"));
    }
}

void CodexAppServerClient::handleNotification(const QString& method,
                                               const QJsonObject& params)
{
    if (method == QStringLiteral("item/agentMessage/delta")) {
        emit assistantDelta(params.value(QStringLiteral("delta")).toString());
    } else if (method == QStringLiteral("item/reasoning/summaryTextDelta")) {
        emit reasoningDelta(params.value(QStringLiteral("delta")).toString());
    } else if (method == QStringLiteral("turn/completed")) {
        const QJsonObject turn = params.value(QStringLiteral("turn")).toObject();
        const QString status = statusText(turn.value(QStringLiteral("status")));
        const bool ok = status == QStringLiteral("completed") || status == QStringLiteral("succeeded");
        m_activeTurnId.clear();
        m_turnInFlight = false;
        setState(State::Ready, ok ? QStringLiteral("Codex turn complete") : status);
        emit turnFinished(ok, status);
        startNextTurn();
    } else if (method == QStringLiteral("error")) {
        emit activity(params.value(QStringLiteral("message")).toString());
    } else if (method == QStringLiteral("item/mcpToolCall/progress")) {
        emit activity(params.value(QStringLiteral("message")).toString());
    } else if (method == QStringLiteral("mcpServer/startupStatus/updated")) {
        emit activity(QStringLiteral("Codex tool services updated"));
    }
}

void CodexAppServerClient::handleServerRequest(const QJsonValue& id,
                                               const QString& method,
                                               const QJsonObject& params)
{
    if (method != QStringLiteral("item/tool/call")) {
        sendObject(QJsonObject{
            {QStringLiteral("jsonrpc"), QStringLiteral("2.0")},
            {QStringLiteral("id"), id},
            {QStringLiteral("error"), QJsonObject{
                {QStringLiteral("code"), -32601},
                {QStringLiteral("message"), QStringLiteral("unsupported server request")}}}
        });
        return;
    }
    const QString token = QUuid::createUuid().toString(QUuid::WithoutBraces);
    m_toolRequestIds.insert(token, id);
    const QJsonValue argumentsValue = params.value(QStringLiteral("arguments"));
    const QJsonObject arguments = argumentsValue.isObject() ? argumentsValue.toObject()
                                                              : QJsonObject{};
    const QString tool = params.value(QStringLiteral("tool")).toString();
    qInfo().noquote() << "CODEX_TOOL_REQUEST:" << tool;
    emit toolCallRequested(token, tool, arguments);
}

void CodexAppServerClient::startThread()
{
    if (m_process.state() != QProcess::Running) return;
    QJsonObject params{
        {QStringLiteral("cwd"), m_workingDirectory},
        {QStringLiteral("runtimeWorkspaceRoots"), QJsonArray{m_workingDirectory}},
        {QStringLiteral("sandbox"), QStringLiteral("read-only")},
        {QStringLiteral("approvalPolicy"), QStringLiteral("never")},
        {QStringLiteral("approvalsReviewer"), QStringLiteral("user")},
        {QStringLiteral("developerInstructions"), m_developerInstructions},
        {QStringLiteral("dynamicTools"), m_dynamicTools},
        {QStringLiteral("ephemeral"), false},
        {QStringLiteral("serviceName"), QStringLiteral("DesignStudio")}
    };
    applySelectedOptions(params, true);
    m_threadStartId = sendRequest(QStringLiteral("thread/start"), params);
}

void CodexAppServerClient::startNextTurn()
{
    if (m_state != State::Ready || m_threadId.isEmpty() || m_pendingPrompts.isEmpty()) return;
    const PendingTurn pending = m_pendingPrompts.dequeue();
    QJsonArray input{QJsonObject{
        {QStringLiteral("type"), QStringLiteral("text")},
        {QStringLiteral("text"), pending.prompt}}};
    for (const QJsonValue& item : pending.inputItems)
        input.append(item);
    QJsonObject params{
        {QStringLiteral("threadId"), m_threadId},
        {QStringLiteral("input"), input},
        {QStringLiteral("approvalPolicy"), QStringLiteral("never")}
    };
    applySelectedOptions(params, false);
    m_turnStartId = sendRequest(QStringLiteral("turn/start"), params);
    m_turnInFlight = true;
}

void CodexAppServerClient::scheduleRestart(const QString& reason)
{
    if (!m_supervise || m_workingDirectory.isEmpty()) return;
    if (m_restartTimer.isActive()) return;

    // A server restart invalidates the thread and any outstanding host-side
    // tool-call ids. Never replay an in-flight mutating turn automatically:
    // the tool may have committed before the transport died. Queued turns
    // remain queued and are sent only after a fresh thread is ready.
    m_threadId.clear();
    m_activeTurnId.clear();
    m_toolRequestIds.clear();
    m_stdoutBuffer.clear();

    ++m_restartAttempts;
    const int exponent = qMin(m_restartAttempts - 1, 6);
    const int delayMs = qMin(30000, 500 * (1 << exponent));
    setState(State::Starting,
             QStringLiteral("%1; restarting Codex app-server in %2 ms (attempt %3)")
                 .arg(reason).arg(delayMs).arg(m_restartAttempts));
    m_restartTimer.start(delayMs);

    // Protocol errors can arrive while the child is still technically
    // running. Close that transport now; otherwise the timer would see a
    // running process and silently leave the client in a nonfunctional
    // Starting state.
    if (m_process.state() != QProcess::NotRunning) {
        m_process.terminate();
        if (!m_process.waitForFinished(500)) m_process.kill();
    }
}

void CodexAppServerClient::applySelectedOptions(QJsonObject& params,
                                                bool threadStart) const
{
    if (!m_selectedModel.isEmpty())
        params.insert(QStringLiteral("model"), m_selectedModel);
    if (!m_selectedServiceTier.isEmpty())
        params.insert(QStringLiteral("serviceTier"), m_selectedServiceTier);
    if (!m_selectedEffort.isEmpty()) {
        if (threadStart) {
            params.insert(QStringLiteral("config"), QJsonObject{
                {QStringLiteral("model_reasoning_effort"), m_selectedEffort}});
        } else {
            params.insert(QStringLiteral("effort"), m_selectedEffort);
        }
    }
}

QString CodexAppServerClient::findCodex()
{
    const QString explicitPath = qEnvironmentVariable("DESIGNSTUDIO_CODEX");
    if (!explicitPath.isEmpty() && QFileInfo(explicitPath).isExecutable()) return explicitPath;
    const QString found = QStandardPaths::findExecutable(QStringLiteral("codex"));
    if (!found.isEmpty()) return found;
    const QString local = QDir::home().filePath(QStringLiteral(".local/bin/codex"));
    return QFileInfo(local).isExecutable() ? local : QString{};
}

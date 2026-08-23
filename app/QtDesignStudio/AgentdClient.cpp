#include "AgentdClient.h"

#include <QCoreApplication>
#include <QDebug>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QProcessEnvironment>
#include <QStandardPaths>
#include <QTimer>

namespace {
bool belowRoot(const QString& root, const QString& candidate) {
    const QString cleanRoot = QDir::cleanPath(root);
    const QString cleanCandidate = QDir::cleanPath(candidate);
    return cleanCandidate == cleanRoot
        || cleanCandidate.startsWith(cleanRoot + QDir::separator());
}

QString rpcError(const QJsonObject& error) {
    const QString message = error.value(QStringLiteral("message")).toString();
    const QJsonValue data = error.value(QStringLiteral("data"));
    if (data.isString() && !data.toString().isEmpty())
        return message + QStringLiteral(": ") + data.toString();
    return message.isEmpty() ? QStringLiteral("control-plane request failed") : message;
}
} // namespace

AgentdClient::AgentdClient(QObject* parent) : QObject(parent) {
    m_process.setProcessChannelMode(QProcess::SeparateChannels);
    connect(&m_process, &QProcess::started, this, [this] {
        sendProjectOpen(m_recoveryOpen);
    });
    connect(&m_process, &QProcess::readyReadStandardOutput,
            this, &AgentdClient::consumeStdout);
    connect(&m_process, &QProcess::readyReadStandardError, this, [this] {
        const QString detail = QString::fromUtf8(m_process.readAllStandardError()).trimmed();
        if (!detail.isEmpty()) qWarning().noquote() << "AGENTD_STDERR:" << detail;
        if (!detail.isEmpty() && m_state != State::Ready)
            emit stateChanged(m_state, detail);
    });
    connect(&m_process, &QProcess::errorOccurred, this,
            [this](QProcess::ProcessError error) {
        if (m_stopping || error == QProcess::Crashed) return;
        scheduleRestart(m_process.errorString());
    });
    connect(&m_process,
            qOverload<int, QProcess::ExitStatus>(&QProcess::finished),
            this, [this](int exitCode, QProcess::ExitStatus status) {
        if (m_stopping) return;
        const QString reason = status == QProcess::CrashExit
            ? QStringLiteral("control plane crashed")
            : QStringLiteral("control plane exited with code %1").arg(exitCode);
        scheduleRestart(reason);
    });
}

AgentdClient::~AgentdClient() {
    blockSignals(true);
    stop();
}

QString AgentdClient::executablePath() { return findExecutable(); }

bool AgentdClient::resolveWorkspace(const QString& path,
                                    WorkspaceDocuments* documents,
                                    QString* errorMessage) {
    QFileInfo input(path);
    QString manifestPath;
    if (input.isDir())
        manifestPath = QDir(input.absoluteFilePath()).filePath(QStringLiteral("manifest.json"));
    else
        manifestPath = input.absoluteFilePath();

    QFileInfo manifestInfo(manifestPath);
    const QString canonicalManifest = manifestInfo.canonicalFilePath();
    if (canonicalManifest.isEmpty() || !manifestInfo.isFile()) {
        if (errorMessage) *errorMessage = QStringLiteral("workspace manifest does not exist: %1")
            .arg(manifestPath);
        return false;
    }

    QFile file(canonicalManifest);
    if (!file.open(QIODevice::ReadOnly)) {
        if (errorMessage) *errorMessage = file.errorString();
        return false;
    }
    QJsonParseError parseError;
    const QJsonDocument parsed = QJsonDocument::fromJson(file.readAll(), &parseError);
    if (parseError.error != QJsonParseError::NoError || !parsed.isObject()) {
        if (errorMessage) *errorMessage = QStringLiteral("invalid workspace JSON: %1")
            .arg(parseError.errorString());
        return false;
    }
    const QJsonObject manifest = parsed.object();
    const QJsonObject documentPaths = manifest.value(QStringLiteral("documents")).toObject();
    const QString mechanical = documentPaths.value(QStringLiteral("mechanical")).toString();
    const QString electronics = documentPaths.value(QStringLiteral("electronics")).toString();
    if (manifest.value(QStringLiteral("schema")).toString()
            != QStringLiteral("design-studio.workspace/1")
        || manifest.value(QStringLiteral("workspace_id")).toString().isEmpty()
        || manifest.value(QStringLiteral("revision")).toInteger() < 1
        || mechanical.isEmpty() || electronics.isEmpty()) {
        if (errorMessage) *errorMessage = QStringLiteral(
            "unsupported or incomplete design-studio.workspace/1 manifest");
        return false;
    }

    const QString root = manifestInfo.absoluteDir().canonicalPath();
    auto resolveDocument = [&](const QString& relative, const QString& label) -> QString {
        if (QDir::isAbsolutePath(relative)) {
            if (errorMessage) *errorMessage = label + QStringLiteral(" path must be relative");
            return {};
        }
        const QString absolute = QFileInfo(QDir(root).filePath(relative)).canonicalFilePath();
        if (absolute.isEmpty() || !belowRoot(root, absolute)) {
            if (errorMessage) *errorMessage = label
                + QStringLiteral(" path is missing or escapes the workspace");
            return {};
        }
        return absolute;
    };
    const QString mechanicalPath = resolveDocument(mechanical, QStringLiteral("mechanical"));
    if (mechanicalPath.isEmpty()) return false;
    const QString electronicsPath = resolveDocument(electronics, QStringLiteral("electronics"));
    if (electronicsPath.isEmpty()) return false;

    if (documents) {
        documents->manifestPath = canonicalManifest;
        documents->rootPath = root;
        documents->mechanicalPath = mechanicalPath;
        documents->electronicsPath = electronicsPath;
        documents->workspaceId = manifest.value(QStringLiteral("workspace_id")).toString();
        documents->workspaceRevision = manifest.value(QStringLiteral("revision")).toInteger();
    }
    if (errorMessage) errorMessage->clear();
    return true;
}

bool AgentdClient::openWorkspace(const QString& path, QString* errorMessage) {
    WorkspaceDocuments documents;
    if (!resolveWorkspace(path, &documents, errorMessage)) return false;

    stop();
    m_workspace = documents;
    m_executable = findExecutable();
    m_restartAttempts = 0;
    m_graphRevision = 0;
    m_daemonRevision = 0;
    if (m_executable.isEmpty()) {
        setState(State::Failed, QStringLiteral(
            "designstudio-agentd was not found; set DESIGNSTUDIO_AGENTD or install the control plane"));
        if (errorMessage) *errorMessage = QStringLiteral("designstudio-agentd executable not found");
        return false;
    }
    startProcess(false);
    if (errorMessage) errorMessage->clear();
    return true;
}

qint64 AgentdClient::invoke(const QString& method,
                            const QJsonObject& params,
                            const QString& permission) {
    if (m_state != State::Ready) {
        emit requestFailed(method, QStringLiteral("local control plane is not ready"));
        return 0;
    }
    return sendRequest(method, params, permission);
}

void AgentdClient::stop() {
    m_stopping = true;
    m_pending.clear();
    m_stdoutBuffer.clear();
    if (m_process.state() != QProcess::NotRunning) {
        m_process.terminate();
        if (!m_process.waitForFinished(1500)) {
            m_process.kill();
            m_process.waitForFinished(1500);
        }
    }
    m_stopping = false;
    if (m_state != State::Stopped) setState(State::Stopped, QStringLiteral("stopped"));
}

QString AgentdClient::stateText() const {
    switch (m_state) {
    case State::Stopped: return QStringLiteral("offline");
    case State::Starting: return QStringLiteral("starting");
    case State::Recovering: return QStringLiteral("recovering");
    case State::Ready: return QStringLiteral("ready");
    case State::Failed: return QStringLiteral("failed");
    }
    return QStringLiteral("unknown");
}

void AgentdClient::setState(State state, const QString& detail) {
    m_state = state;
    if (state == State::Failed)
        qWarning().noquote() << "AGENTD_STATE: failed:" << detail;
    else
        qInfo().noquote() << "AGENTD_STATE:" << stateText() << detail;
    emit stateChanged(state, detail);
}

void AgentdClient::startProcess(bool recovering) {
    if (m_process.state() != QProcess::NotRunning || m_stopping) return;
    m_recoveryOpen = recovering;
    m_pending.clear();
    m_stdoutBuffer.clear();
    setState(recovering ? State::Recovering : State::Starting,
             recovering ? QStringLiteral("restarting local control plane")
                        : QStringLiteral("starting local control plane"));
    m_process.setProgram(m_executable);
    m_process.setArguments({QStringLiteral("--stdio")});
    m_process.setProcessEnvironment(QProcessEnvironment::systemEnvironment());
    m_process.start();
}

void AgentdClient::sendProjectOpen(bool recovering) {
    sendRequest(QStringLiteral("project/open"),
                {{QStringLiteral("manifest_path"), m_workspace.manifestPath}},
                QStringLiteral("project:open"), recovering);
}

qint64 AgentdClient::sendRequest(const QString& method,
                                 const QJsonObject& params,
                                 const QString& permission,
                                 bool recoveryOpen) {
    if (m_process.state() != QProcess::Running) return 0;
    const qint64 id = m_nextRequestId++;
    const QJsonObject request{
        {QStringLiteral("jsonrpc"), QStringLiteral("2.0")},
        {QStringLiteral("id"), id},
        {QStringLiteral("method"), method},
        {QStringLiteral("params"), params},
        {QStringLiteral("auth"), QJsonObject{
            {QStringLiteral("permissions"), QJsonArray{permission}}}},
    };
    m_pending.insert(id, {method, recoveryOpen});
    QByteArray bytes = QJsonDocument(request).toJson(QJsonDocument::Compact);
    bytes.append('\n');
    if (m_process.write(bytes) != bytes.size()) {
        m_pending.remove(id);
        scheduleRestart(QStringLiteral("could not write to local control plane"));
        return 0;
    }
    return id;
}

void AgentdClient::consumeStdout() {
    m_stdoutBuffer += m_process.readAllStandardOutput();
    qsizetype newline = -1;
    while ((newline = m_stdoutBuffer.indexOf('\n')) >= 0) {
        const QByteArray line = m_stdoutBuffer.left(newline).trimmed();
        m_stdoutBuffer.remove(0, newline + 1);
        if (line.isEmpty()) continue;
        QJsonParseError error;
        const QJsonDocument document = QJsonDocument::fromJson(line, &error);
        if (error.error != QJsonParseError::NoError || !document.isObject()) {
            scheduleRestart(QStringLiteral("control plane emitted invalid JSON-RPC"));
            return;
        }
        handleResponse(document.object());
    }
}

void AgentdClient::handleResponse(const QJsonObject& response) {
    const qint64 id = response.value(QStringLiteral("id")).toInteger();
    if (!m_pending.contains(id)) return;
    const PendingRequest pending = m_pending.take(id);
    if (response.value(QStringLiteral("error")).isObject()) {
        const QString message = rpcError(response.value(QStringLiteral("error")).toObject());
        // A typed operation failure (for example an unavailable optional
        // solver or an older daemon that does not know a newly introduced
        // method) must not tear down an otherwise healthy workspace session.
        // Only project/open and transport/protocol failures invalidate the
        // control-plane state; callers receive the operation error directly.
        if (pending.method == QStringLiteral("project/open"))
            setState(State::Failed, message);
        emit requestFailed(pending.method, message);
        return;
    }
    const QJsonObject result = response.value(QStringLiteral("result")).toObject();
    if (pending.method != QStringLiteral("project/open")) {
        emit responseReceived(pending.method, result);
        return;
    }

    const QString api = result.value(QStringLiteral("api")).toString();
    const QString workspaceId = result.value(QStringLiteral("workspace_id")).toString();
    const qint64 workspaceRevision = result.value(QStringLiteral("workspace_revision")).toInteger();
    m_graphRevision = result.value(QStringLiteral("graph_revision")).toInteger();
    m_daemonRevision = result.value(QStringLiteral("daemon_revision")).toInteger();
    if (api != QStringLiteral("design-studio.agentd/1")
        || workspaceId != m_workspace.workspaceId
        || workspaceRevision != m_workspace.workspaceRevision
        || m_graphRevision < 1 || m_daemonRevision < 1) {
        const QString message = QStringLiteral(
            "desktop and control-plane workspace revisions do not match");
        setState(State::Failed, message);
        emit requestFailed(pending.method, message);
        return;
    }
    const bool recovered = pending.recoveryOpen;
    m_restartAttempts = 0;
    setState(State::Ready, recovered
        ? QStringLiteral("control plane recovered and workspace re-opened")
        : QStringLiteral("workspace revisions synchronized"));
    emit workspaceOpened(workspaceId, workspaceRevision, m_graphRevision,
                         m_daemonRevision, recovered);
}

void AgentdClient::scheduleRestart(const QString& reason) {
    if (m_stopping || m_workspace.manifestPath.isEmpty()) return;
    if (m_process.state() != QProcess::NotRunning) {
        m_stopping = true;
        m_process.kill();
        m_process.waitForFinished(500);
        m_stopping = false;
    }
    m_pending.clear();
    if (++m_restartAttempts > 3) {
        setState(State::Failed, reason + QStringLiteral("; restart budget exhausted"));
        return;
    }
    setState(State::Recovering,
             reason + QStringLiteral("; retry %1/3").arg(m_restartAttempts));
    QTimer::singleShot(100 * m_restartAttempts, this, [this] {
        startProcess(true);
    });
}

QString AgentdClient::findExecutable() {
    const QString override = qEnvironmentVariable("DESIGNSTUDIO_AGENTD");
    if (!override.isEmpty() && QFileInfo(override).isExecutable())
        return QFileInfo(override).absoluteFilePath();
    const QString adjacent = QDir(QCoreApplication::applicationDirPath())
        .filePath(QStringLiteral("designstudio-agentd"));
    if (QFileInfo(adjacent).isExecutable()) return adjacent;
    const QString libexec = QDir(QCoreApplication::applicationDirPath())
        .filePath(QStringLiteral("../libexec/designstudio-agentd"));
    if (QFileInfo(libexec).isExecutable()) return QFileInfo(libexec).absoluteFilePath();
    return QStandardPaths::findExecutable(QStringLiteral("designstudio-agentd"));
}

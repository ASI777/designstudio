#include "BuildMapPanel.h"

#include "AgentdClient.h"

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDir>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFont>
#include <QFrame>
#include <QHash>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QLabel>
#include <QLineEdit>
#include <QPushButton>
#include <QScrollArea>
#include <QTimer>
#include <QVBoxLayout>

namespace {
constexpr auto kProjectPath =
    "acceptance/agent-workflow-controller/checkpoints/pre-v72-authoritative/"
    "agent-workflow-controller.dsproj";
constexpr auto kReportPath =
    "acceptance/agent-workflow-controller/checkpoints/pre-v72-authoritative/"
    "acceptance-report.json";

QString digestFile(const QString& path, QString* errorMessage) {
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        if (errorMessage)
            *errorMessage = QStringLiteral("Cannot read %1: %2").arg(path, file.errorString());
        return {};
    }
    return QString::fromLatin1(
        QCryptographicHash::hash(file.readAll(), QCryptographicHash::Sha256).toHex());
}

QString sourceCandidate(const QString& start) {
    const QString canonical = QFileInfo(start).canonicalFilePath();
    if (canonical.isEmpty()) return {};
    QDir directory(canonical);
    if (!QFileInfo(canonical).isDir()) directory = QFileInfo(canonical).absoluteDir();
    while (true) {
        if (QFileInfo(directory.filePath(QString::fromLatin1(kProjectPath))).isFile()
            && QFileInfo(directory.filePath(QString::fromLatin1(kReportPath))).isFile()) {
            return directory.canonicalPath();
        }
        if (!directory.cdUp()) break;
    }
    return {};
}

QStringList jsonStrings(const QJsonArray& values) {
    QStringList result;
    for (const QJsonValue& value : values) result.append(value.toString());
    return result;
}

QString checkpointColor(const QString& status) {
    if (status == QStringLiteral("succeeded")) return QStringLiteral("#3fb950");
    if (status == QStringLiteral("running")) return QStringLiteral("#58a6ff");
    if (status == QStringLiteral("rejected_over_budget")) return QStringLiteral("#f85149");
    if (status == QStringLiteral("cancelled")) return QStringLiteral("#d29922");
    return QStringLiteral("#8b949e");
}
} // namespace

BuildMapPanel::BuildMapPanel(QWidget* parent) : QWidget(parent) {
    setObjectName(QStringLiteral("build_map_panel"));
    auto* root = new QVBoxLayout(this);
    root->setContentsMargins(28, 24, 28, 24);
    root->setSpacing(12);

    auto* title = new QLabel(QStringLiteral("Hardware Build Map"), this);
    QFont titleFont = title->font();
    titleFont.setPointSize(titleFont.pointSize() + 5);
    titleFont.setBold(true);
    title->setFont(titleFont);
    root->addWidget(title);

    auto* description = new QLabel(
        QStringLiteral("Compile one approved product intent into component evidence, "
                       "schematic, PCB, FreeCAD, integration, and verification work. "
                       "Every package is digest-bound and independently resumable."),
        this);
    description->setWordWrap(true);
    root->addWidget(description);

    auto* sourceRow = new QHBoxLayout;
    sourceRow->addWidget(new QLabel(QStringLiteral("Evidence root"), this));
    m_sourceRoot = new QLineEdit(this);
    m_sourceRoot->setObjectName(QStringLiteral("build_map_source_root"));
    m_sourceRoot->setPlaceholderText(
        QStringLiteral("Repository containing acceptance/agent-workflow-controller"));
    sourceRow->addWidget(m_sourceRoot, 1);
    auto* browse = new QPushButton(QStringLiteral("Browse…"), this);
    sourceRow->addWidget(browse);
    root->addLayout(sourceRow);

    m_state = new QLabel(
        QStringLiteral("Open a product workspace, then compile the local build map."), this);
    m_state->setObjectName(QStringLiteral("build_map_state"));
    m_state->setWordWrap(true);
    root->addWidget(m_state);
    m_budget = new QLabel(QStringLiteral("Budget: 72 units · no work charged"), this);
    m_budget->setObjectName(QStringLiteral("build_map_budget"));
    root->addWidget(m_budget);

    auto* scroll = new QScrollArea(this);
    scroll->setWidgetResizable(true);
    auto* packageHost = new QWidget(scroll);
    m_packages = new QVBoxLayout(packageHost);
    m_packages->setSpacing(8);
    m_packages->addStretch(1);
    scroll->setWidget(packageHost);
    root->addWidget(scroll, 1);

    auto* actions = new QHBoxLayout;
    m_compile = new QPushButton(QStringLiteral("Compile Build Map"), this);
    m_approve = new QPushButton(QStringLiteral("Approve Architecture"), this);
    m_runNext = new QPushButton(QStringLiteral("Run Next Package"), this);
    m_runAll = new QPushButton(QStringLiteral("Run All Local Packages"), this);
    m_cancel = new QPushButton(QStringLiteral("Cancel"), this);
    m_compile->setObjectName(QStringLiteral("build_map_compile"));
    m_approve->setObjectName(QStringLiteral("build_map_approve"));
    m_runNext->setObjectName(QStringLiteral("build_map_run_next"));
    m_runAll->setObjectName(QStringLiteral("build_map_run_all"));
    m_cancel->setObjectName(QStringLiteral("build_map_cancel"));
    actions->addWidget(m_compile);
    actions->addWidget(m_approve);
    actions->addWidget(m_runNext);
    actions->addWidget(m_runAll);
    actions->addWidget(m_cancel);
    actions->addStretch(1);
    root->addLayout(actions);

    m_gpuGate = new QLabel(
        QStringLiteral("GPU phase locked · no Droplet configured. Local evidence and "
                       "architecture approval must pass first."),
        this);
    m_gpuGate->setObjectName(QStringLiteral("build_map_gpu_gate"));
    m_gpuGate->setStyleSheet(QStringLiteral(
        "color:#d29922;padding:8px;border:1px solid #6e550f;border-radius:4px;"));
    m_gpuGate->setWordWrap(true);
    root->addWidget(m_gpuGate);

    connect(browse, &QPushButton::clicked, this, [this] {
        const QString path = QFileDialog::getExistingDirectory(
            this, QStringLiteral("Select DesignStudio evidence repository"),
            m_sourceRoot->text());
        if (!path.isEmpty()) m_sourceRoot->setText(QFileInfo(path).canonicalFilePath());
        updateActions();
    });
    connect(m_sourceRoot, &QLineEdit::textChanged, this, [this] { updateActions(); });
    connect(m_compile, &QPushButton::clicked, this, &BuildMapPanel::compileBuildMap);
    connect(m_approve, &QPushButton::clicked, this, &BuildMapPanel::approveArchitecture);
    connect(m_runNext, &QPushButton::clicked, this, [this] {
        m_runAllRequested = false;
        advanceOne();
    });
    connect(m_runAll, &QPushButton::clicked, this, [this] {
        m_runAllRequested = true;
        advanceOne();
    });
    connect(m_cancel, &QPushButton::clicked, this, &BuildMapPanel::cancelWorkflow);
    updateActions();
}

void BuildMapPanel::setClient(AgentdClient* client) {
    if (m_client == client) return;
    if (m_client) disconnect(m_client, nullptr, this, nullptr);
    m_client = client;
    if (!m_client) {
        updateActions();
        return;
    }
    connect(m_client, &AgentdClient::workspaceOpened, this,
            [this](const QString&, qint64, qint64, qint64, bool recovered) {
        discoverSourceRoot();
        if (recovered && !m_workflowId.isEmpty()) {
            m_client->invoke(QStringLiteral("workflow/read"),
                             {{QStringLiteral("workflow_id"), m_workflowId}},
                             QStringLiteral("workflow:read"));
        } else {
            clearWorkflow();
            m_state->setText(
                QStringLiteral("Workspace ready · compile the local hardware build map."));
        }
        updateActions();
    });
    connect(m_client, &AgentdClient::stateChanged, this,
            [this](AgentdClient::State, const QString&) { updateActions(); });
    connect(m_client, &AgentdClient::responseReceived,
            this, &BuildMapPanel::handleResponse);
    connect(m_client, &AgentdClient::requestFailed, this,
            [this](const QString& method, const QString& message) {
        if (!method.startsWith(QStringLiteral("workflow/"))
            && method != QStringLiteral("product/compile")) {
            return;
        }
        m_runAllRequested = false;
        m_state->setText(QStringLiteral("%1 failed · %2").arg(method, message));
        m_state->setStyleSheet(QStringLiteral("color:#f85149;"));
        emit statusMessage(m_state->text());
        updateActions();
    });
    discoverSourceRoot();
    updateActions();
}

void BuildMapPanel::discoverSourceRoot() {
    if (!sourceCandidate(m_sourceRoot->text()).isEmpty()) return;
    const QStringList candidates{
        qEnvironmentVariable("DESIGNSTUDIO_SOURCE_ROOT"),
        m_client ? m_client->workspace().rootPath : QString(),
        QDir::currentPath(),
        QCoreApplication::applicationDirPath(),
    };
    for (const QString& candidate : candidates) {
        const QString found = sourceCandidate(candidate);
        if (!found.isEmpty()) {
            m_sourceRoot->setText(found);
            return;
        }
    }
}

bool BuildMapPanel::createIntent(QJsonObject* intent, QString* errorMessage) const {
    const QString root = QFileInfo(m_sourceRoot->text()).canonicalFilePath();
    if (root.isEmpty() || !QFileInfo(root).isDir()) {
        if (errorMessage) *errorMessage = QStringLiteral("Select a valid evidence repository.");
        return false;
    }
    const QString project = QDir(root).filePath(QString::fromLatin1(kProjectPath));
    const QString report = QDir(root).filePath(QString::fromLatin1(kReportPath));
    const QString projectDigest = digestFile(project, errorMessage);
    if (projectDigest.isEmpty()) return false;
    const QString reportDigest = digestFile(report, errorMessage);
    if (reportDigest.isEmpty()) return false;

    *intent = QJsonObject{
        {QStringLiteral("schema"), QStringLiteral("design-studio.product-intent/1")},
        {QStringLiteral("product_id"), QStringLiteral("desktop-ai-control-console")},
        {QStringLiteral("application_family"), QStringLiteral("desktop_ai_control_console")},
        {QStringLiteral("name"), QStringLiteral("USB-C plus BLE desktop AI control console")},
        {QStringLiteral("source_product"), QJsonObject{
             {QStringLiteral("product"), QStringLiteral("acceptance/agent-workflow-controller")},
             {QStringLiteral("source_root"), root},
             {QStringLiteral("project_path"), QString::fromLatin1(kProjectPath)},
             {QStringLiteral("project_digest"), projectDigest},
             {QStringLiteral("acceptance_report_path"), QString::fromLatin1(kReportPath)},
             {QStringLiteral("acceptance_report_digest"), reportDigest}}},
        {QStringLiteral("requirements"), QJsonObject{
             {QStringLiteral("usb_c_connector_mpn"), QStringLiteral("USB4085-GF-A")},
             {QStringLiteral("ble_mcu_module_mpn"),
              QStringLiteral("ESP32-S3-WROOM-1-N8R8")},
             {QStringLiteral("host_transport"), QStringLiteral("usb_c_usb_2")},
             {QStringLiteral("wireless_transport"), QStringLiteral("ble_5")},
             {QStringLiteral("local_backend"), true},
             {QStringLiteral("gpu_allowed"), false},
             {QStringLiteral("architecture_approval_required"), true}}},
        {QStringLiteral("budget_units"), 72},
    };
    if (errorMessage) errorMessage->clear();
    return true;
}

void BuildMapPanel::compileBuildMap() {
    if (!m_client) return;
    QJsonObject intent;
    QString error;
    if (!createIntent(&intent, &error)) {
        m_state->setText(error);
        m_state->setStyleSheet(QStringLiteral("color:#f85149;"));
        emit statusMessage(error);
        return;
    }
    m_runAllRequested = false;
    m_state->setStyleSheet({});
    m_state->setText(QStringLiteral("Compiling deterministic local work packages…"));
    m_client->invoke(QStringLiteral("product/compile"),
                     {{QStringLiteral("intent"), intent}},
                     QStringLiteral("workflow:compile"));
}

void BuildMapPanel::approveArchitecture() {
    if (!m_client || m_workflowId.isEmpty()) return;
    m_client->invoke(
        QStringLiteral("workflow/approve"),
        {{QStringLiteral("workflow_id"), m_workflowId},
         {QStringLiteral("product_digest"), m_productDigest},
         {QStringLiteral("graph_digest"), m_graphDigest},
         {QStringLiteral("approver"), QStringLiteral("local-product-owner")}},
        QStringLiteral("workflow:approve"));
}

void BuildMapPanel::advanceOne() {
    if (!m_client || m_workflowId.isEmpty() || m_approvalDigest.isEmpty()) {
        m_runAllRequested = false;
        return;
    }
    const QString status = m_workflow.value(QStringLiteral("status")).toString();
    const QString method = status == QStringLiteral("approved")
        ? QStringLiteral("workflow/start") : QStringLiteral("workflow/resume");
    m_state->setText(QStringLiteral("Running the next durable local package…"));
    m_client->invoke(
        method,
        {{QStringLiteral("workflow_id"), m_workflowId},
         {QStringLiteral("product_digest"), m_productDigest},
         {QStringLiteral("graph_digest"), m_graphDigest},
         {QStringLiteral("approval_digest"), m_approvalDigest},
         {QStringLiteral("package_limit"), 1}},
        QStringLiteral("workflow:execute"));
}

void BuildMapPanel::cancelWorkflow() {
    if (!m_client || m_workflowId.isEmpty()) return;
    m_runAllRequested = false;
    m_client->invoke(
        QStringLiteral("workflow/cancel"),
        {{QStringLiteral("workflow_id"), m_workflowId},
         {QStringLiteral("product_digest"), m_productDigest},
         {QStringLiteral("graph_digest"), m_graphDigest}},
        QStringLiteral("workflow:execute"));
}

void BuildMapPanel::handleResponse(const QString& method, const QJsonObject& result) {
    if (method == QStringLiteral("product/compile")) {
        m_productDigest = result.value(QStringLiteral("product_digest")).toString();
        m_graph = result.value(QStringLiteral("work_package_graph")).toObject();
        m_graphDigest = m_graph.value(QStringLiteral("graph_digest")).toString();
        m_workflow = result.value(QStringLiteral("workflow")).toObject();
        m_workflowId = m_workflow.value(QStringLiteral("workflow_id")).toString();
    } else if (method == QStringLiteral("workflow/read")) {
        m_graph = result.value(QStringLiteral("work_package_graph")).toObject();
        m_graphDigest = m_graph.value(QStringLiteral("graph_digest")).toString();
        m_workflow = result.value(QStringLiteral("workflow")).toObject();
        m_workflowId = m_workflow.value(QStringLiteral("workflow_id")).toString();
        m_productDigest = m_workflow.value(QStringLiteral("product_digest")).toString();
    } else if (method.startsWith(QStringLiteral("workflow/"))) {
        m_workflow = result.value(QStringLiteral("workflow")).toObject();
    } else {
        return;
    }

    const QJsonObject approval = m_workflow.value(QStringLiteral("approval")).toObject();
    m_approvalDigest = approval.value(QStringLiteral("approval_digest")).toString();
    rebuildPackages();

    const QString status = m_workflow.value(QStringLiteral("status")).toString();
    QString displayStatus = status;
    displayStatus.replace(QLatin1Char('_'), QLatin1Char(' '));
    m_state->setStyleSheet({});
    m_state->setText(QStringLiteral("Workflow %1 · %2")
                         .arg(m_workflowId, displayStatus));
    emit statusMessage(m_state->text());
    updateActions();

    if (m_runAllRequested && status == QStringLiteral("running")) {
        QTimer::singleShot(0, this, &BuildMapPanel::advanceOne);
    } else if (status != QStringLiteral("running")) {
        m_runAllRequested = false;
    }
}

void BuildMapPanel::rebuildPackages() {
    while (m_packages->count() > 1) {
        QLayoutItem* item = m_packages->takeAt(0);
        delete item->widget();
        delete item;
    }

    QHash<QString, QJsonObject> checkpoints;
    for (const QJsonValue& value :
         m_workflow.value(QStringLiteral("checkpoints")).toArray()) {
        const QJsonObject checkpoint = value.toObject();
        checkpoints.insert(checkpoint.value(QStringLiteral("package_id")).toString(),
                           checkpoint);
    }

    int index = 0;
    for (const QJsonValue& value : m_graph.value(QStringLiteral("packages")).toArray()) {
        const QJsonObject package = value.toObject();
        const QString packageId = package.value(QStringLiteral("id")).toString();
        const QJsonObject checkpoint = checkpoints.value(packageId);
        const QString status = checkpoint.value(QStringLiteral("status"))
                                   .toString(QStringLiteral("blocked"));
        QString displayStatus = status;
        displayStatus.replace(QLatin1Char('_'), QLatin1Char(' '));
        auto* card = new QFrame(this);
        card->setObjectName(QStringLiteral("build_package_%1").arg(packageId));
        card->setFrameShape(QFrame::StyledPanel);
        card->setStyleSheet(QStringLiteral(
            "QFrame{background:#161b22;border:1px solid #30363d;border-radius:6px;}"));
        auto* layout = new QVBoxLayout(card);
        auto* heading = new QHBoxLayout;
        auto* name = new QLabel(
            QStringLiteral("%1. %2").arg(++index).arg(
                package.value(QStringLiteral("name")).toString()),
            card);
        QFont nameFont = name->font();
        nameFont.setBold(true);
        name->setFont(nameFont);
        heading->addWidget(name, 1);
        auto* state = new QLabel(displayStatus, card);
        state->setStyleSheet(QStringLiteral("color:%1;font-weight:600;")
                                .arg(checkpointColor(status)));
        heading->addWidget(state);
        layout->addLayout(heading);

        const QString engine = package.value(QStringLiteral("engine_id")).toString();
        const QStringList dependencies =
            jsonStrings(package.value(QStringLiteral("depends_on")).toArray());
        layout->addWidget(new QLabel(
            QStringLiteral("Engine: %1 · Dependencies: %2 · Cost: %3 units")
                .arg(engine,
                     dependencies.isEmpty() ? QStringLiteral("none")
                                            : dependencies.join(QStringLiteral(", ")))
                .arg(package.value(QStringLiteral("budget_units")).toInteger()),
            card));
        layout->addWidget(new QLabel(
            QStringLiteral("Registered evidence inputs: %1")
                .arg(package.value(QStringLiteral("input_refs")).toArray().size()),
            card));
        const QString output = checkpoint.value(QStringLiteral("output_digest")).toString();
        if (!output.isEmpty()) {
            auto* evidence = new QLabel(
                QStringLiteral("Output %1… · %2")
                    .arg(output.left(16),
                         checkpoint.value(QStringLiteral("result_ref")).toString()),
                card);
            evidence->setTextInteractionFlags(Qt::TextSelectableByMouse);
            layout->addWidget(evidence);
        }
        m_packages->insertWidget(m_packages->count() - 1, card);
    }

    const QJsonObject budget = m_workflow.value(QStringLiteral("budget")).toObject();
    if (!budget.isEmpty()) {
        m_budget->setText(QStringLiteral("Budget: %1 allocated · %2 consumed · %3 remaining")
                              .arg(budget.value(QStringLiteral("allocated_units")).toInteger())
                              .arg(budget.value(QStringLiteral("consumed_units")).toInteger())
                              .arg(budget.value(QStringLiteral("remaining_units")).toInteger()));
    }
}

void BuildMapPanel::updateActions() {
    const bool ready = m_client && m_client->state() == AgentdClient::State::Ready;
    const bool sourceReady = !sourceCandidate(m_sourceRoot->text()).isEmpty();
    const QString status = m_workflow.value(QStringLiteral("status")).toString();
    m_compile->setEnabled(ready && sourceReady);
    m_approve->setEnabled(ready && status == QStringLiteral("awaiting_approval"));
    const bool advanceable = status == QStringLiteral("approved")
        || status == QStringLiteral("running") || status == QStringLiteral("cancelled");
    m_runNext->setEnabled(ready && advanceable && !m_approvalDigest.isEmpty());
    m_runAll->setEnabled(ready && advanceable && !m_approvalDigest.isEmpty());
    m_cancel->setEnabled(ready && (status == QStringLiteral("approved")
                                   || status == QStringLiteral("running")));
    m_runNext->setText(status == QStringLiteral("cancelled")
                           ? QStringLiteral("Resume Next Package")
                           : QStringLiteral("Run Next Package"));
}

void BuildMapPanel::clearWorkflow() {
    m_graph = {};
    m_workflow = {};
    m_productDigest.clear();
    m_graphDigest.clear();
    m_workflowId.clear();
    m_approvalDigest.clear();
    m_runAllRequested = false;
    m_budget->setText(QStringLiteral("Budget: 72 units · no work charged"));
    while (m_packages->count() > 1) {
        QLayoutItem* item = m_packages->takeAt(0);
        delete item->widget();
        delete item;
    }
}

#include "MainWindow.h"
#include "ProjectModel.h"
#include "PcbCanvas.h"
#include "Pcb3DView.h"
#include "SchematicView.h"
#include "LayerPanel.h"
#include "ComponentPanel.h"
#include "DrcPanel.h"
#include "DesignChatPanel.h"
#include "CadAssistPanel.h"
#include "CadAssistProjectBinding.h"
#include "EnclosureConceptTab.h"
#include "CredentialsDialog.h"
#include "CoreBridge.h"
#include "AgentdClient.h"
#include "ApplicationConcept.h"
#include "ProductConfiguratorPanel.h"
#include "PhysicalDesignPanel.h"
#include "PropagationPanel.h"
#include "ProductHomePage.h"
#include "DesignFlowPanel.h"
#include "BuildMapPanel.h"
#include "SemanticSelectionModel.h"
#include "SemanticAssemblyPanel.h"
#include "SupportJobClient.h"

#include <QMenuBar>
#include <QMenu>
#include <QAction>
#include <QActionGroup>
#include <QToolBar>
#include <QStatusBar>
#include <QLabel>
#include <QTabWidget>
#include <QDockWidget>
#include <QFileDialog>
#include <QMessageBox>
#include <QCloseEvent>
#include <QDragEnterEvent>
#include <QDropEvent>
#include <QMimeData>
#include <QSettings>
#include <QApplication>
#include <QDialog>
#include <QFormLayout>
#include <QDoubleSpinBox>
#include <QSpinBox>
#include <QDialogButtonBox>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QPushButton>
#include <QSplitter>
#include <QProgressBar>
#include <QFile>
#include <QDir>
#include <QSaveFile>
#include <QJsonDocument>
#include <QJsonArray>
#include <QDateTime>
#include <QDebug>
#include <QClipboard>
#include <QBuffer>
#include <QCryptographicHash>
#include <QElapsedTimer>
#include <QProcess>
#include <QRegularExpression>
#include <QStandardPaths>
#include <QUuid>
#include <QtConcurrent/QtConcurrentRun>
#include <QFutureWatcher>
#include <QInputDialog>
#include <QTimer>
#include <QIcon>
#include <QPainter>
#include <QPixmap>
#include <algorithm>

namespace {
QIcon codexSidePanelIcon()
{
    QPixmap pixmap(20, 20);
    pixmap.fill(Qt::transparent);
    QPainter painter(&pixmap);
    painter.setRenderHint(QPainter::Antialiasing);
    QPen outline(QColor(QStringLiteral("#C7CBD1")));
    outline.setWidthF(1.4);
    painter.setPen(outline);
    painter.setBrush(Qt::NoBrush);
    painter.drawRoundedRect(QRectF(2.5, 3.5, 15.0, 13.0), 2.2, 2.2);
    painter.drawLine(QPointF(12.0, 4.2), QPointF(12.0, 15.8));
    painter.fillRect(QRectF(13.0, 5.0, 3.1, 10.0), QColor(QStringLiteral("#7F8792")));
    return QIcon(pixmap);
}

QJsonObject toolResult(bool ok, const QString& message,
                       const QJsonObject& data = {}) {
    QJsonObject result{{QStringLiteral("ok"), ok},
                       {QStringLiteral("message"), message}};
    if (!data.isEmpty()) result.insert(QStringLiteral("data"), data);
    return result;
}

QString jsonText(const QJsonObject& object) {
    return QString::fromUtf8(QJsonDocument(object).toJson(QJsonDocument::Compact));
}

QJsonObject invokeAgentdOnce(const QString& method, const QJsonObject& params,
                             const QString& permission) {
    const QString executable = AgentdClient::executablePath();
    if (executable.isEmpty())
        return toolResult(false, QStringLiteral("designstudio-agentd is not installed"));
    QProcess process;
    process.setProgram(executable);
    process.setArguments({QStringLiteral("--stdio")});
    process.start();
    if (!process.waitForStarted(3000))
        return toolResult(false, QStringLiteral("could not start designstudio-agentd: %1")
                                     .arg(process.errorString()));
    const QJsonObject request{
        {QStringLiteral("jsonrpc"), QStringLiteral("2.0")},
        {QStringLiteral("id"), 1},
        {QStringLiteral("method"), method},
        {QStringLiteral("params"), params},
        {QStringLiteral("auth"), QJsonObject{
            {QStringLiteral("permissions"), QJsonArray{permission}}}}
    };
    QByteArray bytes = QJsonDocument(request).toJson(QJsonDocument::Compact);
    bytes.append('\n');
    process.write(bytes);
    process.waitForBytesWritten(2000);
    QByteArray output;
    QElapsedTimer timer;
    timer.start();
    while (!output.contains('\n') && timer.elapsed() < 8000) {
        process.waitForReadyRead(250);
        output += process.readAllStandardOutput();
    }
    process.terminate();
    if (!process.waitForFinished(1000)) process.kill();
    const QByteArray line = output.left(output.indexOf('\n')).trimmed();
    QJsonParseError error;
    const QJsonDocument response = QJsonDocument::fromJson(line, &error);
    if (error.error != QJsonParseError::NoError || !response.isObject())
        return toolResult(false, QStringLiteral("control plane returned invalid JSON: %1")
                                     .arg(error.errorString()));
    const QJsonObject object = response.object();
    if (object.value(QStringLiteral("error")).isObject()) {
        const QJsonObject rpcError = object.value(QStringLiteral("error")).toObject();
        return toolResult(false, rpcError.value(QStringLiteral("message")).toString(
                                     QStringLiteral("control-plane request failed")));
    }
    return toolResult(true, QStringLiteral("control-plane operation complete"),
                      object.value(QStringLiteral("result")).toObject());
}

} // namespace

MainWindow::MainWindow(QWidget* parent, UiProfile profile)
    : QMainWindow(parent), m_uiProfile(profile) {
    setWindowTitle(profile == UiProfile::FreeCadEmbedded
                       ? "DesignStudio Product — FreeCAD"
                       : "DesignStudio");
    setMinimumSize(1200, 750);
    setAcceptDrops(true);

    m_selectionModel = new designstudio::SemanticSelectionModel(this);

    loadStyleSheet();
    setupModel();
    setupViews();
    setupDocks();
    setupMenus();
    setupToolbar();
    setupStatusBar();
    tryLoadCore();
    setupControlPlane();

    // Restore geometry — clear saved state if layout version changed
    // (adding new dock panels changes the layout version)
    QSettings s("DesignStudio","DesignStudio");
    static const int LAYOUT_VERSION = 4;  // v4: validated CAD-assist evidence dock
    if (s.value("layoutVersion").toInt() != LAYOUT_VERSION) {
        s.remove("windowState");
        s.remove("geometry");
        s.setValue("layoutVersion", LAYOUT_VERSION);
    }
    if (m_uiProfile == UiProfile::Standalone) {
        restoreGeometry(s.value("geometry").toByteArray());
        restoreState(s.value("windowState").toByteArray());
    } else {
        // FreeCAD owns the one application menu, status bar and window frame.
        menuBar()->hide();
        statusBar()->hide();
        for (QToolBar* toolbar : findChildren<QToolBar*>()) toolbar->hide();
    }
}

MainWindow::~MainWindow() {
    // Stop and destroy the daemon while every status widget connected to it is
    // still alive. QObject's generic child-deletion order is not a UI lifetime
    // contract and previously allowed a late stateChanged signal to hit a dead
    // QLabel during FreeCAD shutdown.
    if (m_agentd) {
        disconnect(m_agentd, nullptr, this, nullptr);
        m_agentd->blockSignals(true);
        m_agentd->stop();
        delete m_agentd;
        m_agentd = nullptr;
    }

    // Canvases and panels borrow both m_model and m_core.  QMainWindow normally
    // deletes QObject children only after this derived destructor has returned,
    // which would destroy those two dependencies first.  Tear down the
    // dependent UI explicitly while its borrowed services are still alive.
    const auto docks = findChildren<QDockWidget*>(QString(), Qt::FindDirectChildrenOnly);
    for (QDockWidget* dock : docks) {
        removeDockWidget(dock);
        delete dock;
    }
    if (QWidget* central = takeCentralWidget()) delete central;

    delete m_core;
    m_core = nullptr;
    delete m_model;
    m_model = nullptr;
}

void MainWindow::loadStyleSheet() {
    // Search next to executable, then in standard locations
    QStringList paths = {
        QApplication::applicationDirPath() + "/dark.qss",
        QDir::currentPath() + "/dark.qss",
        ":/dark.qss"
    };
    for (auto& p : paths) {
        QFile f(p);
        if (f.open(QIODevice::ReadOnly)) {
            // Scope optional DesignStudio styling to this widget tree.  Never
            // overwrite the host application's palette or unrelated workbenches.
            setStyleSheet(QString::fromUtf8(f.readAll()));
            return;
        }
    }
}

void MainWindow::setupModel() {
    m_model = new ProjectModel(this);
    connect(m_model, &ProjectModel::modified, this, &MainWindow::updateTitle);
    connect(m_model, &ProjectModel::loaded,   this, &MainWindow::updateTitle);
    connect(m_model, &ProjectModel::modified, this, [this] {
        clearCadAssistSession(QStringLiteral(
            "CAD-assist evidence cleared: the project revision changed"));
        updateCadAssistAvailability();
    });
    connect(m_model, &ProjectModel::loaded, this, [this] {
        clearCadAssistSession(QStringLiteral(
            "No CAD-assist evidence loaded for this project"));
        updateCadAssistAvailability();
    });
}

void MainWindow::setupViews() {
    m_tabs = new QTabWidget(this);
    m_tabs->setTabPosition(QTabWidget::South);

    m_pcbCanvas    = new PcbCanvas(m_model, this);
    m_pcb3DView    = new designstudio::Pcb3DView(m_model, this);
    m_schView      = new SchematicView(m_model, this);
    if (m_uiProfile == UiProfile::Standalone)
        m_enclosureConcept = new designstudio::EnclosureConceptTab(this);

    m_productHome = new ProductHomePage(this);
    m_designFlow = new DesignFlowPanel(this);
    m_buildMap = new BuildMapPanel(this);
    m_tabs->addTab(m_productHome, QStringLiteral("Product Home"));
    m_tabs->addTab(m_designFlow, QStringLiteral("Design Flow"));
    m_tabs->addTab(m_buildMap, QStringLiteral("Build Map"));
    if (m_enclosureConcept)
        m_tabs->addTab(m_enclosureConcept, QStringLiteral("Mechanical"));
    m_tabs->addTab(m_schView, QStringLiteral("Schematic"));
    m_tabs->addTab(m_pcbCanvas, QStringLiteral("PCB"));
    m_tabs->addTab(m_pcb3DView, QStringLiteral("PCB 3D"));

    // Embedded profile: the FreeCAD host module listens for this tab and swaps
    // the shared MDI surface to the shaded mechanical document viewport. The
    // tab therefore belongs to the project instead of a separate window.
    if (m_uiProfile == UiProfile::FreeCadEmbedded) {
        m_mechanicalViewport = new QWidget(this);
        auto* mechanicalLayout = new QVBoxLayout(m_mechanicalViewport);
        mechanicalLayout->setContentsMargins(28, 24, 28, 24);
        auto* mechanicalNote = new QLabel(QStringLiteral(
            "Interactive shaded CAD viewport for this product.\n\n"
            "Opening the workspace mechanical document (product.FCStd)…\n"
            "If nothing appears, the document is missing from the workspace "
            "mechanical/ directory."), m_mechanicalViewport);
        mechanicalNote->setAlignment(Qt::AlignCenter);
        mechanicalNote->setWordWrap(true);
        mechanicalNote->setTextFormat(Qt::RichText);
        mechanicalNote->setTextInteractionFlags(Qt::TextSelectableByMouse);
        mechanicalLayout->addWidget(mechanicalNote);
        m_tabs->addTab(m_mechanicalViewport, QStringLiteral("Mechanical 3D"));
    }

    m_physicalDesignPanel = new PhysicalDesignPanel(this);
    m_tabs->addTab(m_physicalDesignPanel, QStringLiteral("Physical Design"));
    connect(m_physicalDesignPanel, &PhysicalDesignPanel::statusMessage,
            this, [this](const QString& message) {
        if (m_statusLabel) m_statusLabel->setText(message);
    });
    connect(m_physicalDesignPanel, &PhysicalDesignPanel::operationRequested,
            this, [this](const QString& operation, const QJsonObject& arguments) {
        const QString token = QStringLiteral("physical-design-%1")
            .arg(QUuid::createUuid().toString(QUuid::WithoutBraces));
        onCodexToolRequested(token, operation, arguments);
    });
    connect(m_physicalDesignPanel, &PhysicalDesignPanel::codexPromptRequested,
            this, [this](const QString& prompt) {
        if (m_chatDock) { m_chatDock->show(); m_chatDock->raise(); }
        if (m_chatPanel) m_chatPanel->submitPrompt(prompt);
    });

    m_verificationView = new QWidget(this);
    auto* verificationLayout = new QVBoxLayout(m_verificationView);    verificationLayout->setContentsMargins(28, 24, 28, 24);
    auto* verificationTitle = new QLabel(QStringLiteral("Verification and Release Readiness"),
                                         m_verificationView);
    QFont verificationFont = verificationTitle->font();
    verificationFont.setPointSize(verificationFont.pointSize() + 4);
    verificationFont.setBold(true);
    verificationTitle->setFont(verificationFont);
    auto* verificationText = new QLabel(
        QStringLiteral("Required routing, bindings, analysis and evidence must all pass before release."),
        m_verificationView);
    verificationText->setWordWrap(true);
    verificationText->setTextFormat(Qt::RichText);
    verificationText->setTextInteractionFlags(Qt::TextSelectableByMouse);
    verificationText->setMinimumHeight(150);
    auto* runChecks = new QPushButton(QStringLiteral("Run Checks"), m_verificationView);
    connect(runChecks, &QPushButton::clicked, this, [this] {
        if (m_drcDock) { m_drcDock->show(); m_drcDock->raise(); }
        if (m_drcPanel) m_drcPanel->runDrc();
    });
    verificationLayout->addWidget(verificationTitle);
    verificationLayout->addWidget(verificationText);
    verificationLayout->addWidget(runChecks, 0, Qt::AlignLeft);
    verificationLayout->addStretch(1);
    m_tabs->addTab(m_verificationView, QStringLiteral("Verification"));

    // User-selected tab visibility. Hidden tabs stay constructed so docks,
    // actions and evidence wiring remain valid; navigation falls back to
    // Product Home instead of switching to a page whose tab is hidden.
    const QStringList hiddenTabs =
        QSettings(QStringLiteral("DesignStudio"), QStringLiteral("DesignStudio"))
            .value(QStringLiteral("hiddenTabs")).toStringList();
    for (int i = 0; i < m_tabs->count(); ++i) {
        if (hiddenTabs.contains(m_tabs->tabText(i)))
            m_tabs->setTabVisible(i, false);
    }

    // Present only hash-matched evidence. A stale green report is more harmful
    // than no report at all, especially in a recorded review.
    connect(m_model, &ProjectModel::loaded, verificationText,
            [this, verificationText] {
        // ProjectModel::clear() emits loaded() while a new workspace is being
        // assembled.  During that short transition there is deliberately no
        // electronics file path yet.  Do not hand an empty path to QFile:
        // apart from the noisy QFSFileEngine warning, that made a clean
        // workspace look as if opening it had failed.
        const QString projectPath = m_model->filePath().trimmed();
        if (projectPath.isEmpty()) {
            verificationText->setText(QStringLiteral(
                "<h2 style='color:#9aa4b2'>Checks not run</h2>"
                "<p>Verification will be available after the workspace files are created.</p>"));
            return;
        }

        const QFileInfo project(projectPath);
        QFile projectFile(project.absoluteFilePath());
        const QString reportPath =
            project.absoluteDir().filePath(QStringLiteral("verification-report.json"));
        QFile reportFile(reportPath);
        if (!projectFile.open(QIODevice::ReadOnly)
            || !reportFile.open(QIODevice::ReadOnly)) {
            verificationText->setText(QStringLiteral(
                "<h2 style='color:#9aa4b2'>Checks not run</h2>"
                "<p>Run verification to create hash-bound electrical and mechanical evidence.</p>"));
            return;
        }
        const QString projectSha = QString::fromLatin1(
            QCryptographicHash::hash(projectFile.readAll(), QCryptographicHash::Sha256).toHex());
        const QJsonObject report =
            QJsonDocument::fromJson(reportFile.readAll()).object();
        const QJsonObject reportProject = report.value(QStringLiteral("project")).toObject();
        const QString evidenceSha =
            reportProject.value(QStringLiteral("file_sha256")).toString();
        if (projectSha != evidenceSha) {
            verificationText->setText(QStringLiteral(
                "<h2 style='color:#d29922'>Checks out of date</h2>"
                "<p>The project changed after its last verification. "
                "The previous pass is intentionally not shown as current.</p>"
                "<p><b>Run Checks</b> before recording or releasing this revision.</p>"));
            return;
        }

        const QString overall =
            report.value(QStringLiteral("overall_status")).toString(
                report.value(QStringLiteral("status")).toString());
        const QJsonObject categories =
            report.value(QStringLiteral("categories")).toObject();
        int passed = 0;
        for (auto it = categories.begin(); it != categories.end(); ++it)
            if (it.value().toObject().value(QStringLiteral("status")).toString()
                == QStringLiteral("pass"))
                ++passed;
        const QJsonObject drcMetrics = categories.value(QStringLiteral("drc"))
            .toObject().value(QStringLiteral("metrics")).toObject();
        const QJsonObject connectivityMetrics =
            categories.value(QStringLiteral("connectivity")).toObject()
                .value(QStringLiteral("metrics")).toObject();
        const bool pass = overall == QStringLiteral("pass");
        verificationText->setText(QStringLiteral(
            "<h2 style='color:%1'>%2</h2>"
            "<p style='font-size:14px'><b>%3/%4 required categories passed</b></p>"
            "<table cellspacing='8'>"
            "<tr><td>DRC errors</td><td><b>%5</b></td>"
            "<td>DRC warnings</td><td><b>%6</b></td></tr>"
            "<tr><td>Disconnected nets</td><td><b>%7</b></td>"
            "<td>Evidence</td><td><b>SHA-256 matched</b></td></tr>"
            "</table>"
            "<p style='color:#788492'>Verified %8 · revision %9</p>")
            .arg(pass ? QStringLiteral("#2ea043") : QStringLiteral("#f85149"),
                 pass ? QStringLiteral("VERIFIED — PASS")
                      : QStringLiteral("VERIFICATION FAILED"))
            .arg(passed)
            .arg(categories.size())
            .arg(drcMetrics.value(QStringLiteral("errors")).toInt())
            .arg(drcMetrics.value(QStringLiteral("warnings")).toInt())
            .arg(connectivityMetrics.value(QStringLiteral("disconnected_nets")).toInt())
            .arg(report.value(QStringLiteral("generated_utc")).toString())
            .arg(reportProject.value(QStringLiteral("revision")).toInt()));
    });

    connect(m_tabs, &QTabWidget::currentChanged, this, &MainWindow::onTabChanged);
    connect(m_pcbCanvas, &PcbCanvas::statusMessage, this, [this](const QString& msg){
        m_coordLabel->setText(msg);
    });
    connect(m_schView, &SchematicView::statusMessage, this, [this](const QString& msg){
        m_coordLabel->setText(msg);
    });
    connect(m_pcb3DView, &designstudio::Pcb3DView::statusMessage,
            this, [this](const QString& msg){ m_statusLabel->setText(msg); });
    if (m_enclosureConcept) {
        connect(m_enclosureConcept, &designstudio::EnclosureConceptTab::statusMessage,
                this, [this](const QString& msg){ m_statusLabel->setText(msg); });
    }

    connect(m_productHome, &ProductHomePage::newProductRequested,
            this, &MainWindow::onNew);
    connect(m_productHome, &ProductHomePage::openProductRequested,
            this, &MainWindow::onOpen);
    connect(m_productHome, &ProductHomePage::importElectronicsRequested,
            this, &MainWindow::onOpen);
    connect(m_productHome, &ProductHomePage::importFreeCadRequested, this, [this] {
        const QString path = QFileDialog::getOpenFileName(
            this, QStringLiteral("Import Existing FreeCAD Project"), QString(),
            QStringLiteral("FreeCAD Documents (*.FCStd);;All Files (*)"));
        if (path.isEmpty()) return;
        if (!m_nativeToolHandler) {
            QMessageBox::information(this, QStringLiteral("Import FreeCAD Project"),
                QStringLiteral("Activate DesignStudio inside FreeCAD to import this document into a product workspace."));
            return;
        }
        const QJsonObject result = m_nativeToolHandler(
            QStringLiteral("import_freecad_project"),
            QJsonObject{{QStringLiteral("path"), path}});
        m_statusLabel->setText(result.value(QStringLiteral("message")).toString());
    });
    connect(m_productHome, &ProductHomePage::resumeLastSessionRequested, this, [this] {
        const QStringList recent = QSettings(QStringLiteral("DesignStudio"),
            QStringLiteral("DesignStudio")).value(QStringLiteral("recentProducts")).toStringList();
        if (!recent.isEmpty()) {
            const QFileInfo info(recent.first());
            QString error;
            if (info.isDir() || info.fileName() == QStringLiteral("manifest.json"))
                openWorkspace(recent.first(), &error);
            else
                openFile(recent.first());
        }
    });
    connect(m_productHome, &ProductHomePage::assistantPromptRequested,
            this, [this](const QString& prompt) {
        if (m_chatDock) { m_chatDock->show(); m_chatDock->raise(); }
        m_chatPanel->submitPrompt(prompt);
    });
    connect(m_productHome, &ProductHomePage::applicationStarterRequested,
            this, [this](const QString& prompt) {
        if (m_chatDock) { m_chatDock->show(); m_chatDock->raise(); }
        m_chatPanel->submitPrompt(prompt);
    });
    connect(m_productHome, &ProductHomePage::recentProductRequested,
            this, [this](const QString& path) {
        const QFileInfo info(path);
        QString error;
        if (info.isDir() || info.fileName() == QStringLiteral("manifest.json"))
            openWorkspace(path, &error);
        else
            openFile(path);
    });
    connect(m_designFlow, &DesignFlowPanel::startRequested,
            this, [this](const QString& prompt) {
        if (m_chatDock) { m_chatDock->show(); m_chatDock->raise(); }
        if (m_chatPanel) m_chatPanel->submitPrompt(prompt);
    });
    connect(m_designFlow, &DesignFlowPanel::resumeRequested,
            this, [this](const QString& prompt) {
        if (m_chatDock) { m_chatDock->show(); m_chatDock->raise(); }
        if (m_chatPanel) m_chatPanel->submitPrompt(prompt);
    });
    connect(m_designFlow, &DesignFlowPanel::openChatRequested,
            this, [this] {
        if (m_chatDock) { m_chatDock->show(); m_chatDock->raise(); }
    });
    connect(m_model, &ProjectModel::loaded, this, [this] {
        if (m_designFlow)
            m_designFlow->setWorkspaceState(true, m_model->filePath());
    });
    m_productHome->setRecentProducts(QSettings(QStringLiteral("DesignStudio"),
        QStringLiteral("DesignStudio")).value(QStringLiteral("recentProducts")).toStringList());

    setCentralWidget(m_tabs);
}

void MainWindow::setupDocks() {
    // Left dock: layers + components
    {
        auto* dock = new QDockWidget("Product Tree", this);
        dock->setObjectName("dock_left");
        dock->setAllowedAreas(Qt::LeftDockWidgetArea | Qt::RightDockWidgetArea);

        auto* splitter = new QSplitter(Qt::Vertical);
        m_layerPanel = new LayerPanel(m_pcbCanvas);
        m_compPanel  = new ComponentPanel(m_model);

        splitter->addWidget(m_layerPanel);
        splitter->addWidget(m_compPanel);
        splitter->setStretchFactor(0, 1);
        splitter->setStretchFactor(1, 2);

        dock->setWidget(splitter);
        addDockWidget(Qt::LeftDockWidgetArea, dock);
        dock->setMinimumWidth(200);
        dock->setMaximumWidth(280);
    }

    // Bottom dock: DRC
    {
        m_drcDock = new QDockWidget("Verification Details", this);
        m_drcDock->setObjectName("dock_drc");
        m_drcDock->setAllowedAreas(Qt::BottomDockWidgetArea);

        m_drcPanel = new DrcPanel(m_model, m_core, this);
        m_drcDock->setWidget(m_drcPanel);
        addDockWidget(Qt::BottomDockWidgetArea, m_drcDock);
        m_drcDock->setMinimumHeight(160);
        m_drcDock->setMaximumHeight(320);
        m_drcDock->hide();

        connect(m_drcPanel, &DrcPanel::violationSelected, this, [this](double x, double y){
            // TODO: pan canvas to violation location
            (void)x; (void)y;
        });
    }

    // Right dock: AI Design Chat
    {
        m_chatDock = new QDockWidget("Codex", this);
        m_chatDock->setObjectName("dock_chat");
        m_chatDock->setAllowedAreas(Qt::LeftDockWidgetArea | Qt::RightDockWidgetArea);

        m_chatPanel = new DesignChatPanel(m_model, this);
        m_chatDock->setWidget(m_chatPanel);
        addDockWidget(Qt::RightDockWidgetArea, m_chatDock);
        m_chatDock->setMinimumWidth(300);
        m_chatDock->setMaximumWidth(480);

    }

    // Alternative configurations are hidden until the product graph contains
    // at least one real (non-placeholder) candidate.
    {
        m_configuratorDock = new QDockWidget(QStringLiteral("Design Alternatives"), this);
        m_configuratorDock->setObjectName(QStringLiteral("dock_design_alternatives"));
        m_configuratorDock->setAllowedAreas(Qt::LeftDockWidgetArea | Qt::RightDockWidgetArea);
        m_configuratorPanel = new ProductConfiguratorPanel(m_configuratorDock);
        m_configuratorDock->setWidget(m_configuratorPanel);
        addDockWidget(Qt::RightDockWidgetArea, m_configuratorDock);
        m_configuratorDock->setMinimumWidth(340);
        m_configuratorDock->hide();
        connect(m_configuratorPanel, &ProductConfiguratorPanel::statusMessage,
                this, [this](const QString& message) { m_statusLabel->setText(message); });
        connect(m_configuratorPanel, &ProductConfiguratorPanel::alternativesAvailable,
                this, [this](bool available) {
            m_configuratorDock->toggleViewAction()->setVisible(available);
            if (!available) m_configuratorDock->hide();
        });
        m_configuratorDock->toggleViewAction()->setVisible(false);
    }

    // Coupled physical-intelligence evidence is kept in a separate dock so a
    // user can inspect every affected object and incomplete solver gate after
    // applying a product edit without confusing it with a visual preview.
    {
        m_propagationDock = new QDockWidget(QStringLiteral("Engineering Propagation"), this);
        m_propagationDock->setObjectName(QStringLiteral("dock_engineering_propagation"));
        m_propagationDock->setAllowedAreas(Qt::LeftDockWidgetArea | Qt::RightDockWidgetArea
                                           | Qt::BottomDockWidgetArea);
        m_propagationPanel = new PropagationPanel(m_propagationDock);
        m_propagationDock->setWidget(m_propagationPanel);
        addDockWidget(Qt::RightDockWidgetArea, m_propagationDock);
        m_propagationDock->setMinimumWidth(380);
        m_propagationDock->hide();
        connect(m_configuratorPanel, &ProductConfiguratorPanel::configurationChanged,
                this, [this](const QString& configurationId, const QString& reason,
                             const QStringList& changedNodeIds) {
            if (!m_agentd || m_agentd->state() != AgentdClient::State::Ready
                || configurationId.isEmpty() || changedNodeIds.isEmpty()) return;
            QJsonArray nodes;
            for (const QString& nodeId : changedNodeIds) nodes.append(nodeId);
            m_propagationPanel->setPending(reason);
            m_propagationDock->show();
            m_agentd->invoke(QStringLiteral("propagation/run"), {
                {QStringLiteral("configuration_id"), configurationId},
                {QStringLiteral("changed_node_ids"), nodes},
                {QStringLiteral("edit_reason"), reason}},
                QStringLiteral("analysis:execute"));
        });
    }

    // AP242 identity is a single source of truth for the BOM, product graph,
    // FreeCAD feature-tree view and property inspector.  Keep this browser
    // hidden until a validated assembly is loaded so ordinary PCB projects do
    // not gain an empty dock.
    {
        m_semanticAssemblyDock = new QDockWidget(QStringLiteral("Semantic Assembly"), this);
        m_semanticAssemblyDock->setObjectName(QStringLiteral("dock_semantic_assembly"));
        m_semanticAssemblyDock->setAllowedAreas(Qt::LeftDockWidgetArea | Qt::RightDockWidgetArea);
        m_semanticAssemblyPanel = new designstudio::SemanticAssemblyPanel(m_semanticAssemblyDock);
        m_semanticAssemblyDock->setWidget(m_semanticAssemblyPanel);
        addDockWidget(Qt::RightDockWidgetArea, m_semanticAssemblyDock);
        m_semanticAssemblyDock->setMinimumWidth(360);
        m_semanticAssemblyDock->hide();
        connect(m_semanticAssemblyPanel, &designstudio::SemanticAssemblyPanel::semanticObjectActivated,
                this, [this](const QString& semanticId, const QString& reference) {
            m_selectionModel->select(semanticId, reference,
                                     QStringLiteral("semantic assembly"));
        });
    }

    // Right dock: independently validated drawing evidence. This panel has no
    // ProjectModel mutation surface; persistent changes still require a typed
    // proposal and explicit acceptance through the trusted policy boundary.
#ifdef DESIGNSTUDIO_DEVELOPER_TOOLS
    if (m_uiProfile == UiProfile::Standalone) {
        m_cadAssistDock = new QDockWidget("CAD Assist Evidence", this);
        m_cadAssistDock->setObjectName("dock_cad_assist");
        m_cadAssistDock->setAllowedAreas(Qt::LeftDockWidgetArea | Qt::RightDockWidgetArea);
        m_cadAssistPanel = new CadAssistPanel(m_cadAssistDock);
        m_cadAssistDock->setWidget(m_cadAssistPanel);
        addDockWidget(Qt::RightDockWidgetArea, m_cadAssistDock);
        m_cadAssistDock->setMinimumWidth(320);
        m_cadAssistDock->hide();
    }
#endif

    // Progress and activity remain in the Assistant timeline; there is no
    // second activity dock competing with the conversation.

    // Native in-process jobs requested from chat (run on the live model).
    connect(m_chatPanel, &DesignChatPanel::nativeJobRequested, this,
            [this](const QString& job){
        if (job == "save") {            // flush model so a file-based agent reads latest
            saveForAgents();
        } else if (job == "newproject") {
            // /build with no project open → create one so the design has somewhere
            // to land and the canvas can show it. Synchronous: sets the path before
            // the chat panel reads it back.
            m_model->clear();
            const QString dir = QDir::homePath() + "/DesignStudio";
            QDir().mkpath(dir);
            const QString path = dir + "/ai-build-" +
                QDateTime::currentDateTime().toString("yyyyMMdd-hhmmss") + ".dsproj";
            m_model->saveToFile(path);          // sets filePath + writes empty project
            m_chatPanel->setProjectPath(path);  // chat panel now has --project to pass
            if (m_actReload) m_actReload->setEnabled(true);
            updateCadAssistAvailability();
            m_statusLabel->setText("New project: " + path);
        } else if (job == "reload-generated-project") {
            if (m_model->filePath().isEmpty()
                || !m_model->loadFromFile(m_model->filePath())) {
                m_statusLabel->setText("Datasheet-derived electronics could not be reloaded: "
                                       + m_model->lastError());
                return;
            }
            if (!m_completedAgentStages.contains(QStringLiteral("electrical")))
                m_completedAgentStages.append(QStringLiteral("electrical"));
            m_completedAgentStages.removeAll(QStringLiteral("pcb"));
            m_completedAgentStages.removeAll(QStringLiteral("verification"));
            m_completedAgentStages.removeAll(QStringLiteral("verification-failed"));
            m_completedAgentStages.removeAll(QStringLiteral("verification-incomplete"));
            if (!m_completedAgentStages.contains(QStringLiteral("pcb-draft")))
                m_completedAgentStages.append(QStringLiteral("pcb-draft"));
            refreshEngineeringViews();
            navigateTo(m_pcbCanvas);
            m_pcbCanvas->fitToBoard();
            m_statusLabel->setText("Datasheet-derived SMT/THT footprints loaded as a PCB draft.");
        } else if (job == "placement-optimized") {
            // The deterministic file-based placer has completed its second
            // phase. Reload its atomic write, then promote the draft stage so
            // routing and verification cannot run against a draft layout.
            if (!m_model->filePath().isEmpty()
                && !m_model->loadFromFile(m_model->filePath())) {
                m_statusLabel->setText("Optimized placement could not be reloaded: "
                                       + m_model->lastError());
                return;
            }
            const bool provisional = std::any_of(m_model->footprints.cbegin(),
                m_model->footprints.cend(), [](const ProjFootprint& footprint) {
                    return footprint.mpn.startsWith(QStringLiteral("PROVISIONAL-"));
                });
            if (provisional) {
                m_completedAgentStages.removeAll(QStringLiteral("pcb"));
                if (!m_completedAgentStages.contains(QStringLiteral("pcb-draft")))
                    m_completedAgentStages.append(QStringLiteral("pcb-draft"));
                m_chatPanel->postToolResult("placement",
                    "Architecture placeholders were arranged, but physical PCB optimization waits for automatic datasheet extraction.",
                    false);
                return;
            }
            m_completedAgentStages.removeAll(QStringLiteral("pcb-draft"));
            if (!m_completedAgentStages.contains(QStringLiteral("pcb")))
                m_completedAgentStages.append(QStringLiteral("pcb"));
            refreshEngineeringViews();
            m_statusLabel->setText("Placement optimized; routing is now permitted.");
        } else if (job == "route") {
            if (m_completedAgentStages.contains(QStringLiteral("pcb-draft"))
                && !m_completedAgentStages.contains(QStringLiteral("pcb"))) {
                m_chatPanel->postToolResult(
                    "router",
                    "Routing is deferred until /layout completes optimized placement.",
                    false);
                return;
            }
            const bool provisional = std::any_of(m_model->footprints.cbegin(),
                m_model->footprints.cend(), [](const ProjFootprint& footprint) {
                    return footprint.mpn.startsWith(QStringLiteral("PROVISIONAL-"));
                });
            if (provisional) {
                m_chatPanel->postToolResult(
                    "router",
                    "Routing waits until architecture placeholders are replaced by approved Sol/Luna SMT/THT component assets.",
                    false);
                return;
            }
            if (!m_core || !m_core->isLoaded()) {
                m_chatPanel->postToolResult("router",
                    "Native engine not loaded — cannot route.", false);
                return;
            }
            // Same background-thread pattern as onAutoRoute() — keeps UI live.
            m_actAutoRoute->setEnabled(false);
            m_statusLabel->setText("Auto-routing (from chat)…");
            m_progressBar->setRange(0, 0);
            m_progressBar->setVisible(true);
            CoreBridge*   core  = m_core;
            ProjectModel* model = m_model;
            auto* w = new QFutureWatcher<RouteResult>(this);
            connect(w, &QFutureWatcher<RouteResult>::finished, this, [this, w]{
                RouteResult r = w->result();
                w->deleteLater();
                if (r.ok) {
                    m_model->traces += r.newTraces;
                    m_model->vias   += r.newVias;
                    m_model->setModified(true);
                    m_pcbCanvas->update();
                    saveForAgents();
                    // In the auto-finish chain (after /build) go straight to DRC;
                    // otherwise run the normal post-route courtyard cleanup.
                    if (m_chatPanel->autoFinishing())
                        m_chatPanel->continueAutoFinishAfterRoute();
                    else
                        m_chatPanel->runPostRouteCleanup();
                }
                m_chatPanel->postToolResult("router", r.message, r.ok);
                m_progressBar->setVisible(false);
                m_actAutoRoute->setEnabled(true);
            });
            w->setFuture(QtConcurrent::run([core, model]() -> RouteResult {
                return core->autoRoute(model);
            }));
        }
    });

    connect(m_chatPanel, &DesignChatPanel::codexToolRequested,
            this, &MainWindow::onCodexToolRequested);
    connect(m_chatPanel, &DesignChatPanel::codexToolApprovalResolved, this,
            [this](const QString& token, bool approved) {
        if (!m_pendingCodexTools.contains(token)) return;
        if (!approved) {
            const QString name = m_pendingCodexTools.take(token).name;
            const QJsonObject rejection = toolResult(
                false, QStringLiteral("User rejected stage: %1").arg(name));
            if (m_designFlow)
                m_designFlow->setToolResult(name, false,
                    QStringLiteral("User rejected the proposed operation."));
            if (m_physicalDesignPanel)
                m_physicalDesignPanel->setOperationResult(name, false, rejection);
            m_chatPanel->completeCodexTool(
                token, false, jsonText(rejection));
            return;
        }
        executeApprovedCodexTool(token);
    });
    connect(m_chatPanel, &DesignChatPanel::codexRunStopped, this, [this] {
        // Stop invalidates host-side approval tokens as well as the Codex
        // transport.  Otherwise a stale approval card could execute a
        // mutation after the user believed the run was cancelled.
        const auto pending = m_pendingCodexTools;
        m_pendingCodexTools.clear();
        if (!m_activeCodexToolToken.isEmpty()) {
            m_activeCodexToolCancelled = true;
            if (m_nativeToolCanceller) m_nativeToolCanceller();
        }
        for (auto it = pending.cbegin(); it != pending.cend(); ++it) {
            if (m_designFlow)
                m_designFlow->setToolResult(
                    it.value().name, false, QStringLiteral("Stopped by user."));
            if (m_physicalDesignPanel)
                m_physicalDesignPanel->setOperationResult(
                    it.value().name, false,
                    toolResult(false, QStringLiteral("Stopped by user; baseline unchanged.")));
        }
    });

    // Wire project path into chat panel so it can auto-export circuit state
    connect(m_model, &ProjectModel::loaded, this, [this]{
        m_chatPanel->setProjectPath(m_model->filePath());
    });

    // One semantic selection bus keeps the 3D AP242 object, PCB footprint,
    // product alternatives/property surface and future BOM/product-graph
    // consumers synchronized without view-to-view feedback loops.
    connect(m_compPanel, &ComponentPanel::componentFocused, this,
            [this](const QString& ref) {
        m_selectionModel->select({}, ref, QStringLiteral("PCB component tree"));
    });
    connect(m_pcbCanvas, &PcbCanvas::componentSelected, this,
            [this](const QString& ref) {
        m_selectionModel->select({}, ref, QStringLiteral("PCB canvas"));
    });
    connect(m_pcb3DView, &designstudio::Pcb3DView::componentSelected, this,
            [this](const QString& reference, const QString& semanticId) {
        m_selectionModel->select(semanticId, reference, QStringLiteral("3D AP242"));
    });
    connect(m_pcb3DView, &designstudio::Pcb3DView::semanticAssemblyChanged, this,
            [this](const QByteArray& json) {
        if (!m_semanticAssemblyPanel || !m_semanticAssemblyDock) return;
        m_semanticAssemblyPanel->setAssemblyJson(json);
        m_semanticAssemblyDock->setVisible(m_semanticAssemblyPanel->hasAssembly());
    });
    connect(m_selectionModel, &designstudio::SemanticSelectionModel::selectionChanged,
            this, [this](const QString& semanticId, const QString& reference,
                         const QString& source) {
        if (m_compPanel && !reference.isEmpty()) m_compPanel->selectComponent(reference);
        if (m_pcbCanvas) m_pcbCanvas->selectComponent(reference);
        if (m_pcb3DView) m_pcb3DView->selectComponent(
            reference.isEmpty() ? semanticId : reference);
        if (m_semanticAssemblyPanel)
            m_semanticAssemblyPanel->selectSemanticObject(semanticId, reference);
        if (m_nativeToolHandler && !semanticId.isEmpty()
            && source != QStringLiteral("FreeCAD tree")) {
            QJsonObject nativeArgs{{QStringLiteral("semantic_id"), semanticId}};
            nativeArgs.insert(QStringLiteral("reference_designator"), reference);
            if (m_agentd) {
                const auto workspace = m_agentd->workspace();
                nativeArgs.insert(QStringLiteral("mechanical_path"), workspace.mechanicalPath);
                nativeArgs.insert(QStringLiteral("workspace_root"), workspace.rootPath);
            }
            const QJsonObject result = m_nativeToolHandler(
                QStringLiteral("select_semantic_object"), nativeArgs);
            if (!result.value(QStringLiteral("ok")).toBool(false)
                && m_uiProfile == UiProfile::FreeCadEmbedded)
                m_statusLabel->setText(QStringLiteral("FreeCAD selection unavailable: %1")
                                           .arg(result.value(QStringLiteral("message")).toString()));
        }
        if (m_configuratorPanel)
            m_configuratorPanel->setSelectedSemanticObject(semanticId, reference, source);
        emit semanticSelectionChanged(semanticId, reference, source);
    });
    connect(m_model, &ProjectModel::loaded, this, [this] {
        if (m_semanticAssemblyPanel) m_semanticAssemblyPanel->setAssemblyJson({});
        if (m_semanticAssemblyDock) m_semanticAssemblyDock->hide();
        m_selectionModel->clear(QStringLiteral("workspace changed"));
    });
    // Components panel "Add Datasheet → Extract Footprint" runs the chat's
    // self-contained datasheet extractor (generates just that footprint).
    connect(m_compPanel, &ComponentPanel::extractDatasheetRequested,
            m_chatPanel, &DesignChatPanel::extractFootprintFromDatasheet);
    connect(m_model, &ProjectModel::loaded, this, [this]{
        m_layerPanel->setLayerCount(m_model->copperLayers);
        m_compPanel->refresh();
    });

    // Reuse only the user's explicitly selected external KiCad sources. No
    // KiCad library assets are copied into or distributed with DesignStudio.
    QSettings settings(QStringLiteral("DesignStudio"), QStringLiteral("DesignStudio"));
    for (const QString& source : settings.value(QStringLiteral("kicadSymbolSources"))
                                      .toStringList()) {
        QString ignored;
        m_schView->loadKicadSymbolSource(source, &ignored);
    }
}

void MainWindow::setupMenus() {
    auto* fileMenu = menuBar()->addMenu("&File");
    m_actNew = fileMenu->addAction("&New Product…", this, &MainWindow::onNew,
                                   QKeySequence::New);
    m_actOpen = fileMenu->addAction("&Open Product…", this, &MainWindow::onOpen,
                                    QKeySequence::Open);
    auto* importMenu = fileMenu->addMenu(QStringLiteral("Import"));
    importMenu->addAction(QStringLiteral("Existing FreeCAD Project…"),
                          [this] { emit m_productHome->importFreeCadRequested(); });
    importMenu->addAction(QStringLiteral("Existing Electronics Project…"),
                          this, &MainWindow::onOpen);
    m_actLoadKicadSymbols = importMenu->addAction(
        QStringLiteral("KiCad Symbol Library / Schematic…"), this, [this] {
            const QStringList paths = QFileDialog::getOpenFileNames(
                this, QStringLiteral("Load KiCad schematic symbols"), QString(),
                QStringLiteral("KiCad schematic sources (*.kicad_sym *.kicad_sch);;All files (*)"));
            if (paths.isEmpty()) return;
            QStringList failures;
            QStringList accepted;
            for (const QString& path : paths) {
                QString error;
                if (!m_schView->loadKicadSymbolSource(path, &error))
                    failures.append(QFileInfo(path).fileName() + QStringLiteral(": ") + error);
                else
                    accepted.append(QFileInfo(path).absoluteFilePath());
            }
            if (!accepted.isEmpty()) {
                QSettings settings(QStringLiteral("DesignStudio"), QStringLiteral("DesignStudio"));
                QStringList remembered = settings.value(QStringLiteral("kicadSymbolSources"))
                                             .toStringList();
                for (const QString& path : accepted)
                    if (!remembered.contains(path)) remembered.append(path);
                settings.setValue(QStringLiteral("kicadSymbolSources"), remembered);
            }
            navigateTo(m_schView);
            if (!failures.isEmpty())
                QMessageBox::warning(this, QStringLiteral("KiCad symbol import"),
                                     failures.join(QLatin1Char('\n')));
            m_statusLabel->setText(QStringLiteral("%1 KiCad symbols available to the schematic view")
                                       .arg(m_schView->loadedKicadSymbolCount()));
        });
    fileMenu->addAction(QStringLiteral("Export…"), this, [this] {
        navigateTo(m_verificationView);
        m_statusLabel->setText(QStringLiteral("Export is available after release readiness passes"));
    });
    fileMenu->addSeparator();
    m_actSave = fileMenu->addAction("&Save", this, &MainWindow::onSave,
                                    QKeySequence::Save);
    fileMenu->addAction(QStringLiteral("Close Product"), this, &QWidget::close,
                        QKeySequence::Close);

    // FreeCAD supplies native undo/redo and clipboard actions in the embedded
    // production profile. The standalone diagnostics shell adds no fake ones.
    menuBar()->addMenu("&Edit");

    auto* productMenu = menuBar()->addMenu("&Product");
    if (m_enclosureConcept)
        productMenu->addAction(QStringLiteral("Mechanical"), this,
                               [this] { navigateTo(m_enclosureConcept); });
    productMenu->addAction(QStringLiteral("Schematic"), this,
                           [this] { navigateTo(m_schView); });
    productMenu->addAction(QStringLiteral("PCB"), this,
                           [this] { navigateTo(m_pcbCanvas); });
    productMenu->addAction(QStringLiteral("Completed PCB 3D"), this, [this] {
        // In the FreeCAD-integrated profile, synchronize the assembly first.
        // Standalone DesignStudio still exposes the real STEP viewer directly.
        if (m_nativeToolHandler && m_agentd) {
            const auto workspace = m_agentd->workspace();
            if (!workspace.mechanicalPath.isEmpty() && !workspace.electronicsPath.isEmpty()) {
                saveForAgents();
                const QJsonObject result = m_nativeToolHandler(
                    QStringLiteral("sync_completed_pcb_3d"),
                    QJsonObject{{QStringLiteral("mechanical_path"), workspace.mechanicalPath},
                                {QStringLiteral("project_path"), workspace.electronicsPath}});
                m_statusLabel->setText(result.value(QStringLiteral("message")).toString());
                if (!result.value(QStringLiteral("ok")).toBool(false)) {
                    QMessageBox::warning(this, QStringLiteral("Completed PCB 3D"),
                                         m_statusLabel->text());
                    return;
                }
            }
        }
        m_pcb3DView->refreshFromProject();
        navigateTo(m_pcb3DView);
    });
    productMenu->addAction(QStringLiteral("Components and Bindings"), this, [this] {
        if (auto* tree = findChild<QDockWidget*>(QStringLiteral("dock_left"))) {
            tree->show(); tree->raise();
        }
    });
    productMenu->addAction(QStringLiteral("Design Alternatives"), this, [this] {
        if (m_configuratorDock->toggleViewAction()->isVisible()) {
            m_configuratorDock->show(); m_configuratorDock->raise();
        }
    });

    auto* verifyMenu = menuBar()->addMenu("&Verify");
    m_actRunDrc = verifyMenu->addAction(QStringLiteral("Run Checks"), this, [this] {
        navigateTo(m_verificationView);
        if (m_drcDock) { m_drcDock->show(); m_drcDock->raise(); }
        m_drcPanel->runDrc();
    }, QKeySequence(QStringLiteral("Ctrl+D")));
    verifyMenu->addAction(QStringLiteral("Evidence"), this,
                          [this] { navigateTo(m_verificationView); });
    verifyMenu->addAction(QStringLiteral("Release Readiness"), this,
                          [this] { navigateTo(m_verificationView); });

    // Contextual actions are created once and appear only on their workspace.
    m_actSelect = new QAction(QStringLiteral("Select"), this);
    m_actRoute = new QAction(QStringLiteral("Route Trace"), this);
    m_actVia = new QAction(QStringLiteral("Place Via"), this);
    connect(m_actSelect, &QAction::triggered, this,
            [this] { m_pcbCanvas->setActiveTool(Tool::Select); });
    connect(m_actRoute, &QAction::triggered, this,
            [this] { m_pcbCanvas->setActiveTool(Tool::RouteTrace); });
    connect(m_actVia, &QAction::triggered, this,
            [this] { m_pcbCanvas->setActiveTool(Tool::PlaceVia); });
    m_actSelect->setCheckable(true); m_actSelect->setChecked(true);
    m_actRoute->setCheckable(true);
    m_actVia->setCheckable(true);
    m_toolGroup = new QActionGroup(this);
    m_toolGroup->addAction(m_actSelect);
    m_toolGroup->addAction(m_actRoute);
    m_toolGroup->addAction(m_actVia);
    m_toolGroup->setExclusive(true);

    m_actAutoRoute = new QAction(QStringLiteral("Auto-route…"), this);
    connect(m_actAutoRoute, &QAction::triggered, this, &MainWindow::onAutoRoute);
    m_actBoardSetup = new QAction(QStringLiteral("Board Setup…"), this);
    connect(m_actBoardSetup, &QAction::triggered, this, &MainWindow::onBoardSetup);
    m_actPour = new QAction(QStringLiteral("Copper Pour"), this);
    connect(m_actPour, &QAction::triggered, this, [this] {
        if (!m_core) return;
        // Scanline copper fill over every layer is heavy on a dense board — run it
        // off the UI thread (matches Auto Route / DRC) so the window stays live.
        m_actPour->setEnabled(false);
        m_statusLabel->setText("Pouring copper…");
        CoreBridge*   core  = m_core;
        ProjectModel* model = m_model;
        int           layer = m_pcbCanvas->activeLayer();
        auto* w = new QFutureWatcher<bool>(this);
        connect(w, &QFutureWatcher<bool>::finished, this, [this, w]{
            m_pcbCanvas->update();
            m_statusLabel->setText(w->result() ? "Copper pour complete" : "Copper pour found nothing to fill");
            m_actPour->setEnabled(true);
            w->deleteLater();
        });
        w->setFuture(QtConcurrent::run([core, model, layer]{
            return core->pourCopper(model, -1, layer);
        }));
    });

#ifdef DESIGNSTUDIO_DEVELOPER_TOOLS
    if (m_uiProfile == UiProfile::Standalone) {
        auto* diagnosticsMenu = menuBar()->addMenu(QStringLiteral("Diagnostics"));
        m_actLoadCadAssist = diagnosticsMenu->addAction(
            "Load CAD Assist Evidence…", this, [this] {
            const QString path = QFileDialog::getOpenFileName(
                this,
                "Load CAD Assist Evidence",
                QString(),
                "CAD Assist Sessions (*.json);;All Files (*)");
            if (path.isEmpty()) return;
            QString error;
            if (!loadCadAssistContract(path, &error)) {
                QMessageBox::critical(
                    this,
                    "CAD Assist Evidence Rejected",
                    "The evidence was not attached to the project.\n\n" + error);
            }
            });
        m_actCopyCadAssistContext = diagnosticsMenu->addAction(
            "Copy CAD Assist Binding", this, [this] {
            const QString environment = QStringLiteral(
                "export DESIGNSTUDIO_PROJECT_ID='%1'\n"
                "export DESIGNSTUDIO_PROJECT_REVISION='%2'\n"
                "export DESIGNSTUDIO_PROJECT_SHA256='%3'\n")
                    .arg(m_model->documentId())
                    .arg(m_model->revision())
                    .arg(m_model->fileSha256());
            QApplication::clipboard()->setText(environment);
            m_statusLabel->setText(
                "Copied project ID, revision, and SHA-256 for CAD Visual Helper");
            });
        updateCadAssistAvailability();
        diagnosticsMenu->addAction(QStringLiteral("API Credentials…"), this, [this] {
            CredentialsDialog dialog(this);
            dialog.exec();
        });
    }
#endif

    auto* viewMenu = menuBar()->addMenu("&View");
    m_actZoomIn = viewMenu->addAction(QStringLiteral("Zoom In"), this, [this] {
        if (m_tabs->currentWidget() == m_pcbCanvas) m_pcbCanvas->zoomIn();
        else if (m_tabs->currentWidget() == m_schView) m_schView->zoomIn();
    }, QKeySequence::ZoomIn);
    m_actZoomOut = viewMenu->addAction(QStringLiteral("Zoom Out"), this, [this] {
        if (m_tabs->currentWidget() == m_pcbCanvas) m_pcbCanvas->zoomOut();
        else if (m_tabs->currentWidget() == m_schView) m_schView->zoomOut();
    }, QKeySequence::ZoomOut);
    m_actZoomFit = viewMenu->addAction(QStringLiteral("Fit Active Design to View"), this, [this] {
        if (m_tabs->currentWidget() == m_pcbCanvas) m_pcbCanvas->fitToBoard();
        else if (m_tabs->currentWidget() == m_schView) m_schView->fitToSchematic();
        else if (m_tabs->currentWidget() == m_pcb3DView) m_pcb3DView->fitToModel();
    }, QKeySequence(Qt::Key_Home));
    viewMenu->addSeparator();
    for (auto* dock : findChildren<QDockWidget*>()) {
        QAction* toggle = dock->toggleViewAction();
        if (dock == m_chatDock) {
            m_actToggleCodex = toggle;
            m_actToggleCodex->setObjectName(QStringLiteral("action_toggle_codex_side_panel"));
            m_actToggleCodex->setText(QStringLiteral("Toggle Codex Side Panel"));
            m_actToggleCodex->setToolTip(
                QStringLiteral("Toggle Codex side panel (Ctrl+Alt+B)"));
            m_actToggleCodex->setStatusTip(
                QStringLiteral("Show or hide the Codex chat side panel"));
            m_actToggleCodex->setShortcut(QKeySequence(QStringLiteral("Ctrl+Alt+B")));
            m_actToggleCodex->setShortcutContext(Qt::ApplicationShortcut);
            m_actToggleCodex->setIcon(codexSidePanelIcon());
        }
        viewMenu->addAction(toggle);
    }

    auto* helpMenu = menuBar()->addMenu("&Help");
    helpMenu->addAction(QStringLiteral("DesignStudio Help"));
    helpMenu->addAction(QStringLiteral("Report Problem"));
    helpMenu->addAction("About DesignStudio", this, [this]{
        QMessageBox::about(this, "About DesignStudio",
            "<h3>DesignStudio</h3><p>Unified industrial product design workspace.</p>");
    });
}

void MainWindow::setupToolbar() {
    m_contextToolbar = addToolBar(QStringLiteral("DesignStudio Workspace"));
    m_contextToolbar->setObjectName(QStringLiteral("toolbar_designstudio_context"));
    m_contextToolbar->setMovable(false);
    m_pcbContextActions = {m_actSelect, m_actRoute, m_actVia, m_actAutoRoute,
                           m_actBoardSetup, m_actPour, m_actRunDrc};
    auto* erc = new QAction(QStringLiteral("Run ERC"), this);
    connect(erc, &QAction::triggered, m_drcPanel, &DrcPanel::runDrc);
    auto* simulation = new QAction(QStringLiteral("Simulation"), this);
    connect(simulation, &QAction::triggered, this, [this] {
        m_statusLabel->setText(QStringLiteral("Simulation requires a complete bound schematic"));
    });
    m_schematicContextActions = {m_actLoadKicadSymbols, erc, simulation};
    m_pcb3DContextActions = {m_actZoomFit};
    m_verificationContextActions = {m_actRunDrc};
    auto* enclosure = new QAction(QStringLiteral("Generate Parametric Enclosure"), this);
    connect(enclosure, &QAction::triggered, this, [this] {
        if (m_enclosureConcept) navigateTo(m_enclosureConcept);
    });
    m_mechanicalContextActions = {enclosure};
    updateWorkspaceActions(m_tabs->currentWidget());
}

void MainWindow::setupStatusBar() {
    m_statusLabel     = new QLabel("Ready", this);
    m_coordLabel      = new QLabel("", this);
    m_coreStatusLabel = new QLabel("", this);
    m_controlPlaneStatusLabel = new QLabel("Control: offline", this);

    m_coordLabel->setMinimumWidth(280);
    m_coreStatusLabel->setProperty("muted","true");
    m_controlPlaneStatusLabel->setProperty("muted", "true");

    m_progressBar = new QProgressBar(this);
    m_progressBar->setFixedWidth(140);
    m_progressBar->setFixedHeight(14);
    m_progressBar->setTextVisible(false);
    m_progressBar->setStyleSheet(
        "QProgressBar{background:#161b22;border:1px solid #30363d;border-radius:3px;}"
        "QProgressBar::chunk{background:#1f6feb;border-radius:2px;}");
    m_progressBar->setVisible(false);

    statusBar()->addWidget(m_statusLabel, 1);
    statusBar()->addPermanentWidget(m_progressBar);
    statusBar()->addPermanentWidget(m_coordLabel);
    statusBar()->addPermanentWidget(m_controlPlaneStatusLabel);
    statusBar()->addPermanentWidget(m_coreStatusLabel);
}

void MainWindow::setupControlPlane() {
    m_agentd = new AgentdClient(this);
    m_configuratorPanel->setClient(m_agentd);
    m_buildMap->setClient(m_agentd);
    connect(m_agentd, &AgentdClient::responseReceived, this,
            [this](const QString& method, const QJsonObject& result) {
        if (method != QStringLiteral("propagation/run") || !m_propagationPanel) return;
        m_propagationPanel->setRun(result);
        if (m_propagationDock) m_propagationDock->show();
    });
    connect(m_agentd, &AgentdClient::requestFailed, this,
            [this](const QString& method, const QString& message) {
        if (method != QStringLiteral("propagation/run") || !m_propagationPanel) return;
        m_propagationPanel->setPending(QStringLiteral("failed: %1").arg(message));
        if (m_propagationDock) m_propagationDock->show();
    });
    connect(m_buildMap, &BuildMapPanel::statusMessage, this,
            [this](const QString& message) { m_statusLabel->setText(message); });
    connect(m_agentd, &AgentdClient::stateChanged, this,
            [this](AgentdClient::State state, const QString& detail) {
        const bool ready = state == AgentdClient::State::Ready;
        const bool failed = state == AgentdClient::State::Failed;
        m_controlPlaneStatusLabel->setText(
            QStringLiteral("Control: %1").arg(m_agentd->stateText()));
        m_controlPlaneStatusLabel->setToolTip(detail);
        m_controlPlaneStatusLabel->setStyleSheet(
            ready ? QStringLiteral("color:#3fb950;")
                  : failed ? QStringLiteral("color:#f85149;")
                           : QStringLiteral("color:#d29922;"));
        if (!detail.isEmpty()) m_statusLabel->setText(detail);
        emit workspaceLifecycleChanged(m_agentd->stateText(), detail);
    });
    connect(m_agentd, &AgentdClient::workspaceOpened, this,
            [this](const QString&, qint64 workspaceRevision,
                   qint64 graphRevision, qint64 daemonRevision, bool recovered) {
        m_controlPlaneStatusLabel->setText(
            QStringLiteral("Control: ready · W%1/G%2/D%3")
                .arg(workspaceRevision).arg(graphRevision).arg(daemonRevision));
        m_statusLabel->setText(recovered
            ? QStringLiteral("Control plane recovered; workspace revisions synchronized")
            : QStringLiteral("Workspace revisions synchronized with local control plane"));
        // The alternatives panel decides whether the graph contains a real
        // candidate. Placeholder-only workspaces keep the dock unavailable.
    });
}

QJsonObject MainWindow::createProductWorkspace(const QString& name,
                                                const QString& description) {
    return createAgentWorkspace(QJsonObject{
        {QStringLiteral("name"), name},
        {QStringLiteral("description"), description},
    });
}

void MainWindow::tryLoadCore() {
    QStringList candidates = {
        QApplication::applicationDirPath() + "/libdesigncore.so",
        QDir::cleanPath(QApplication::applicationDirPath() + "/../lib/libdesigncore.so"),
        QDir::cleanPath(QApplication::applicationDirPath() + "/../../core/libdesigncore.so"),
        QDir::currentPath() + "/core/build/libdesigncore.so",
        QDir::currentPath() + "/libdesigncore.so",
        "libdesigncore.so",
    };
    for (auto& path : candidates) {
        QString err;
        m_core = CoreBridge::create(path, err);
        if (m_core) {
            m_coreStatusLabel->setText("Engine: loaded");
            m_coreStatusLabel->setStyleSheet("color:#3fb950;");
            if (m_drcPanel) m_drcPanel->setCore(m_core);
            if (m_chatPanel) m_chatPanel->setCore(m_core);
            if (m_pcbCanvas) m_pcbCanvas->setCore(m_core);
            return;
        }
    }
    m_coreStatusLabel->setText("Engine: not loaded (DRC/route offline)");
    m_coreStatusLabel->setStyleSheet("color:#d29922;");
}

bool MainWindow::exportVerificationReport(const QString& path, QString* errorMessage) {
    if (!m_core || !m_core->isLoaded()) {
        if (errorMessage) *errorMessage = "Native verification engine is not loaded";
        return false;
    }
    const QJsonObject report = m_core->buildVerificationReport(m_model);
    QSaveFile file(path);
    if (!file.open(QIODevice::WriteOnly)) {
        if (errorMessage) *errorMessage = file.errorString();
        return false;
    }
    if (file.write(QJsonDocument(report).toJson(QJsonDocument::Indented)) < 0 || !file.commit()) {
        if (errorMessage) *errorMessage = file.errorString();
        return false;
    }
    return true;
}

// ── File operations ───────────────────────────────────────────────────────────
QString MainWindow::projectFilePath() const {
    return m_model ? m_model->filePath() : QString();
}

void MainWindow::navigateTo(QWidget* widget) {
    if (!m_tabs || !widget) return;
    const int index = m_tabs->indexOf(widget);
    if (index >= 0 && !m_tabs->isTabVisible(index)) {
        // A hidden tab must never become the current page: docks and status
        // wiring assume the visible page matches the active tab.
        const int home = m_tabs->indexOf(m_productHome);
        if (home >= 0 && m_tabs->isTabVisible(home)) {
            m_tabs->setCurrentIndex(home);
            return;
        }
    }
    m_tabs->setCurrentWidget(widget);
}

bool MainWindow::openFile(const QString& path) {
    if (!m_model->loadFromFile(path)) {
        const QString message = "Could not open file:\n" + path + "\n\n" + m_model->lastError();
        if (m_nonInteractive)
            qWarning().noquote() << "PROJECT_REJECTED:" << message;
        else
            QMessageBox::critical(this, "Open Project Failed", message);
        return false;
    }
    const QString placementMode = m_model->placementState.value(QStringLiteral("mode"))
                                      .toString().trimmed().toLower();
    if (placementMode == QStringLiteral("draft")) {
        m_completedAgentStages.removeAll(QStringLiteral("pcb"));
        if (!m_completedAgentStages.contains(QStringLiteral("pcb-draft")))
            m_completedAgentStages.append(QStringLiteral("pcb-draft"));
    } else if (placementMode == QStringLiteral("optimized")) {
        m_completedAgentStages.removeAll(QStringLiteral("pcb-draft"));
        if (!m_completedAgentStages.contains(QStringLiteral("pcb")))
            m_completedAgentStages.append(QStringLiteral("pcb"));
    }
    m_statusLabel->setText("Opened: " + path);
    if (m_actReload) m_actReload->setEnabled(true);
    addRecentProduct(path);
    return true;
}

bool MainWindow::openFileForVerification(const QString& path, QString* errorMessage) {
    if (!m_model->loadForVerification(path)) {
        if (errorMessage) *errorMessage = m_model->lastError();
        return false;
    }
    m_statusLabel->setText("Verification input: " + path);
    // Deliberately do not add a read-only project/3 wrapper to the recent-file
    // editing list or enable reload/save affordances through this CLI path.
    return true;
}

bool MainWindow::openWorkspace(const QString& path, QString* errorMessage) {
    AgentdClient::WorkspaceDocuments documents;
    QString error;
    if (!AgentdClient::resolveWorkspace(path, &documents, &error)) {
        if (errorMessage) *errorMessage = error;
        return false;
    }
    QFile manifestInput(documents.manifestPath);
    if (manifestInput.open(QIODevice::ReadOnly)) {
        const QJsonObject manifest = QJsonDocument::fromJson(manifestInput.readAll()).object();
        const QJsonObject product = manifest.value(QStringLiteral("product")).toObject();
        m_activeProductName = product.value(QStringLiteral("name")).toString();
        m_activeProductDescription = product.value(QStringLiteral("description")).toString();
        m_activeApplicationFamily = designstudio::classifyApplicationFamily(
            m_activeProductName + QLatin1Char(' ') + m_activeProductDescription);
        m_activeAxisCount = designstudio::axisCountFromPrompt(
            m_activeProductName + QLatin1Char(' ') + m_activeProductDescription,
            m_activeApplicationFamily == QStringLiteral("robotic_joint_capstone") ? 3 : 1);
    }
    if (!openFile(documents.electronicsPath)) {
        if (errorMessage) *errorMessage = m_model->lastError();
        return false;
    }
    // Reconstruct the durable workflow stage set before the control plane is
    // reopened. This lets Codex resume after a desktop or daemon restart
    // without repeating successful geometry/electronics mutations.
    m_completedAgentStages = {QStringLiteral("workspace")};
    if (m_model->mechanicalContract.value(QStringLiteral("status")).toString()
        == QStringLiteral("locked"))
        m_completedAgentStages.append(QStringLiteral("mechanical"));
    if (!m_model->footprints.isEmpty() || !m_model->symbols.isEmpty())
        m_completedAgentStages.append(QStringLiteral("electrical"));
    if (m_model->boardOutline.size() >= 3)
        m_completedAgentStages.append(QStringLiteral("pcb"));
    const QString verificationPath = QDir(documents.rootPath).filePath(
        QStringLiteral("verification/reports/engineering-verification.json"));
    QFile verificationFile(verificationPath);
    if (verificationFile.open(QIODevice::ReadOnly)) {
        const QJsonObject report = QJsonDocument::fromJson(verificationFile.readAll()).object();
        const QJsonObject project = report.value(QStringLiteral("project")).toObject();
        if (report.value(QStringLiteral("overall_status")).toString() == QStringLiteral("pass")
            && project.value(QStringLiteral("document_id")).toString() == m_model->documentId()
            && project.value(QStringLiteral("revision")).toInteger(-1) == m_model->revision()
            && project.value(QStringLiteral("file_sha256")).toString() == m_model->fileSha256())
            m_completedAgentStages.append(QStringLiteral("verification"));
    }
    if (!m_agentd->openWorkspace(documents.manifestPath, &error)) {
        if (errorMessage) *errorMessage = error;
        return false;
    }
    if (m_enclosureConcept) {
        const QJsonObject contract = m_model->mechanicalContract;
        const QString contractId = contract.value(QStringLiteral("contract_id")).toString();
        const QString contractSha = contract.value(QStringLiteral("contract_digest")).toString();
        m_enclosureConcept->setPlacementDocumentBinding(
            contractId.isEmpty() ? m_model->documentId() : contractId,
            contractId.isEmpty() ? m_model->revision()
                                 : contract.value(QStringLiteral("revision")).toInteger(),
            contractSha.isEmpty() ? m_model->fileSha256() : contractSha);
        const QJsonObject designVolume =
            contract.value(QStringLiteral("design_volume_mm")).toObject();
        const QJsonArray volumeMin = designVolume.value(QStringLiteral("min")).toArray();
        const QJsonArray volumeMax = designVolume.value(QStringLiteral("max")).toArray();
        if (volumeMin.size() == 3 && volumeMax.size() == 3) {
            m_enclosureConcept->setSupportDesignVolumeMm(QVector3D(
                float(volumeMax[0].toDouble() - volumeMin[0].toDouble()),
                float(volumeMax[1].toDouble() - volumeMin[1].toDouble()),
                float(volumeMax[2].toDouble() - volumeMin[2].toDouble())));
        }
    }
    m_statusLabel->setText(QStringLiteral("Opening workspace: %1").arg(documents.rootPath));
    addRecentProduct(documents.rootPath);
    if (errorMessage) errorMessage->clear();
    return true;
}

bool MainWindow::loadCadAssistContract(const QString& path, QString* errorMessage) {
    if (!m_cadAssistPanel || !m_cadAssistDock) {
        if (errorMessage)
            *errorMessage = "CAD Assist is owned by FreeCAD in the electronics UI profile";
        return false;
    }
    const designstudio::CadAssistLoadResult result =
        designstudio::loadCadAssistForProject(path, *m_model);
    if (!result.ok()) {
        if (errorMessage) *errorMessage = result.errorSummary();
        return false;
    }

    m_cadAssistPanel->setSession(result.session, path);
    m_cadAssistDock->show();
    m_cadAssistDock->raise();
    m_statusLabel->setText(
        QStringLiteral("Validated CAD-assist session: %1")
            .arg(result.session->sessionId()));
    if (errorMessage) errorMessage->clear();
    return true;
}

bool MainWindow::loadEnclosureGlb(const QString& path, QString* errorMessage) {
    if (!m_enclosureConcept) {
        if (errorMessage)
            *errorMessage = "Enclosure CAD is owned by FreeCAD in the electronics UI profile";
        return false;
    }
    if (!m_enclosureConcept->loadGlb(path, errorMessage)) return false;
    navigateTo(m_enclosureConcept);
    return true;
}

bool MainWindow::exportEnclosureEvidence(const QString& path, QString* errorMessage) const {
    if (!m_enclosureConcept) {
        if (errorMessage)
            *errorMessage = "Enclosure CAD is owned by FreeCAD in the electronics UI profile";
        return false;
    }
    return m_enclosureConcept->exportEvidence(path, errorMessage);
}

bool MainWindow::generateBalancingSphereDemo(const QString& outputDirectory,
                                              QString* errorMessage) {
    if (!m_enclosureConcept) {
        if (errorMessage)
            *errorMessage = "Mechanical demonstration generation is owned by FreeCAD in the electronics UI profile";
        return false;
    }
    const bool ok = m_enclosureConcept->generateBalancingSphereDemo(
        outputDirectory, errorMessage);
    if (ok) navigateTo(m_enclosureConcept);
    return ok;
}

// Flush the in-memory model to disk so a read-only agent sees the latest state.
// Agent output is never accepted by watching/reloading this file; persistent AI
// changes must travel through the proposal-validation boundary.
void MainWindow::saveForAgents() {
    if (m_model->filePath().isEmpty()) return;
    if (m_model->saveToFile(m_model->filePath())) updateCadAssistAvailability();
}

void MainWindow::onCodexToolRequested(const QString& token, const QString& tool,
                                      const QJsonObject& arguments) {
    qInfo().noquote() << "DESIGNSTUDIO_TOOL_RECEIVED:" << tool;
    if (tool == QStringLiteral("read_product_state")) {
        m_chatPanel->completeCodexTool(token, true, jsonText(productState()));
        return;
    }
    if (tool == QStringLiteral("show_workspace_view")) {
        const QString view = arguments.value(QStringLiteral("view")).toString();
        QJsonObject nativeResult;
        if (view == QStringLiteral("pcb")) navigateTo(m_pcbCanvas);
        else if (view == QStringLiteral("schematic")) navigateTo(m_schView);
        else if (view == QStringLiteral("evidence")) {
            if (auto* dock = findChild<QDockWidget*>(QStringLiteral("dock_drc"))) {
                dock->show();
                dock->raise();
            }
        } else if (view == QStringLiteral("mechanical") && m_nativeToolHandler) {
            nativeResult = m_nativeToolHandler(tool, arguments);
        } else if (view == QStringLiteral("pcb_3d") && m_nativeToolHandler) {
            const auto workspace = m_agentd->workspace();
            nativeResult = m_nativeToolHandler(QStringLiteral("sync_completed_pcb_3d"),
                QJsonObject{{QStringLiteral("mechanical_path"), workspace.mechanicalPath},
                            {QStringLiteral("project_path"), workspace.electronicsPath}});
            if (!nativeResult.value(QStringLiteral("ok")).toBool(false)) {
                m_chatPanel->completeCodexTool(token, false, jsonText(nativeResult));
                return;
            }
        }
        m_chatPanel->completeCodexTool(token, true,
            jsonText(nativeResult.isEmpty()
                ? toolResult(true, QStringLiteral("Visible view switched to %1").arg(view))
                : nativeResult));
        return;
    }
    if (tool == QStringLiteral("capture_workspace_view")) {
        const QString view = arguments.value(QStringLiteral("view")).toString();
        if (view == QStringLiteral("pcb")) navigateTo(m_pcbCanvas);
        else if (view == QStringLiteral("schematic")) navigateTo(m_schView);
        else if ((view == QStringLiteral("mechanical") || view == QStringLiteral("pcb_3d"))
                 && m_nativeToolHandler)
            m_nativeToolHandler(QStringLiteral("show_workspace_view"), arguments);
        QApplication::processEvents();
        QWidget* target = QApplication::activeWindow();
        if (!target) target = window();
        QPixmap pixmap = target->grab();
        QByteArray png;
        QBuffer buffer(&png);
        buffer.open(QIODevice::WriteOnly);
        pixmap.save(&buffer, "PNG");
        const QString imageUrl = QStringLiteral("data:image/png;base64,")
            + QString::fromLatin1(png.toBase64());
        m_chatPanel->completeCodexTool(token, true, QJsonArray{
            QJsonObject{{QStringLiteral("type"), QStringLiteral("inputText")},
                        {QStringLiteral("text"), QStringLiteral("Live %1 view captured at %2x%3")
                            .arg(view).arg(pixmap.width()).arg(pixmap.height())}},
            QJsonObject{{QStringLiteral("type"), QStringLiteral("inputImage")},
                        {QStringLiteral("imageUrl"), imageUrl}}
        });
        return;
    }

    static const QSet<QString> mutating{
        QStringLiteral("create_product_workspace"),
        QStringLiteral("apply_mechanical_stage"),
        QStringLiteral("apply_mechanical_cad_program"),
        QStringLiteral("create_physical_design_session"),
        QStringLiteral("create_physical_design_session_v2"),
        QStringLiteral("create_guided_physical_design_session"),
        QStringLiteral("generate_constraint_candidates"),
        QStringLiteral("export_physical_test_plan"),
        QStringLiteral("record_physical_observation"),
        QStringLiteral("create_physical_candidate_decision"),
        QStringLiteral("capture_selected_design_region"),
        QStringLiteral("preview_local_redesign"),
        QStringLiteral("commit_local_redesign"),
        QStringLiteral("capture_local_redesign_v2"),
        QStringLiteral("preview_local_redesign_v2"),
        QStringLiteral("commit_local_redesign_v2"),
        QStringLiteral("discard_local_redesign_v2"),
        QStringLiteral("rollback_local_redesign_v2"),
        QStringLiteral("generate_authoritative_drawings"),
        QStringLiteral("generate_interaction_structure"),
        QStringLiteral("apply_electrical_stage"),
        QStringLiteral("apply_pcb_stage"),
        QStringLiteral("extract_component_datasheet"),
        QStringLiteral("publish_component_assets"),
        QStringLiteral("build_electronics_from_datasheets"),
        QStringLiteral("run_engineering_checks"),
        QStringLiteral("commit_configuration")
    };
    if (!mutating.contains(tool)) {
        m_chatPanel->completeCodexTool(token, false,
            jsonText(toolResult(false, QStringLiteral("Unregistered DesignStudio tool: %1").arg(tool))));
        return;
    }
    const QMap<QString, QString> titles{
        {QStringLiteral("create_product_workspace"), QStringLiteral("Create unified product workspace")},
        {QStringLiteral("apply_mechanical_stage"), QStringLiteral("Build deterministic enclosure")},
        {QStringLiteral("apply_mechanical_cad_program"), QStringLiteral("Apply mechanical feature program")},
        {QStringLiteral("create_physical_design_session"), QStringLiteral("Create physical design session")},
        {QStringLiteral("create_physical_design_session_v2"), QStringLiteral("Capture constraint-driven design session")},
        {QStringLiteral("create_guided_physical_design_session"), QStringLiteral("Create guided photo/sketch/hardware design session")},
        {QStringLiteral("generate_constraint_candidates"), QStringLiteral("Generate three physical design candidates")},
        {QStringLiteral("export_physical_test_plan"), QStringLiteral("Export three physical grip-test bucks")},
        {QStringLiteral("record_physical_observation"), QStringLiteral("Record a physical grip-test observation")},
        {QStringLiteral("create_physical_candidate_decision"), QStringLiteral("Create the digest-bound physical candidate decision")},
        {QStringLiteral("capture_selected_design_region"), QStringLiteral("Capture selected redesign region")},
        {QStringLiteral("preview_local_redesign"), QStringLiteral("Build bounded redesign preview")},
        {QStringLiteral("commit_local_redesign"), QStringLiteral("Commit verified local redesign")},
        {QStringLiteral("capture_local_redesign_v2"), QStringLiteral("Capture topology-aware FaceN redesign")},
        {QStringLiteral("preview_local_redesign_v2"), QStringLiteral("Build topology-aware local patch preview")},
        {QStringLiteral("commit_local_redesign_v2"), QStringLiteral("Commit topology-aware local patch")},
        {QStringLiteral("discard_local_redesign_v2"), QStringLiteral("Reject uncommitted local patch preview")},
        {QStringLiteral("rollback_local_redesign_v2"), QStringLiteral("Rollback topology-aware local patch")},
        {QStringLiteral("generate_authoritative_drawings"), QStringLiteral("Generate authoritative engineering drawings")},
        {QStringLiteral("create_pcb_topology_study"), QStringLiteral("Compare rigid, rigid-flex and PCB-island topologies")},
        {QStringLiteral("derive_mechanical_component_requirements"), QStringLiteral("Derive mechanical components from physical requirements")},
        {QStringLiteral("generate_interaction_structure"), QStringLiteral("Generate component cavities and ribs")},
        {QStringLiteral("apply_electrical_stage"), QStringLiteral("Create industrial schematic")},
        {QStringLiteral("apply_pcb_stage"), QStringLiteral("Create PCB placement draft")},
        {QStringLiteral("extract_component_datasheet"), QStringLiteral("Acquire and extract component datasheet")},
        {QStringLiteral("publish_component_assets"), QStringLiteral("Publish approved component assets")},
        {QStringLiteral("build_electronics_from_datasheets"), QStringLiteral("Build electronics from supplier datasheets")},
        {QStringLiteral("run_engineering_checks"), QStringLiteral("Run engineering verification")},
        {QStringLiteral("commit_configuration"), QStringLiteral("Commit immutable configuration")}
    };
    m_pendingCodexTools.insert(token, PendingCodexTool{tool, arguments});
    if (m_designFlow) m_designFlow->setToolPending(tool, arguments);
    m_chatPanel->presentCodexApproval(
        token, titles.value(tool, tool),
        QStringLiteral("This trusted typed operation will modify the active DesignStudio product. ")
            + QStringLiteral("The baseline remains auditable and missing evidence remains incomplete."),
        arguments);
}

void MainWindow::executeApprovedCodexTool(const QString& token) {
    if (!m_pendingCodexTools.contains(token)) return;
    const PendingCodexTool pending = m_pendingCodexTools.take(token);
    m_statusLabel->setText(QStringLiteral("Codex stage: %1").arg(pending.name));
    m_progressBar->setRange(0, 0);
    m_progressBar->show();
    QApplication::processEvents();
    if (pending.name == QStringLiteral("extract_component_datasheet")) {
        m_progressBar->hide();
        m_chatPanel->runCodexDatasheetExtraction(
            token, pending.arguments.value(QStringLiteral("source")).toString(),
            pending.arguments.value(QStringLiteral("mpn")).toString());
        return;
    }
    if (pending.name == QStringLiteral("publish_component_assets")) {
        m_progressBar->hide();
        m_chatPanel->runCodexComponentPublication(
            token, pending.arguments.value(QStringLiteral("manifest_path")).toString());
        return;
    }
    if (pending.name == QStringLiteral("build_electronics_from_datasheets")) {
        m_progressBar->hide();
        saveForAgents();
        m_chatPanel->runCodexDatasheetProductBuild(
            token, pending.arguments.value(QStringLiteral("intent")).toString());
        return;
    }
    m_activeCodexToolToken = token;
    m_activeCodexToolCancelled = false;
    const QJsonObject result = executeCodexTool(pending.name, pending.arguments);
    m_progressBar->hide();
    const bool cancelled = m_activeCodexToolCancelled;
    m_activeCodexToolToken.clear();
    m_activeCodexToolCancelled = false;
    if (cancelled) {
        // The native host owns the rollback result.  Never replace a
        // rollback_failed/workspace-blocked response with a reassuring local
        // message merely because the Stop button was pressed.
        QJsonObject stopped = result;
        if (!stopped.value(QStringLiteral("cancelled")).toBool()) {
            stopped = toolResult(
                false, QStringLiteral("Stopped by user; last approved revision preserved."),
                QJsonObject{{QStringLiteral("cancelled"), true}});
        }
        m_statusLabel->setText(stopped.value(QStringLiteral("message")).toString());
        if (m_designFlow)
            m_designFlow->setToolResult(pending.name, false,
                stopped.value(QStringLiteral("message")).toString());
        if (m_physicalDesignPanel)
            m_physicalDesignPanel->setOperationResult(pending.name, false, stopped);
        m_chatPanel->completeCodexTool(token, false, jsonText(stopped));
        return;
    }
    const bool ok = result.value(QStringLiteral("ok")).toBool(false);
    if (ok)
        qInfo().noquote() << "DESIGNSTUDIO_STAGE_OK:" << pending.name
                          << result.value(QStringLiteral("message")).toString();
    else
        qWarning().noquote() << "DESIGNSTUDIO_STAGE_FAILED:" << pending.name
                             << result.value(QStringLiteral("message")).toString();
    m_statusLabel->setText(result.value(QStringLiteral("message")).toString());
    if (m_designFlow)
        m_designFlow->setToolResult(pending.name, ok,
            result.value(QStringLiteral("message")).toString());
    if (m_physicalDesignPanel)
        m_physicalDesignPanel->setOperationResult(pending.name, ok, result);
    m_chatPanel->completeCodexTool(token, ok, jsonText(result));
}

QJsonObject MainWindow::executeCodexTool(const QString& tool,
                                         const QJsonObject& arguments) {
    if (tool == QStringLiteral("create_product_workspace")) return createAgentWorkspace(arguments);
    if (tool == QStringLiteral("apply_electrical_stage")) return applyElectricalConcept(arguments);
    if (tool == QStringLiteral("apply_pcb_stage")) return applyPcbConcept(arguments);
    if (tool == QStringLiteral("run_engineering_checks")) return runAgentChecks();
    if (tool == QStringLiteral("commit_configuration")) return commitAgentConfiguration(arguments);
    if (tool == QStringLiteral("create_physical_design_session")
        || tool == QStringLiteral("create_physical_design_session_v2")
        || tool == QStringLiteral("create_guided_physical_design_session")
        || tool == QStringLiteral("generate_constraint_candidates")
        || tool == QStringLiteral("export_physical_test_plan")
        || tool == QStringLiteral("record_physical_observation")
        || tool == QStringLiteral("create_physical_candidate_decision")
        || tool == QStringLiteral("capture_selected_design_region")
        || tool == QStringLiteral("preview_local_redesign")
        || tool == QStringLiteral("commit_local_redesign")
        || tool == QStringLiteral("capture_local_redesign_v2")
        || tool == QStringLiteral("preview_local_redesign_v2")
        || tool == QStringLiteral("commit_local_redesign_v2")
        || tool == QStringLiteral("discard_local_redesign_v2")
        || tool == QStringLiteral("rollback_local_redesign_v2")
        || tool == QStringLiteral("generate_authoritative_drawings")
        || tool == QStringLiteral("create_pcb_topology_study")
        || tool == QStringLiteral("derive_mechanical_component_requirements")) {
        if (!m_completedAgentStages.contains(QStringLiteral("workspace")))
            return toolResult(false,
                QStringLiteral("Create the workspace before starting physical design or local redesign"));
        if (!m_nativeToolHandler)
            return toolResult(false, QStringLiteral("FreeCAD native tool dispatcher is unavailable"));
        const auto workspace = m_agentd->workspace();
        const QString root = QFileInfo(workspace.rootPath).canonicalFilePath();
        if (root.isEmpty())
            return toolResult(false, QStringLiteral("Active workspace root is unavailable"));
        QJsonObject nativeArgs{
            {QStringLiteral("mechanical_path"), workspace.mechanicalPath},
            {QStringLiteral("workspace_root"), root}
        };
        const auto addWorkspaceJsonPath = [&](const QString& argumentName) -> bool {
            const QString requested = arguments.value(argumentName).toString();
            const QString candidate = QFileInfo(QDir(root).filePath(requested)).canonicalFilePath();
            const QString prefix = root + QDir::separator();
            if (requested.isEmpty() || candidate.isEmpty()
                || !candidate.endsWith(QStringLiteral(".json"), Qt::CaseInsensitive)
                || (candidate != root && !candidate.startsWith(prefix)))
                return false;
            nativeArgs.insert(argumentName, candidate);
            return true;
        };
        if (tool == QStringLiteral("create_guided_physical_design_session")) {
            if (!arguments.value(QStringLiteral("capture")).isObject())
                return toolResult(false, QStringLiteral("Guided physical-design capture is incomplete"));
            nativeArgs.insert(QStringLiteral("capture"), arguments.value(QStringLiteral("capture")));
        } else if (tool == QStringLiteral("capture_local_redesign_v2")) {
            nativeArgs.insert(QStringLiteral("intent"), arguments.value(QStringLiteral("intent")));
            nativeArgs.insert(QStringLiteral("permitted_expansion_mm"),
                              arguments.value(QStringLiteral("permitted_expansion_mm")));
            nativeArgs.insert(QStringLiteral("continuity_required"),
                              arguments.value(QStringLiteral("continuity_required")));
            if (arguments.value(QStringLiteral("protected_objects")).isArray())
                nativeArgs.insert(QStringLiteral("protected_objects"),
                                  arguments.value(QStringLiteral("protected_objects")));
            if (arguments.value(QStringLiteral("manufacturing")).isObject())
                nativeArgs.insert(QStringLiteral("manufacturing"),
                                  arguments.value(QStringLiteral("manufacturing")));
            if (!arguments.value(QStringLiteral("redesign_id")).toString().isEmpty())
                nativeArgs.insert(QStringLiteral("redesign_id"),
                                  arguments.value(QStringLiteral("redesign_id")));
        } else if (tool == QStringLiteral("create_physical_design_session")
                   || tool == QStringLiteral("create_physical_design_session_v2")) {
            const bool hasInline = arguments.value(QStringLiteral("session")).isObject();
            const bool hasPath = !arguments.value(QStringLiteral("session_path")).toString().isEmpty();
            if (hasInline == hasPath)
                return toolResult(false,
                    QStringLiteral("Provide exactly one inline physical design session or workspace JSON path"));
            if (hasInline)
                nativeArgs.insert(QStringLiteral("session"), arguments.value(QStringLiteral("session")));
            else if (!addWorkspaceJsonPath(QStringLiteral("session_path")))
                return toolResult(false,
                    QStringLiteral("Physical design session must be an existing workspace-relative JSON file"));
        } else if (tool == QStringLiteral("generate_constraint_candidates")) {
            if (!addWorkspaceJsonPath(QStringLiteral("session_path")))
                return toolResult(false,
                    QStringLiteral("Constraint candidate generation requires an existing workspace v2 session JSON file"));
        } else if (tool == QStringLiteral("create_pcb_topology_study")
                   || tool == QStringLiteral("derive_mechanical_component_requirements")) {
            const QString requested = arguments.value(QStringLiteral("source_path")).toString();
            const QString candidate = QFileInfo(QDir(root).filePath(requested)).canonicalFilePath();
            const QString suffix = QFileInfo(candidate).suffix().toLower();
            const bool permittedSuffix = suffix == QStringLiteral("json")
                || (tool == QStringLiteral("create_pcb_topology_study")
                    && suffix == QStringLiteral("dsproj"));
            if (requested.isEmpty() || candidate.isEmpty() || !permittedSuffix
                || !candidate.startsWith(root + QDir::separator()))
                return toolResult(false,
                    QStringLiteral("Co-design input must be an existing workspace JSON or PCB project file"));
            nativeArgs.insert(QStringLiteral("source_path"), candidate);
            const QString idName = tool == QStringLiteral("create_pcb_topology_study")
                ? QStringLiteral("study_id") : QStringLiteral("requirements_id");
            const QString identifier = arguments.value(idName).toString().trimmed();
            if (!identifier.isEmpty()) nativeArgs.insert(idName, identifier);
        } else if (tool == QStringLiteral("export_physical_test_plan")) {
            if (!addWorkspaceJsonPath(QStringLiteral("session_path"))
                || !arguments.value(QStringLiteral("request")).isObject())
                return toolResult(false, QStringLiteral("Grip-buck export requires a workspace session and structured test plan"));
            nativeArgs.insert(QStringLiteral("request"), arguments.value(QStringLiteral("request")));
        } else if (tool == QStringLiteral("record_physical_observation")) {
            if (!addWorkspaceJsonPath(QStringLiteral("plan_path"))
                || !arguments.value(QStringLiteral("observation")).isObject())
                return toolResult(false, QStringLiteral("Observation requires a workspace test plan and fixed 1–5 form"));
            nativeArgs.insert(QStringLiteral("observation"), arguments.value(QStringLiteral("observation")));
        } else if (tool == QStringLiteral("create_physical_candidate_decision")) {
            if (!addWorkspaceJsonPath(QStringLiteral("plan_path"))
                || !arguments.value(QStringLiteral("observation_paths")).isArray())
                return toolResult(false, QStringLiteral("Decision requires a workspace plan and observation paths"));
            QJsonArray resolvedObservations;
            for (const QJsonValue& value : arguments.value(QStringLiteral("observation_paths")).toArray()) {
                const QString requested = value.toString();
                const QString candidate = QFileInfo(QDir(root).filePath(requested)).canonicalFilePath();
                if (requested.isEmpty() || candidate.isEmpty() || !candidate.endsWith(QStringLiteral(".json"), Qt::CaseInsensitive)
                    || !candidate.startsWith(root + QDir::separator()))
                    return toolResult(false, QStringLiteral("Every observation must be an existing workspace JSON file"));
                resolvedObservations.append(candidate);
            }
            nativeArgs.insert(QStringLiteral("observation_paths"), resolvedObservations);
            for (const QString& name : {QStringLiteral("decision_id"), QStringLiteral("observer"), QStringLiteral("created_utc")}) {
                const QString value = arguments.value(name).toString();
                if (value.isEmpty()) return toolResult(false, QStringLiteral("Physical decision metadata is incomplete"));
                nativeArgs.insert(name, value);
            }
        } else if (tool == QStringLiteral("capture_selected_design_region")) {
            nativeArgs.insert(QStringLiteral("intent"), arguments.value(QStringLiteral("intent")));
            nativeArgs.insert(QStringLiteral("max_expansion_mm"),
                              arguments.value(QStringLiteral("max_expansion_mm")));
            if (arguments.value(QStringLiteral("protected_semantic_ids")).isArray())
                nativeArgs.insert(QStringLiteral("protected_semantic_ids"),
                                  arguments.value(QStringLiteral("protected_semantic_ids")));
            if (!arguments.value(QStringLiteral("physical_design_session_path")).toString().isEmpty()) {
                if (!addWorkspaceJsonPath(QStringLiteral("physical_design_session_path")))
                    return toolResult(false,
                        QStringLiteral("Physical design session must be an existing workspace-relative JSON file"));
            }
        } else if (tool == QStringLiteral("generate_authoritative_drawings")) {
            const QString sourceId = arguments.value(QStringLiteral("source_semantic_id")).toString();
            const QString outputDirectory = arguments.value(QStringLiteral("output_directory")).toString();
            if (sourceId.isEmpty() || outputDirectory.isEmpty())
                return toolResult(false, QStringLiteral("Drawing generation requires source_semantic_id and output_directory"));
            nativeArgs.insert(QStringLiteral("source_semantic_id"), sourceId);
            nativeArgs.insert(QStringLiteral("output_directory"), outputDirectory);
            if (arguments.value(QStringLiteral("sections")).isArray())
                nativeArgs.insert(QStringLiteral("sections"), arguments.value(QStringLiteral("sections")));
            if (arguments.value(QStringLiteral("selected_faces")).isArray())
                nativeArgs.insert(QStringLiteral("selected_faces"), arguments.value(QStringLiteral("selected_faces")));
            if (arguments.value(QStringLiteral("selected_edges")).isArray())
                nativeArgs.insert(QStringLiteral("selected_edges"), arguments.value(QStringLiteral("selected_edges")));
            if (!arguments.value(QStringLiteral("package_id")).toString().isEmpty())
                nativeArgs.insert(QStringLiteral("package_id"), arguments.value(QStringLiteral("package_id")));
        } else {
            const QString pathArgument = (tool == QStringLiteral("preview_local_redesign")
                                          || tool == QStringLiteral("preview_local_redesign_v2"))
                ? QStringLiteral("redesign_path") : QStringLiteral("preview_receipt_path");
            const QString v2PathArgument = tool == QStringLiteral("rollback_local_redesign_v2")
                ? QStringLiteral("commit_receipt_path") : pathArgument;
            if (tool == QStringLiteral("rollback_local_redesign_v2")) {
                const QString requested = arguments.value(v2PathArgument).toString();
                const QString candidate = QFileInfo(QDir(root).filePath(requested)).canonicalFilePath();
                const QString prefix = root + QDir::separator();
                if (requested.isEmpty() || candidate.isEmpty() || !candidate.endsWith(QStringLiteral(".json"), Qt::CaseInsensitive)
                    || (candidate != root && !candidate.startsWith(prefix)))
                    return toolResult(false, QStringLiteral("Commit receipt must be an existing workspace-relative JSON file"));
                nativeArgs.insert(v2PathArgument, candidate);
            } else if (!addWorkspaceJsonPath(pathArgument))
                return toolResult(false,
                    QStringLiteral("Local redesign input must be an existing workspace-relative JSON file"));
            if (tool == QStringLiteral("preview_local_redesign") || tool == QStringLiteral("preview_local_redesign_v2")) {
                const bool hasProgram = arguments.value(QStringLiteral("program")).isObject();
                const bool hasCandidate = !arguments.value(
                    QStringLiteral("candidate_command_id")).toString().isEmpty();
                if (hasProgram != hasCandidate)
                    return toolResult(false,
                        QStringLiteral("Preview requires both the typed program and candidate command ID"));
                if (hasProgram) {
                    nativeArgs.insert(QStringLiteral("program"),
                                      arguments.value(QStringLiteral("program")));
                    nativeArgs.insert(QStringLiteral("candidate_command_id"),
                                      arguments.value(QStringLiteral("candidate_command_id")));
                }
                if (tool == QStringLiteral("preview_local_redesign_v2")
                    && arguments.value(QStringLiteral("candidate_faces")).isArray())
                    nativeArgs.insert(QStringLiteral("candidate_faces"),
                                      arguments.value(QStringLiteral("candidate_faces")));
            }
        }
        const QJsonObject nativeResult = m_nativeToolHandler(tool, nativeArgs);
        if (!nativeResult.value(QStringLiteral("ok")).toBool(false)) return nativeResult;
        m_model->setModified(true);
        return toolResult(true, nativeResult.value(QStringLiteral("message")).toString(), nativeResult);
    }
    if (tool == QStringLiteral("generate_interaction_structure")) {
        if (!m_completedAgentStages.contains(QStringLiteral("mechanical")))
            return toolResult(false, QStringLiteral("Approve the mechanical contract before generating supports"));
        if (!m_nativeToolHandler)
            return toolResult(false, QStringLiteral("FreeCAD native tool dispatcher is unavailable"));
        const auto workspace = m_agentd->workspace();
        const QString root = QFileInfo(workspace.rootPath).canonicalFilePath();
        const QString requested = arguments.value(QStringLiteral("spec_path")).toString();
        const QString candidate = QFileInfo(QDir(workspace.rootPath).filePath(requested))
                                      .canonicalFilePath();
        const QString rootPrefix = root + QDir::separator();
        if (root.isEmpty() || candidate.isEmpty()
            || (candidate != root && !candidate.startsWith(rootPrefix)))
            return toolResult(false,
                QStringLiteral("Support specification must be an existing JSON file inside the active workspace"));
        QJsonObject nativeArgs{
            {QStringLiteral("mechanical_path"), workspace.mechanicalPath},
            {QStringLiteral("workspace_root"), root},
            {QStringLiteral("spec_path"), candidate}
        };
        const QString refinement = arguments.value(
            QStringLiteral("refinement")).toString(QStringLiteral("local"));
        if (refinement != QStringLiteral("local")
            && refinement != QStringLiteral("cloud"))
            return toolResult(false, QStringLiteral("refinement must be local or cloud"));
        if (refinement == QStringLiteral("cloud")) {
            QFile input(candidate);
            if (!input.open(QIODevice::ReadOnly) || input.size() > 64 * 1024 * 1024)
                return toolResult(false,
                    QStringLiteral("Support specification cannot be read or exceeds 64 MiB"));
            QJsonParseError parseError;
            const QJsonDocument specificationDocument =
                QJsonDocument::fromJson(input.readAll(), &parseError);
            if (parseError.error != QJsonParseError::NoError
                || !specificationDocument.isObject())
                return toolResult(false, QStringLiteral("Support specification is invalid JSON"));
            const QUrl gateway(qEnvironmentVariable(
                "DS_AI_GATEWAY_URL", QStringLiteral("http://127.0.0.1:8000")));
            const designstudio::SupportJobClientResult cloud =
                designstudio::SupportJobClient::submitAndWait(
                    gateway, specificationDocument.object());
            if (!cloud.ok)
                return toolResult(false,
                    QStringLiteral("Cloud support refinement failed: %1").arg(cloud.error));
            const QUuid jobId(cloud.jobId);
            if (jobId.isNull())
                return toolResult(false, QStringLiteral("Cloud support job ID is invalid"));
            const QString resultRelative = QStringLiteral("contracts/support-result-%1.json")
                .arg(jobId.toString(QUuid::WithoutBraces));
            const QString resultPath = QDir(root).filePath(resultRelative);
            QSaveFile resultFile(resultPath);
            const QByteArray resultBytes =
                QJsonDocument(cloud.result).toJson(QJsonDocument::Indented);
            if (!resultFile.open(QIODevice::WriteOnly)
                || resultFile.write(resultBytes) != resultBytes.size()
                || !resultFile.commit())
                return toolResult(false,
                    QStringLiteral("Cloud result was received but could not be persisted"));
            nativeArgs.insert(QStringLiteral("result_path"), resultPath);
        }
        const QJsonObject nativeResult = m_nativeToolHandler(tool, nativeArgs);
        if (!nativeResult.value(QStringLiteral("ok")).toBool(false)) return nativeResult;
        m_model->setModified(true);
        return toolResult(true,
            refinement == QStringLiteral("cloud")
                ? QStringLiteral("MI300X support candidate digest-checked and reconstructed as exact FreeCAD B-Rep; collision, wall-thickness, structural and manufacturability checks remain required before release")
                : QStringLiteral("Protected component cavities and deterministic ribs generated locally; collision, wall-thickness, structural and manufacturability checks remain required before release"),
            nativeResult);
    }
    if (tool == QStringLiteral("apply_mechanical_cad_program")) {
        if (!m_completedAgentStages.contains(QStringLiteral("workspace")))
            return toolResult(false, QStringLiteral("Create the workspace before applying a mechanical CAD program"));
        if (!m_nativeToolHandler)
            return toolResult(false, QStringLiteral("FreeCAD native tool dispatcher is unavailable"));
        const auto workspace = m_agentd->workspace();
        const QString root = QFileInfo(workspace.rootPath).canonicalFilePath();
        QJsonObject nativeArgs{
            {QStringLiteral("mechanical_path"), workspace.mechanicalPath},
            {QStringLiteral("workspace_root"), root}
        };
        if (arguments.value(QStringLiteral("program")).isObject()) {
            nativeArgs.insert(QStringLiteral("program"), arguments.value(QStringLiteral("program")));
        } else {
            const QString requested = arguments.value(QStringLiteral("program_path")).toString();
            const QString candidate = QFileInfo(QDir(workspace.rootPath).filePath(requested))
                                          .canonicalFilePath();
            const QString rootPrefix = root + QDir::separator();
            if (root.isEmpty() || candidate.isEmpty()
                || !candidate.endsWith(QStringLiteral(".json"), Qt::CaseInsensitive)
                || (candidate != root && !candidate.startsWith(rootPrefix)))
                return toolResult(false,
                    QStringLiteral("Mechanical CAD program must be an existing workspace-relative JSON file or inline object"));
            nativeArgs.insert(QStringLiteral("program_path"), candidate);
        }
        const QJsonObject nativeResult = m_nativeToolHandler(tool, nativeArgs);
        if (!nativeResult.value(QStringLiteral("ok")).toBool(false)) return nativeResult;
        m_model->setModified(true);
        return toolResult(true,
            QStringLiteral("Typed mechanical CAD program applied as editable FreeCAD features."),
            nativeResult);
    }
    if (tool == QStringLiteral("apply_mechanical_stage")) {
        if (!m_completedAgentStages.contains(QStringLiteral("workspace")))
            return toolResult(false, QStringLiteral("Create the workspace before the mechanical stage"));
        if (!m_nativeToolHandler)
            return toolResult(false, QStringLiteral("FreeCAD native tool dispatcher is unavailable"));
        QJsonObject nativeArgs = arguments;
        nativeArgs.insert(QStringLiteral("mechanical_path"), m_agentd->workspace().mechanicalPath);
        nativeArgs.insert(QStringLiteral("family"), m_activeApplicationFamily);
        const QJsonObject nativeResult = m_nativeToolHandler(tool, nativeArgs);
        if (!nativeResult.value(QStringLiteral("ok")).toBool(false)) return nativeResult;

        const QJsonObject nativeData = nativeResult.value(QStringLiteral("data")).toObject();
        const QJsonArray legalSize = nativeData.value(QStringLiteral("legal_pcb_size_mm")).toArray();
        const QJsonArray legalOrigin = nativeData.value(QStringLiteral("legal_pcb_origin_mm")).toArray();
        const double boardW = qMin(100.0, legalSize.size() == 3
            ? legalSize[0].toDouble() : arguments.value(QStringLiteral("width_mm")).toDouble() - 10.0);
        const double boardH = qMin(60.0, legalSize.size() == 3
            ? legalSize[1].toDouble() : arguments.value(QStringLiteral("depth_mm")).toDouble() - 10.0);
        m_model->boardWidthMm = boardW;
        m_model->boardHeightMm = boardH;
        m_model->boardOutline = {{0, 0}, {boardW, 0}, {boardW, boardH}, {0, boardH}};
        QJsonObject mechanicalContract{
            {QStringLiteral("schema"), QStringLiteral("design-studio.mechanical-contract/1")},
            {QStringLiteral("contract_id"), QStringLiteral("freecad:%1-enclosure")
                .arg(m_activeApplicationFamily)},
            {QStringLiteral("revision"), 1}, {QStringLiteral("status"), QStringLiteral("locked")},
            {QStringLiteral("units"), QStringLiteral("mm")},
            {QStringLiteral("source"), QJsonObject{{QStringLiteral("document"),
                m_agentd->workspace().mechanicalPath}, {QStringLiteral("object"),
                QStringLiteral("DesignStudioEnclosure")}}},
            {QStringLiteral("frame_to_world"), QJsonObject{
                {QStringLiteral("origin_mm"), legalOrigin.size() == 3 ? legalOrigin
                    : QJsonArray{0.0, 0.0, 5.5}},
                {QStringLiteral("x_axis"), QJsonArray{1.0, 0.0, 0.0}},
                {QStringLiteral("y_axis"), QJsonArray{0.0, 1.0, 0.0}},
                {QStringLiteral("z_axis"), QJsonArray{0.0, 0.0, 1.0}}}},
            {QStringLiteral("design_volume_mm"), QJsonObject{
                {QStringLiteral("min"), QJsonArray{0.0, 0.0, 0.0}},
                {QStringLiteral("max"), QJsonArray{
                    arguments.value(QStringLiteral("width_mm")).toDouble(),
                    arguments.value(QStringLiteral("depth_mm")).toDouble(),
                    arguments.value(QStringLiteral("height_mm")).toDouble()}},
            }},
            {QStringLiteral("board"), QJsonObject{
                {QStringLiteral("outline_pts"), QJsonArray{QJsonArray{0, 0}, QJsonArray{boardW, 0},
                    QJsonArray{boardW, boardH}, QJsonArray{0, boardH}}},
                {QStringLiteral("cutouts"), QJsonArray{}},
                {QStringLiteral("thickness_mm"), 1.6},
                {QStringLiteral("z_range_mm"), QJsonArray{0.0, 1.6}},
                {QStringLiteral("mounting_holes"), QJsonArray{}},
                {QStringLiteral("height_zones"), QJsonArray{}},
                {QStringLiteral("cooling_zones"), QJsonArray{}},
                {QStringLiteral("service_clearances"), QJsonArray{}}}},
            {QStringLiteral("fixed_items"), QJsonArray{}},
            {QStringLiteral("connector_locations"), QJsonArray{}}
        };
        const QByteArray contractMaterial = QJsonDocument(mechanicalContract)
            .toJson(QJsonDocument::Compact);
        mechanicalContract.insert(QStringLiteral("contract_digest"), QString::fromLatin1(
            QCryptographicHash::hash(contractMaterial, QCryptographicHash::Sha256).toHex()));
        m_model->mechanicalContract = mechanicalContract;
        if (m_enclosureConcept) {
            m_enclosureConcept->setPlacementDocumentBinding(
                mechanicalContract.value(QStringLiteral("contract_id")).toString(),
                mechanicalContract.value(QStringLiteral("revision")).toInteger(),
                mechanicalContract.value(QStringLiteral("contract_digest")).toString());
            m_enclosureConcept->setSupportDesignVolumeMm(QVector3D(
                float(arguments.value(QStringLiteral("width_mm")).toDouble()),
                float(arguments.value(QStringLiteral("depth_mm")).toDouble()),
                float(arguments.value(QStringLiteral("height_mm")).toDouble())));
        }
        const QString contractRelative = QStringLiteral("contracts/mechanical-contract.json");
        const QByteArray persistedContract = QJsonDocument(mechanicalContract)
            .toJson(QJsonDocument::Indented);
        QSaveFile contractFile(QDir(m_agentd->workspace().rootPath).filePath(contractRelative));
        if (!contractFile.open(QIODevice::WriteOnly)
            || contractFile.write(persistedContract) != persistedContract.size()
            || !contractFile.commit())
            return toolResult(false, QStringLiteral("Could not persist the locked mechanical contract"));
        QFile manifestInput(m_agentd->workspace().manifestPath);
        if (manifestInput.open(QIODevice::ReadOnly)) {
            QJsonObject manifest = QJsonDocument::fromJson(manifestInput.readAll()).object();
            QJsonArray contracts = manifest.value(QStringLiteral("contracts")).toArray();
            if (!contracts.contains(contractRelative)) contracts.append(contractRelative);
            manifest.insert(QStringLiteral("contracts"), contracts);
            QSaveFile manifestOutput(m_agentd->workspace().manifestPath);
            if (manifestOutput.open(QIODevice::WriteOnly)) {
                manifestOutput.write(QJsonDocument(manifest).toJson(QJsonDocument::Indented));
                manifestOutput.commit();
            }
        }
        m_model->verificationRequirements.requireEnclosureEvidence = true;
        m_model->verificationRequirements.enclosureEvidencePath =
            QStringLiteral("../") + contractRelative;
        m_model->verificationRequirements.enclosureEvidenceSha256 = QString::fromLatin1(
            QCryptographicHash::hash(persistedContract, QCryptographicHash::Sha256).toHex());
        m_model->setModified(true);
        if (!m_model->saveToFile(m_model->filePath()))
            return toolResult(false, QStringLiteral("Mechanical document built, but electronics evidence could not be saved: %1")
                                         .arg(m_model->lastError()));
        if (!m_completedAgentStages.contains(QStringLiteral("mechanical")))
            m_completedAgentStages.append(QStringLiteral("mechanical"));
        m_nativeToolHandler(QStringLiteral("show_workspace_view"),
                            QJsonObject{{QStringLiteral("view"), QStringLiteral("mechanical")}});
        return toolResult(true,
            QStringLiteral("Mechanical stage built and shown: editable enclosure and locked %1x%2 mm PCB volume; component geometry will come from extracted footprints and optional STEP models")
                .arg(boardW).arg(boardH), nativeResult);
    }
    return toolResult(false, QStringLiteral("tool is not implemented"));
}

QJsonObject MainWindow::createAgentWorkspace(const QJsonObject& arguments) {
    const auto current = m_agentd ? m_agentd->workspace() : AgentdClient::WorkspaceDocuments{};
    if (!current.rootPath.isEmpty() && QFileInfo::exists(current.manifestPath)) {
        if (!m_completedAgentStages.contains(QStringLiteral("workspace")))
            m_completedAgentStages.prepend(QStringLiteral("workspace"));
        return toolResult(true, QStringLiteral("An authoritative workspace is already open; continuing it instead of creating a duplicate"),
            QJsonObject{{QStringLiteral("workspace_root"), current.rootPath},
                        {QStringLiteral("manifest"), current.manifestPath},
                        {QStringLiteral("reused"), true}});
    }
    QString productName = arguments.value(QStringLiteral("name")).toString().trimmed();
    if (productName.contains(QLatin1Char('/')) || productName.contains(QLatin1Char('\\'))) {
        productName = QFileInfo(productName).completeBaseName();
        if (productName.endsWith(QStringLiteral(".dsworkspace"), Qt::CaseInsensitive))
            productName.chop(QStringLiteral(".dsworkspace").size());
    }
    if (productName.isEmpty()) productName = QStringLiteral("Industrial product");
    const QString productDescription = arguments.value(QStringLiteral("description")).toString().trimmed();
    m_activeProductName = productName;
    m_activeProductDescription = productDescription;
    m_activeApplicationFamily = designstudio::classifyApplicationFamily(
        productName + QLatin1Char(' ') + productDescription);
    m_activeAxisCount = designstudio::axisCountFromPrompt(
        productName + QLatin1Char(' ') + productDescription,
        m_activeApplicationFamily == QStringLiteral("robotic_joint_capstone") ? 3 : 1);
    QString slug = productName.toLower();
    slug.replace(QRegularExpression(QStringLiteral("[^a-z0-9]+")), QStringLiteral("-"));
    slug.remove(QRegularExpression(QStringLiteral("^-+|-+$")));
    if (slug.isEmpty()) slug = QStringLiteral("industrial-product");
    emit workspaceLifecycleChanged(
        QStringLiteral("creating"),
        QStringLiteral("starting one-shot project/create"));
    QString parent = qEnvironmentVariable("DESIGNSTUDIO_WORKSPACE_PARENT").trimmed();
    if (parent.isEmpty())
        parent = QDir::home().filePath(QStringLiteral("DesignStudio"));
    else
        parent = QFileInfo(parent).absoluteFilePath();
    if (!QDir().mkpath(parent))
        return toolResult(false, QStringLiteral("Cannot create workspace parent: %1").arg(parent));
    QString root = QDir(parent).filePath(slug + QStringLiteral(".dsworkspace"));
    if (QFileInfo::exists(root)) root = QDir(parent).filePath(
        slug + QDateTime::currentDateTime().toString(QStringLiteral("-yyyyMMdd-hhmmss-zzz-"))
        + QUuid::createUuid().toString(QUuid::WithoutBraces).left(8)
        + QStringLiteral(".dsworkspace"));
    const QJsonObject creation = invokeAgentdOnce(
        QStringLiteral("project/create"),
        QJsonObject{{QStringLiteral("workspace_root"), root},
                    {QStringLiteral("name"), productName},
                    {QStringLiteral("description"), productDescription}},
        QStringLiteral("project:create"));
    if (!creation.value(QStringLiteral("ok")).toBool(false)) {
        const QString message = QStringLiteral("project/create failed: %1")
            .arg(creation.value(QStringLiteral("message")).toString());
        emit workspaceLifecycleChanged(QStringLiteral("offline"), message);
        return toolResult(false, message);
    }
    const QJsonObject paths = creation.value(QStringLiteral("data")).toObject();
    const QString electronicsPath = paths.value(QStringLiteral("electronics_path")).toString();
    const QString mechanicalPath = paths.value(QStringLiteral("mechanical_path")).toString();
    m_model->clear();
    if (!m_model->saveToFile(electronicsPath)) {
        QDir(root).removeRecursively();
        const QString message = QStringLiteral("electronics document creation failed: %1")
            .arg(m_model->lastError());
        emit workspaceLifecycleChanged(QStringLiteral("offline"), message);
        return toolResult(false, message);
    }
    if (!m_nativeToolHandler) {
        QDir(root).removeRecursively();
        const QString message =
            QStringLiteral("FreeCAD document creation failed: native dispatcher is unavailable");
        emit workspaceLifecycleChanged(QStringLiteral("offline"), message);
        return toolResult(false, message);
    }
    const QJsonObject native = m_nativeToolHandler(
        QStringLiteral("create_product_workspace"),
        QJsonObject{{QStringLiteral("mechanical_path"), mechanicalPath},
                    {QStringLiteral("name"), productName},
                    {QStringLiteral("family"), m_activeApplicationFamily}});
    if (!native.value(QStringLiteral("ok")).toBool(false)) {
        QDir(root).removeRecursively();
        const QString message = QStringLiteral("FreeCAD document creation failed: %1")
            .arg(native.value(QStringLiteral("message")).toString());
        emit workspaceLifecycleChanged(QStringLiteral("offline"), message);
        return toolResult(false, message);
    }
    QString error;
    if (!openWorkspace(root, &error)) {
        if (m_agentd) m_agentd->stop();
        QDir(root).removeRecursively();
        const QString message =
            QStringLiteral("workspace open failed: %1").arg(error);
        emit workspaceLifecycleChanged(QStringLiteral("offline"), message);
        return toolResult(false, message);
    }
    m_completedAgentStages = {QStringLiteral("workspace")};
    return toolResult(true, QStringLiteral("Unified workspace created and opened"),
        QJsonObject{{QStringLiteral("workspace_root"), root},
                    {QStringLiteral("manifest"), QDir(root).filePath(QStringLiteral("manifest.json"))},
                    {QStringLiteral("mechanical"), mechanicalPath},
                    {QStringLiteral("electronics"), electronicsPath},
                    {QStringLiteral("authority"), QStringLiteral("FreeCAD geometry + electronics document + Rust product graph")}});
}

QJsonObject MainWindow::applyElectricalConcept(const QJsonObject& arguments) {
    if (!m_completedAgentStages.contains(QStringLiteral("mechanical")))
        return toolResult(false, QStringLiteral("Approve the mechanical contract before electronics"));
    const double minimum = arguments.value(QStringLiteral("input_min_v")).toDouble();
    const double maximum = arguments.value(QStringLiteral("input_max_v")).toDouble();
    if (minimum >= maximum)
        return toolResult(false, QStringLiteral("input_min_v must be below input_max_v"));

    QString requestedFamily = arguments.value(QStringLiteral("family")).toString().trimmed();
    if (requestedFamily.isEmpty()) requestedFamily = m_activeApplicationFamily;
    if (m_activeApplicationFamily == QStringLiteral("custom_concept"))
        m_activeApplicationFamily = requestedFamily;
    else if (requestedFamily != m_activeApplicationFamily)
        return toolResult(false, QStringLiteral("Electrical family '%1' does not match workspace family '%2'; refusing to generate the wrong product")
            .arg(requestedFamily, m_activeApplicationFamily));

    designstudio::ApplicationConceptRequest request;
    request.family = m_activeApplicationFamily;
    request.inputMinV = minimum;
    request.inputMaxV = maximum;
    request.interfaceName = arguments.value(QStringLiteral("interface")).toString();
    request.axisCount = arguments.value(QStringLiteral("axes")).toInt(m_activeAxisCount);
    request.axisCurrentA = arguments.value(QStringLiteral("motor_current_a")).toDouble(2.0);
    const designstudio::ApplicationConceptResult conceptResult =
        designstudio::buildApplicationConcept(*m_model, request);
    if (!conceptResult.ok) return toolResult(false, conceptResult.error,
        QJsonObject{{QStringLiteral("family"), request.family}});
    m_activeInputMinV = minimum;
    m_activeInputMaxV = maximum;
    m_activeInterface = request.interfaceName;
    m_activeAxisCount = conceptResult.axisCount > 0 ? conceptResult.axisCount : request.axisCount;
    m_activeAxisCurrentA = request.axisCurrentA;
    m_model->setModified(true);
    if (!m_model->saveToFile(m_model->filePath()))
        return toolResult(false, QStringLiteral("Electrical model could not be saved: %1")
                                     .arg(m_model->lastError()));
    refreshEngineeringViews();
    navigateTo(m_schView);
    if (!m_completedAgentStages.contains(QStringLiteral("electrical")))
        m_completedAgentStages.append(QStringLiteral("electrical"));
    return toolResult(true,
        QStringLiteral("Electrical concept shown for %1: %2; %3 component roles await automatic exact-MPN datasheet extraction")
            .arg(m_activeApplicationFamily, conceptResult.summary)
            .arg(m_model->unresolvedComponents.size()),
        QJsonObject{{QStringLiteral("components"), m_model->footprints.size()},
                    {QStringLiteral("nets"), m_model->nets.size()},
                    {QStringLiteral("family"), m_activeApplicationFamily},
                    {QStringLiteral("axes"), m_activeAxisCount},
                    {QStringLiteral("evidence_gate"), QStringLiteral("incomplete")}});
}

QJsonObject MainWindow::applyPcbConcept(const QJsonObject& arguments) {
    if (!m_completedAgentStages.contains(QStringLiteral("electrical")))
        return toolResult(false, QStringLiteral("Approve the electrical stage before PCB layout"));
    const double width = arguments.value(QStringLiteral("width_mm")).toDouble();
    const double height = arguments.value(QStringLiteral("height_mm")).toDouble();
    const QString placementMode = arguments.value(QStringLiteral("placement_mode"))
                                      .toString(QStringLiteral("draft")).trimmed().toLower();
    if (placementMode != QStringLiteral("draft")
        && placementMode != QStringLiteral("optimized"))
        return toolResult(false, QStringLiteral("placement_mode must be draft or optimized"));
    const bool draft = placementMode == QStringLiteral("draft");
    const bool provisional = std::any_of(m_model->footprints.cbegin(),
        m_model->footprints.cend(), [](const ProjFootprint& footprint) {
            return footprint.mpn.startsWith(QStringLiteral("PROVISIONAL-"));
        });
    if (!draft && provisional)
        return toolResult(false,
            QStringLiteral("Physical placement and routing require datasheet-derived footprints; run build_electronics_from_datasheets first"),
            QJsonObject{{QStringLiteral("next_action"),
                QStringLiteral("build_electronics_from_datasheets")}});
    if (!draft && (width > m_model->boardWidthMm + 0.01
                   || height > m_model->boardHeightMm + 0.01))
        return toolResult(false, QStringLiteral("PCB exceeds the locked mechanical contract; retry only apply_pcb_stage using the allowed dimensions"),
            QJsonObject{{QStringLiteral("requested_width_mm"), width},
                        {QStringLiteral("requested_height_mm"), height},
                        {QStringLiteral("allowed_width_mm"), m_model->boardWidthMm},
                        {QStringLiteral("allowed_height_mm"), m_model->boardHeightMm},
                        {QStringLiteral("next_action"), QStringLiteral("retry apply_pcb_stage; do not recreate workspace or repeat mechanical/electrical stages")}});
    m_model->boardWidthMm = width;
    m_model->boardHeightMm = height;
    m_model->boardOutline = {{0, 0}, {width, 0}, {width, height}, {0, height}};
    m_model->copperLayers = arguments.value(QStringLiteral("layers")).toInt();
    m_model->layerPolicies.clear();
    m_model->copperZones.clear();
    for (int layer = 0; layer < m_model->copperLayers; ++layer) {
        ProjLayerPolicy policy;
        policy.layer = layer;
        const bool groundPlane = m_model->copperLayers >= 4 && layer == 1;
        const bool powerPlane = m_model->copperLayers >= 4 && layer == 2;
        policy.name = layer == 0 ? QStringLiteral("Top signal")
                    : layer == m_model->copperLayers - 1 ? QStringLiteral("Bottom signal")
                    : groundPlane ? QStringLiteral("Ground plane")
                    : powerPlane ? QStringLiteral("Power plane")
                    : QStringLiteral("Inner signal %1").arg(layer);
        policy.role = (groundPlane || powerPlane) ? QStringLiteral("plane") : QStringLiteral("signal");
        policy.allowRouting = policy.role == QStringLiteral("signal");
        policy.preferredDirection = layer == 0 ? QStringLiteral("horizontal")
                                  : layer == m_model->copperLayers - 1 ? QStringLiteral("vertical")
                                                                      : QStringLiteral("any");
        policy.source = QStringLiteral("mechanical-contract + industrial-concept");
        m_model->layerPolicies.append(policy);
    }
    if (m_model->copperLayers >= 4 && width > 1.0 && height > 1.0) {
        auto netId = [this](const QStringList& preferred) {
            for (const QString& name : preferred)
                for (const auto& net : m_model->nets)
                    if (net.name == name) return net.id;
            return -1;
        };
        const int ground = netId({QStringLiteral("GND_POWER"), QStringLiteral("GND_ISO"),
                                  QStringLiteral("GND_FIELD"), QStringLiteral("GND")});
        const int power = netId({QStringLiteral("VIN_PROTECTED"), QStringLiteral("VIN_24V"),
                                 QStringLiteral("5V_ISO"), QStringLiteral("5V")});
        auto addPlane = [this, width, height](int id, const QString& name, int net, int layer) {
            if (net < 0) return;
            ProjCopperZone zone;
            zone.id = id; zone.name = name; zone.netId = net; zone.layer = layer;
            zone.clearanceMm = m_model->pcbRules.defaultClearanceMm;
            zone.minIslandAreaMm2 = 2.0;
            zone.requireConnection = true;
            zone.source = QStringLiteral("application-concept");
            zone.sourceRevision = QStringLiteral("1");
            zone.points = {{0.5, 0.5}, {width - 0.5, 0.5},
                           {width - 0.5, height - 0.5}, {0.5, height - 0.5}};
            m_model->copperZones.append(zone);
        };
        addPlane(1, QStringLiteral("Ground plane"), ground, 1);
        addPlane(2, QStringLiteral("Power plane"), power, 2);
    }
    m_model->traces.clear();
    m_model->vias.clear();
    if (draft) {
        const QString containmentError = m_model->legalizeFootprints();
        if (!containmentError.isEmpty())
            return toolResult(false,
                QStringLiteral("PCB draft cannot contain every complete component courtyard: %1")
                    .arg(containmentError),
                QJsonObject{{QStringLiteral("required_action"),
                    QStringLiteral("increase the substrate dimensions or resolve locked placements")}});
        m_model->placementState = QJsonObject{
            {QStringLiteral("mode"), QStringLiteral("draft")},
            {QStringLiteral("status"), QStringLiteral("awaiting_optimization")},
            {QStringLiteral("routing"), QStringLiteral("deferred")},
            {QStringLiteral("reason"), QStringLiteral("substrate containment passed; connectivity, overlap, and rotation optimization is pending")}
        };
        m_model->setModified(true);
        if (!m_model->saveToFile(m_model->filePath()))
            return toolResult(false, QStringLiteral("PCB draft could not be saved: %1")
                                         .arg(m_model->lastError()));
        m_completedAgentStages.removeAll(QStringLiteral("pcb"));
        if (!m_completedAgentStages.contains(QStringLiteral("pcb-draft")))
            m_completedAgentStages.append(QStringLiteral("pcb-draft"));
        refreshEngineeringViews();
        navigateTo(m_pcbCanvas);
        m_pcbCanvas->fitToBoard();
        return toolResult(true,
            QStringLiteral("PCB draft shown: %1x%2 mm, %3 layers, %4 components; every complete courtyard is inside the substrate. Run /layout to optimize connectivity, overlap, and rotation before routing.")
                .arg(width).arg(height).arg(m_model->copperLayers)
                .arg(m_model->footprints.size()),
            QJsonObject{{QStringLiteral("placement_mode"), QStringLiteral("draft")},
                        {QStringLiteral("routing"), QStringLiteral("deferred")},
                        {QStringLiteral("substrate_containment"), QStringLiteral("passed")},
                        {QStringLiteral("optimization"), QStringLiteral("required")},
                        {QStringLiteral("gate"), QStringLiteral("draft-only")} });
    }
    const QString placementError = m_model->legalizeFootprints();
    if (!placementError.isEmpty())
        return toolResult(false, QStringLiteral("PCB placement is outside the substrate after optimization: %1").arg(placementError));
    m_model->placementState = QJsonObject{
        {QStringLiteral("mode"), QStringLiteral("optimized")},
        {QStringLiteral("status"), QStringLiteral("legalization_passed")},
        {QStringLiteral("routing"), QStringLiteral("permitted")}
    };
    RouteResult route;
    if (m_core && m_core->isLoaded()) {
        route = m_core->autoRoute(m_model);
        if (route.ok) {
            m_model->traces += route.newTraces;
            m_model->vias += route.newVias;
        }
    } else {
        route.message = QStringLiteral("native router unavailable; routing evidence incomplete");
    }
    m_model->setModified(true);
    if (!m_model->saveToFile(m_model->filePath()))
        return toolResult(false, QStringLiteral("PCB model could not be saved: %1")
                                     .arg(m_model->lastError()));
    refreshEngineeringViews();
    navigateTo(m_pcbCanvas);
    m_pcbCanvas->fitToBoard();
    m_completedAgentStages.removeAll(QStringLiteral("pcb-draft"));
    if (!m_completedAgentStages.contains(QStringLiteral("pcb")))
        m_completedAgentStages.append(QStringLiteral("pcb"));
    return toolResult(true,
        QStringLiteral("PCB shown: %1x%2 mm, %3 layers, %4 components; %5")
            .arg(width).arg(height).arg(m_model->copperLayers)
            .arg(m_model->footprints.size()).arg(route.message),
        QJsonObject{{QStringLiteral("route_ok"), route.ok},
                    {QStringLiteral("routed_connections"), route.routedConnections},
                    {QStringLiteral("unrouted_connections"), route.unroutedConnections},
                    {QStringLiteral("disconnected_nets"), QStringLiteral("pending connectivity check")},
                    {QStringLiteral("drc_failures"), QStringLiteral("pending DRC")},
                    {QStringLiteral("segments"), route.newTraces.size()},
                    {QStringLiteral("vias"), route.newVias.size()},
                    {QStringLiteral("gate"), route.ok ? QStringLiteral("pending DRC")
                                                      : QStringLiteral("incomplete")}});
}

QJsonObject MainWindow::runAgentChecks() {
    if (!m_completedAgentStages.contains(QStringLiteral("pcb")))
        return toolResult(false, QStringLiteral("Create the PCB before verification"));
    if (!m_core || !m_core->isLoaded())
        return toolResult(false, QStringLiteral("Native verification engine is unavailable"));
    QJsonObject report = m_core->buildVerificationReport(m_model);
    report.insert(QStringLiteral("schema"), QStringLiteral("design-studio.verification/1"));
    report.insert(QStringLiteral("created_utc"), QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs));
    const int provisionalCount = std::count_if(
        m_model->footprints.cbegin(), m_model->footprints.cend(),
        [](const ProjFootprint& footprint) {
            return footprint.mpn.startsWith(QStringLiteral("PROVISIONAL-"));
        });
    const int unresolvedCount = m_model->unresolvedComponents.size() + provisionalCount;
    const bool componentEvidenceComplete = unresolvedCount == 0;
    report.insert(QStringLiteral("component_evidence"), QJsonObject{
        {QStringLiteral("status"), componentEvidenceComplete
            ? QStringLiteral("pass") : QStringLiteral("incomplete")},
        {QStringLiteral("unresolved_count"), unresolvedCount},
        {QStringLiteral("reason"), componentEvidenceComplete
            ? QStringLiteral("all placed SMT/THT footprints have automatic datasheet extraction records")
            : QStringLiteral("one or more architecture roles still await automatic datasheet extraction")}});
    const QString root = m_agentd->workspace().rootPath;
    const QString reportDirectory = QDir(root).filePath(QStringLiteral("verification/reports"));
    if (!QDir().mkpath(reportDirectory))
        return toolResult(false, QStringLiteral("Could not create the verification report directory"));
    const QString reportPath = QDir(reportDirectory).filePath(
        QStringLiteral("engineering-verification.json"));
    QSaveFile file(reportPath);
    if (!file.open(QIODevice::WriteOnly)
        || file.write(QJsonDocument(report).toJson(QJsonDocument::Indented)) < 0
        || !file.commit())
        return toolResult(false, QStringLiteral("Could not persist verification report"));
    const QString overall = report.value(QStringLiteral("overall_status")).toString();
    m_completedAgentStages.removeAll(QStringLiteral("verification"));
    m_completedAgentStages.removeAll(QStringLiteral("verification-failed"));
    m_completedAgentStages.removeAll(QStringLiteral("verification-incomplete"));
    if (overall == QStringLiteral("pass"))
        m_completedAgentStages.append(QStringLiteral("verification"));
    else if (overall == QStringLiteral("fail"))
        m_completedAgentStages.append(QStringLiteral("verification-failed"));
    else
        m_completedAgentStages.append(QStringLiteral("verification-incomplete"));
    if (auto* dock = findChild<QDockWidget*>(QStringLiteral("dock_drc"))) {
        dock->show();
        dock->raise();
    }
    m_drcPanel->runDrc();
    return toolResult(true,
        QStringLiteral("Deterministic checks executed with status %1; %2 component roles still await automatic datasheet extraction")
            .arg(overall).arg(unresolvedCount),
        QJsonObject{{QStringLiteral("report_path"), reportPath},
                    {QStringLiteral("overall_status"), overall},
                    {QStringLiteral("release_gate"), overall == QStringLiteral("pass")
                            && componentEvidenceComplete
                        ? QStringLiteral("engineering checks passed")
                        : overall == QStringLiteral("pass")
                            ? QStringLiteral("pending automatic component extraction")
                        : QStringLiteral("blocked by engineering checks")},
                    {QStringLiteral("verification"), report}});
}

QJsonObject MainWindow::commitAgentConfiguration(const QJsonObject& arguments) {
    if (!m_completedAgentStages.contains(QStringLiteral("verification")))
        return toolResult(false, QStringLiteral("Engineering verification must pass before commit"));
    const auto workspace = m_agentd->workspace();
    if (workspace.manifestPath.isEmpty())
        return toolResult(false, QStringLiteral("No unified workspace is open"));
    const QString reportPath = QDir(workspace.rootPath).filePath(
        QStringLiteral("verification/reports/engineering-verification.json"));
    QFile reportFile(reportPath);
    if (!reportFile.open(QIODevice::ReadOnly))
        return toolResult(false, QStringLiteral("Current verification report is missing"));
    const QJsonObject currentReport = QJsonDocument::fromJson(reportFile.readAll()).object();
    const QJsonObject reportProject = currentReport.value(QStringLiteral("project")).toObject();
    if (currentReport.value(QStringLiteral("overall_status")).toString() != QStringLiteral("pass")
        || reportProject.value(QStringLiteral("document_id")).toString() != m_model->documentId()
        || reportProject.value(QStringLiteral("revision")).toInteger(-1) != m_model->revision()
        || reportProject.value(QStringLiteral("file_sha256")).toString() != m_model->fileSha256())
        return toolResult(false, QStringLiteral("Verification report does not pass for the current electronics revision"));

    QFile manifestFile(workspace.manifestPath);
    if (!manifestFile.open(QIODevice::ReadOnly)) return toolResult(false, manifestFile.errorString());
    const QJsonObject manifest = QJsonDocument::fromJson(manifestFile.readAll()).object();
    const QString baselineRelative = manifest.value(QStringLiteral("baseline_configuration")).toString();
    QFile baselineFile(QDir(workspace.rootPath).filePath(baselineRelative));
    if (!baselineFile.open(QIODevice::ReadOnly)) return toolResult(false, baselineFile.errorString());
    const QJsonObject baseline = QJsonDocument::fromJson(baselineFile.readAll()).object();
    const QString baselineId = baseline.value(QStringLiteral("configuration_id")).toString();
    if (baselineId.isEmpty()) return toolResult(false, QStringLiteral("Baseline configuration is invalid"));

    const QString executable = AgentdClient::executablePath();
    if (executable.isEmpty()) return toolResult(false, QStringLiteral("designstudio-agentd is unavailable"));
    QProcess process;
    process.start(executable, {QStringLiteral("--stdio")});
    if (!process.waitForStarted(3000)) return toolResult(false, process.errorString());
    QByteArray responseBuffer;
    qint64 requestId = 1;
    auto request = [&](const QString& method, const QJsonObject& params,
                       const QString& permission) -> QJsonObject {
        const qint64 id = requestId++;
        const QJsonObject rpc{
            {QStringLiteral("jsonrpc"), QStringLiteral("2.0")},
            {QStringLiteral("id"), id}, {QStringLiteral("method"), method},
            {QStringLiteral("params"), params},
            {QStringLiteral("auth"), QJsonObject{{QStringLiteral("permissions"),
                QJsonArray{permission}}}}
        };
        QByteArray bytes = QJsonDocument(rpc).toJson(QJsonDocument::Compact);
        bytes.append('\n');
        process.write(bytes);
        process.waitForBytesWritten(2000);
        QElapsedTimer timer;
        timer.start();
        while (timer.elapsed() < 8000) {
            const qsizetype newline = responseBuffer.indexOf('\n');
            if (newline >= 0) {
                const QByteArray line = responseBuffer.left(newline).trimmed();
                responseBuffer.remove(0, newline + 1);
                const QJsonObject response = QJsonDocument::fromJson(line).object();
                if (response.value(QStringLiteral("id")).toInteger() == id) return response;
                continue;
            }
            process.waitForReadyRead(250);
            responseBuffer += process.readAllStandardOutput();
        }
        return QJsonObject{{QStringLiteral("error"), QJsonObject{
            {QStringLiteral("message"), QStringLiteral("control-plane request timed out")}}}};
    };
    auto errorFrom = [](const QJsonObject& response) {
        return response.value(QStringLiteral("error")).toObject()
            .value(QStringLiteral("message")).toString();
    };
    QJsonObject response = request(QStringLiteral("project/open"),
        QJsonObject{{QStringLiteral("manifest_path"), workspace.manifestPath}},
        QStringLiteral("project:open"));
    if (response.contains(QStringLiteral("error"))) {
        process.kill();
        return toolResult(false, errorFrom(response));
    }
    const QJsonObject overrides{
        {QStringLiteral("product"), m_activeApplicationFamily},
        {QStringLiteral("input_vdc"), QJsonArray{m_activeInputMinV, m_activeInputMaxV}},
        {QStringLiteral("interface"), m_activeInterface},
        {QStringLiteral("axes"), m_activeAxisCount},
        {QStringLiteral("axis_current_a"), m_activeAxisCurrentA},
        {QStringLiteral("pcb_mm"), QJsonArray{m_model->boardWidthMm, m_model->boardHeightMm}},
        {QStringLiteral("pcb_layers"), m_model->copperLayers},
        {QStringLiteral("message"), arguments.value(QStringLiteral("message"))},
        {QStringLiteral("evidence_state"), QStringLiteral("incomplete")}
    };
    response = request(QStringLiteral("configuration/save"),
        QJsonObject{{QStringLiteral("parent_configuration_id"), baselineId},
                    {QStringLiteral("equipped"), baseline.value(QStringLiteral("equipped"))},
                    {QStringLiteral("parameter_overrides"), overrides},
                    {QStringLiteral("requirement_profile"),
                        designstudio::requirementProfileForFamily(m_activeApplicationFamily)}},
        QStringLiteral("configuration:write"));
    if (response.contains(QStringLiteral("error"))) {
        process.kill();
        return toolResult(false, errorFrom(response));
    }
    const QString sandboxId = response.value(QStringLiteral("result")).toObject()
        .value(QStringLiteral("configuration")).toObject()
        .value(QStringLiteral("configuration_id")).toString();
    response = request(QStringLiteral("configuration/commit"),
        QJsonObject{{QStringLiteral("configuration_id"), sandboxId}},
        QStringLiteral("configuration:write"));
    if (response.contains(QStringLiteral("error"))) {
        process.kill();
        return toolResult(false, errorFrom(response));
    }
    const QJsonObject committed = response.value(QStringLiteral("result")).toObject()
        .value(QStringLiteral("configuration")).toObject();
    const QString committedId = committed.value(QStringLiteral("configuration_id")).toString();
    const QJsonObject evaluation = request(QStringLiteral("release/evaluate"),
        QJsonObject{{QStringLiteral("configuration_id"), committedId}},
        QStringLiteral("release:evaluate"));
    process.terminate();
    process.waitForFinished(1000);

    QString reopenError;
    m_agentd->openWorkspace(workspace.manifestPath, &reopenError);
    if (!m_completedAgentStages.contains(QStringLiteral("committed")))
        m_completedAgentStages.append(QStringLiteral("committed"));
    QJsonObject assembly3d;
    if (m_nativeToolHandler) {
        assembly3d = m_nativeToolHandler(QStringLiteral("sync_completed_pcb_3d"),
            QJsonObject{{QStringLiteral("mechanical_path"), workspace.mechanicalPath},
                        {QStringLiteral("project_path"), workspace.electronicsPath}});
        if (assembly3d.value(QStringLiteral("ok")).toBool(false)
            && !m_completedAgentStages.contains(QStringLiteral("pcb-3d")))
            m_completedAgentStages.append(QStringLiteral("pcb-3d"));
    }
    const bool assemblyVisible = assembly3d.value(QStringLiteral("ok")).toBool(false);
    return toolResult(true,
        assemblyVisible
            ? QStringLiteral("Immutable child configuration committed and the completed PCB 3D assembly is visible; manufacturing release remains evidence-bound")
            : QStringLiteral("Immutable child configuration committed; PCB 3D synchronization is incomplete and manufacturing release remains evidence-bound"),
        QJsonObject{{QStringLiteral("configuration_id"), committedId},
                    {QStringLiteral("revision"), committed.value(QStringLiteral("revision"))},
                    {QStringLiteral("digest"), committed.value(QStringLiteral("digest"))},
                    {QStringLiteral("release"), evaluation.value(QStringLiteral("result"))},
                    {QStringLiteral("pcb_3d"), assembly3d},
                    {QStringLiteral("baseline_unchanged"), true}});
}

void MainWindow::refreshEngineeringViews() {
    if (m_layerPanel) m_layerPanel->setLayerCount(m_model->copperLayers);
    if (m_compPanel) m_compPanel->refresh();
    if (m_pcbCanvas) m_pcbCanvas->update();
    if (m_schView) m_schView->update();
}

QJsonObject MainWindow::productState() const {
    const auto workspace = m_agentd ? m_agentd->workspace() : AgentdClient::WorkspaceDocuments{};
    QJsonArray componentAssets;
    int assetReady = 0, assetPending = 0, assetMissing = 0;
    for (const auto& footprint : m_model->footprints) {
        QString status = footprint.boundComponent.isEmpty()
            ? footprint.asset3d.value(QStringLiteral("status")).toString()
            : QStringLiteral("exact_step");
        if (status == QStringLiteral("ready") || status == QStringLiteral("exact_step"))
            ++assetReady;
        else if (status == QStringLiteral("proxy_ready")
                 || status == QStringLiteral("cloud_pending")
                 || status == QStringLiteral("cloud_failed"))
            ++assetPending;
        else
            ++assetMissing;
        componentAssets.append(QJsonObject{
            {QStringLiteral("ref"), footprint.ref}, {QStringLiteral("status"), status},
            {QStringLiteral("generator"), footprint.boundComponent.isEmpty()
                ? footprint.asset3d.value(QStringLiteral("generator")).toString()
                : QStringLiteral("supplier-step-binding")}});
    }
    return QJsonObject{
        {QStringLiteral("ok"), true},
        {QStringLiteral("workspace"), workspace.rootPath},
        {QStringLiteral("workspace_id"), workspace.workspaceId},
        {QStringLiteral("product_name"), m_activeProductName},
        {QStringLiteral("application_family"), m_activeApplicationFamily},
        {QStringLiteral("axes"), m_activeAxisCount},
        {QStringLiteral("stages"), QJsonArray::fromStringList(m_completedAgentStages)},
        {QStringLiteral("mechanical_contract_locked"),
            m_model->mechanicalContract.value(QStringLiteral("status")).toString() == QStringLiteral("locked")},
        {QStringLiteral("board"), QJsonObject{{QStringLiteral("width_mm"), m_model->boardWidthMm},
            {QStringLiteral("height_mm"), m_model->boardHeightMm},
            {QStringLiteral("layers"), m_model->copperLayers}}},
        {QStringLiteral("components"), m_model->footprints.size()},
        {QStringLiteral("component_3d"), QJsonObject{
            {QStringLiteral("ready"), assetReady}, {QStringLiteral("pending"), assetPending},
            {QStringLiteral("missing"), assetMissing},
            {QStringLiteral("items"), componentAssets}}},
        {QStringLiteral("nets"), m_model->nets.size()},
        {QStringLiteral("traces"), m_model->traces.size()},
        {QStringLiteral("unresolved_evidence"), m_model->unresolvedComponents.size()},
        {QStringLiteral("release_gate"), m_model->unresolvedComponents.isEmpty()
            ? QStringLiteral("pending verification") : QStringLiteral("incomplete")}
    };
}

void MainWindow::onNew() {
    if (!confirmSave()) return;
    if (!m_nativeToolHandler) {
        const QString supported = QDir(QCoreApplication::applicationDirPath())
            .filePath(QStringLiteral("DesignStudio"));
        const QString current = QFileInfo(QCoreApplication::applicationFilePath())
            .canonicalFilePath();
        const QString target = QFileInfo(supported).canonicalFilePath();
        if (!target.isEmpty() && target != current
            && QProcess::startDetached(supported, {})) {
            QMessageBox::information(
                this, QStringLiteral("New Mechanical Product"),
                QStringLiteral("Mechanical workspace creation has been opened in the "
                               "supported FreeCAD-hosted DesignStudio application."));
        } else {
            QMessageBox::warning(
                this, QStringLiteral("New Mechanical Product"),
                QStringLiteral("Mechanical workspace creation is supported in the "
                               "FreeCAD-hosted DesignStudio application. Run the "
                               "installed bin/DesignStudio launcher."));
        }
        return;
    }
    const QString name = QInputDialog::getText(
        this, QStringLiteral("New Product"), QStringLiteral("Product name:"));
    if (name.trimmed().isEmpty()) return;
    // Cold-start creation intentionally uses the one-shot project/create RPC.
    // There cannot be a persistent project control process until a manifest
    // exists to open.
    m_controlPlaneStatusLabel->setText(QStringLiteral("Control: creating"));
    m_controlPlaneStatusLabel->setToolTip(
        QStringLiteral("creating workspace documents with one-shot project/create"));
    m_controlPlaneStatusLabel->setStyleSheet(QStringLiteral("color:#d29922;"));
    m_statusLabel->setText(QStringLiteral("Creating workspace manifest"));
    const QJsonObject result = createAgentWorkspace(
        QJsonObject{{QStringLiteral("name"), name.trimmed()}});
    const bool ok = result.value(QStringLiteral("ok")).toBool(false);
    m_statusLabel->setText(result.value(QStringLiteral("message")).toString());
    if (!ok) {
        m_controlPlaneStatusLabel->setText(QStringLiteral("Control: offline"));
        m_controlPlaneStatusLabel->setToolTip(m_statusLabel->text());
        m_controlPlaneStatusLabel->setStyleSheet(QStringLiteral("color:#f85149;"));
        QMessageBox::warning(this, QStringLiteral("New Product"), m_statusLabel->text());
    }
}

void MainWindow::onOpen() {
    if (!confirmSave()) return;
    QString path = QFileDialog::getOpenFileName(this,
        "Open Project", QString(),
        "DesignStudio Workspaces and Projects (manifest.json *.dsproj);;All Files (*)");
    if (!path.isEmpty()) {
        if (QFileInfo(path).fileName() == QStringLiteral("manifest.json")) {
            QString error;
            if (!openWorkspace(path, &error))
                QMessageBox::critical(this, "Open Workspace Failed", error);
        } else {
            openFile(path);
        }
    }
}

void MainWindow::onReload() {
    const QString path = m_model->filePath();
    if (path.isEmpty()) return;
    if (m_model->isModified()) {
        const auto answer = QMessageBox::warning(
            this, "Reload Project",
            "Reload the current project from disk?\n\n"
            "Unsaved in-memory changes will be discarded. External agent output "
            "is not reloaded automatically and should normally be applied through "
            "a validated proposal.",
            QMessageBox::Yes | QMessageBox::Cancel, QMessageBox::Cancel);
        if (answer != QMessageBox::Yes) return;
    }
    if (m_model->loadFromFile(path)) {
        if (m_pcbCanvas) m_pcbCanvas->update();
        m_statusLabel->setText("Reloaded by user: " + path);
    } else {
        QMessageBox::critical(this, "Reload Project Failed",
            "The active in-memory project was preserved.\n\n" + m_model->lastError());
    }
}

void MainWindow::onSave() {
    if (m_model->filePath().isEmpty()) { onSaveAs(); return; }
    if (!m_model->saveToFile(m_model->filePath()))
        QMessageBox::critical(this, "Save Error", "Failed to save project.\n\n" + m_model->lastError());
    else {
        m_statusLabel->setText("Saved: " + m_model->filePath());
        updateCadAssistAvailability();
    }
}

void MainWindow::onSaveAs() {
    QString path = QFileDialog::getSaveFileName(this,
        "Save Project As", QString(),
        "DesignStudio Projects (*.dsproj);;All Files (*)");
    if (path.isEmpty()) return;
    if (!path.endsWith(".dsproj")) path += ".dsproj";
    if (!m_model->saveToFile(path))
        QMessageBox::critical(this, "Save Error", "Failed to save project.\n\n" + m_model->lastError());
    else {
        if (m_actReload) m_actReload->setEnabled(true);
        updateCadAssistAvailability();
    }
}

void MainWindow::onAutoRoute() {
    if (m_completedAgentStages.contains(QStringLiteral("pcb-draft"))
        && !m_completedAgentStages.contains(QStringLiteral("pcb"))) {
        QMessageBox::information(this, "Auto Route",
            "This PCB is still an unconstrained placement draft. Run /layout first "
            "to optimize connectivity, edge placement, overlap, and rotation.");
        return;
    }
    const bool provisional = std::any_of(m_model->footprints.cbegin(),
        m_model->footprints.cend(), [](const ProjFootprint& footprint) {
            return footprint.mpn.startsWith(QStringLiteral("PROVISIONAL-"));
        });
    if (provisional) {
        QMessageBox::information(this, "Auto Route",
            "Routing waits until the architecture placeholders are replaced by "
            "GPT-5.6 Luna datasheet-derived SMT/THT footprints.");
        return;
    }
    if (!m_core || !m_core->isLoaded()) {
        QMessageBox::information(this, "Auto Route",
            "Native engine (libdesigncore.so) is not loaded.\n"
            "Build the C++ core first:\n\n"
            "  cmake -B core/build -S core -DCMAKE_BUILD_TYPE=Release\n"
            "  cmake --build core/build -j\n\n"
            "Then copy libdesigncore.so next to this executable.");
        return;
    }

    // Disable routing UI to prevent concurrent calls and model modifications.
    m_actAutoRoute->setEnabled(false);
    m_actNew->setEnabled(false);
    m_actOpen->setEnabled(false);
    m_statusLabel->setText("Auto-routing… (UI stays live — results applied when done)");
    m_progressBar->setRange(0, 0);   // indeterminate spinner
    m_progressBar->setVisible(true);

    // Run the A* router on a background thread so the UI event loop stays alive.
    // autoRoute() now collects traces/vias in RouteResult instead of writing to
    // the model, so no cross-thread model writes occur.
    CoreBridge*   core  = m_core;
    ProjectModel* model = m_model;
    auto* watcher = new QFutureWatcher<RouteResult>(this);

    connect(watcher, &QFutureWatcher<RouteResult>::finished,
            this, [this, watcher] {
        RouteResult result = watcher->result();
        watcher->deleteLater();

        // Back on UI thread: apply collected traces and vias to the model.
        if (result.ok) {
            m_model->traces += result.newTraces;
            m_model->vias   += result.newVias;
            m_model->setModified(true);
            m_statusLabel->setText("Route complete — " + result.message);
            m_pcbCanvas->update();
        } else {
            m_statusLabel->setText("Route failed: " + result.message);
            QMessageBox::warning(this, "Auto Route",
                "Routing failed:\n" + result.message);
        }

        m_progressBar->setVisible(false);
        m_actAutoRoute->setEnabled(true);
        m_actNew->setEnabled(true);
        m_actOpen->setEnabled(true);
    });

    watcher->setFuture(QtConcurrent::run([core, model]() -> RouteResult {
        return core->autoRoute(model);
    }));
}

void MainWindow::onBoardSetup() { showBoardSetupDialog(); }

void MainWindow::showBoardSetupDialog() {
    QDialog dlg(this);
    dlg.setWindowTitle("Board Setup");
    dlg.setMinimumWidth(380);

    auto* form = new QFormLayout(&dlg);
    form->setSpacing(10);
    form->setContentsMargins(16,16,16,8);

    auto* grpBoard = new QGroupBox("Board Dimensions");
    auto* boardForm = new QFormLayout(grpBoard);

    auto* wSpin = new QDoubleSpinBox; wSpin->setRange(10,600); wSpin->setSuffix(" mm"); wSpin->setValue(m_model->boardWidthMm);
    auto* hSpin = new QDoubleSpinBox; hSpin->setRange(10,600); hSpin->setSuffix(" mm"); hSpin->setValue(m_model->boardHeightMm);
    auto* gSpin = new QDoubleSpinBox; gSpin->setRange(0.01,5); gSpin->setSuffix(" mm"); gSpin->setValue(m_model->gridMm);
    auto* lSpin = new QSpinBox;       lSpin->setRange(1,32);                            lSpin->setValue(m_model->copperLayers);

    boardForm->addRow("Width:",         wSpin);
    boardForm->addRow("Height:",        hSpin);
    boardForm->addRow("Grid:",          gSpin);
    boardForm->addRow("Copper layers:", lSpin);

    auto* grpStack = new QGroupBox("Stackup (for physics)");
    auto* stackForm = new QFormLayout(grpStack);
    auto* erSpin  = new QDoubleSpinBox; erSpin->setRange(1,20); erSpin->setDecimals(2); erSpin->setValue(m_model->erDielectric);
    auto* hSpin2  = new QDoubleSpinBox; hSpin2->setRange(0.01,5); hSpin2->setSuffix(" mm"); hSpin2->setDecimals(3); hSpin2->setValue(m_model->dielectricHMm);
    auto* tanSpin = new QDoubleSpinBox; tanSpin->setRange(0,1); tanSpin->setDecimals(4); tanSpin->setValue(m_model->lossTangent);
    auto* cuSpin  = new QDoubleSpinBox; cuSpin->setRange(0.001,1); cuSpin->setSuffix(" mm"); cuSpin->setDecimals(4); cuSpin->setValue(m_model->copperTMm);
    stackForm->addRow("Dielectric εr:",   erSpin);
    stackForm->addRow("Dielectric h:",    hSpin2);
    stackForm->addRow("Loss tangent:",    tanSpin);
    stackForm->addRow("Copper thickness:",cuSpin);

    ProjPcbRuleProfile pendingProfile = m_model->pcbRules;
    auto* grpProfile = new QGroupBox("Manufacturing Rule Profile");
    auto* profileForm = new QFormLayout(grpProfile);
    auto* profileName = new QLabel;
    auto* profileSource = new QLabel;
    auto refreshProfile = [&] {
        profileName->setText(QString("%1 (Class %2 / Level %3)")
            .arg(pendingProfile.name).arg(pendingProfile.ipcPerformanceClass)
            .arg(pendingProfile.producibilityLevel));
        profileSource->setText(QString("%1 @ %2")
            .arg(pendingProfile.source, pendingProfile.sourceRevision));
    };
    refreshProfile();
    auto* importProfile = new QPushButton("Import verified profile…");
    connect(importProfile, &QPushButton::clicked, &dlg, [&] {
        const QString path = QFileDialog::getOpenFileName(
            &dlg, "Import PCB Manufacturing Profile", QString(), "JSON profile (*.json)");
        if (path.isEmpty()) return;
        ProjPcbRuleProfile candidate;
        QString error;
        if (!m_model->loadPcbRuleProfileFile(path, candidate, error)) {
            QMessageBox::critical(&dlg, "Profile rejected", error);
            return;
        }
        pendingProfile = candidate;
        refreshProfile();
    });
    profileForm->addRow("Profile:", profileName);
    profileForm->addRow("Provenance:", profileSource);
    profileForm->addRow(importProfile);

    form->addRow(grpBoard);
    form->addRow(grpStack);
    form->addRow(grpProfile);

    auto* buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel);
    form->addRow(buttons);
    connect(buttons, &QDialogButtonBox::accepted, &dlg, &QDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, &dlg, &QDialog::reject);

    if (dlg.exec() == QDialog::Accepted) {
        m_model->boardWidthMm  = wSpin->value();
        m_model->boardHeightMm = hSpin->value();
        m_model->gridMm        = gSpin->value();
        m_model->copperLayers  = lSpin->value();
        m_model->erDielectric  = erSpin->value();
        m_model->dielectricHMm = hSpin2->value();
        m_model->lossTangent   = tanSpin->value();
        m_model->copperTMm     = cuSpin->value();
        m_model->pcbRules      = pendingProfile;
        m_model->setModified(true);
        m_layerPanel->setLayerCount(m_model->copperLayers);
        m_pcbCanvas->fitToBoard();
    }
}

void MainWindow::onTabChanged(int index) {
    QWidget* workspace = m_tabs->widget(index);
    // Notify the FreeCAD host module when the mechanical viewport tab is
    // entered or left so the shared MDI surface swaps documents.
    emit mechanicalViewActive(
        m_mechanicalViewport && workspace == m_mechanicalViewport);
    // Hidden tabs can receive their model-loaded signal before Qt has assigned
    // their final viewport size. Fit on the next event turn, after the tab
    // layout has settled, so the design cannot open off-screen or as a blank
    // corner of the canvas.
    QTimer::singleShot(0, this, [this, workspace] {
        if (!m_tabs || m_tabs->currentWidget() != workspace) return;
        if (workspace == m_pcbCanvas) m_pcbCanvas->fitToBoard();
        else if (workspace == m_schView) m_schView->fitToSchematic();
        else if (workspace == m_pcb3DView) {
            // STEP output can be generated while the project remains open.
            // Pcb3DView caches an unchanged import, so this is cheap on re-entry.
            m_pcb3DView->refreshFromProject();
            m_pcb3DView->fitToModel();
        }
    });
    updateWorkspaceActions(workspace);
}

void MainWindow::updateWorkspaceActions(QWidget* workspace) {
    if (!m_contextToolbar) return;
    m_contextToolbar->clear();
    const QList<QAction*>* actions = nullptr;
    if (workspace == m_pcbCanvas) actions = &m_pcbContextActions;
    else if (workspace == m_schView) actions = &m_schematicContextActions;
    else if (workspace == m_pcb3DView) actions = &m_pcb3DContextActions;
    else if (workspace == m_enclosureConcept) actions = &m_mechanicalContextActions;
    else if (workspace == m_verificationView) actions = &m_verificationContextActions;
    if (actions) {
        for (QAction* action : *actions) m_contextToolbar->addAction(action);
    }
    if (m_actToggleCodex && m_uiProfile == UiProfile::Standalone) {
        if (actions && !actions->isEmpty()) m_contextToolbar->addSeparator();
        m_contextToolbar->addAction(m_actToggleCodex);
    }
    m_contextToolbar->setVisible(
        (m_actToggleCodex && m_uiProfile == UiProfile::Standalone)
        || (actions && !actions->isEmpty()));
    const bool zoomable = workspace == m_pcbCanvas || workspace == m_schView;
    if (m_actZoomIn) m_actZoomIn->setEnabled(zoomable);
    if (m_actZoomOut) m_actZoomOut->setEnabled(zoomable);
    if (m_actZoomFit) m_actZoomFit->setEnabled(
        zoomable || workspace == m_pcb3DView);
}

void MainWindow::addRecentProduct(const QString& path) {
    if (path.isEmpty()) return;
    QSettings settings(QStringLiteral("DesignStudio"), QStringLiteral("DesignStudio"));
    QStringList recent = settings.value(QStringLiteral("recentProducts")).toStringList();
    recent.removeAll(path);
    recent.prepend(path);
    while (recent.size() > 10) recent.removeLast();
    settings.setValue(QStringLiteral("recentProducts"), recent);
    if (m_productHome) m_productHome->setRecentProducts(recent);
}

void MainWindow::updateTitle() {
    QString title = "DesignStudio";
    if (!m_model->filePath().isEmpty()) {
        QFileInfo fi(m_model->filePath());
        title = fi.baseName() + " — DesignStudio";
    }
    if (m_model->isModified()) title = "* " + title;
    setWindowTitle(title);
}

void MainWindow::updateCadAssistAvailability() {
    if (!m_actLoadCadAssist || !m_actCopyCadAssistContext || !m_model) return;
    const bool ready = !m_model->filePath().isEmpty()
        && !m_model->isModified()
        && !m_model->documentId().isEmpty()
        && !m_model->fileSha256().isEmpty()
        && m_model->hasPersistedIdentity();
    m_actLoadCadAssist->setEnabled(ready);
    m_actCopyCadAssistContext->setEnabled(ready);
    m_actLoadCadAssist->setToolTip(
        ready
            ? QStringLiteral("Load evidence bound to this saved project revision")
            : QStringLiteral("Save the current project before loading CAD-assist evidence"));
    m_actCopyCadAssistContext->setToolTip(
        ready
            ? QStringLiteral("Copy helper environment binding for this exact saved revision")
            : QStringLiteral("Save the current project before copying its CAD-assist binding"));
}

void MainWindow::clearCadAssistSession(const QString& reason) {
    if (m_cadAssistPanel) m_cadAssistPanel->clearSession(reason);
}

bool MainWindow::confirmSave() {
    if (!m_model->isModified()) return true;
    auto ret = QMessageBox::question(this, "Unsaved Changes",
        "Save changes before closing?",
        QMessageBox::Save | QMessageBox::Discard | QMessageBox::Cancel);
    if (ret == QMessageBox::Save)    { onSave(); return true; }
    if (ret == QMessageBox::Discard) return true;
    return false;
}

void MainWindow::closeEvent(QCloseEvent* e) {
    if (!confirmSave()) { e->ignore(); return; }
    QSettings s("DesignStudio","DesignStudio");
    s.setValue("geometry",    saveGeometry());
    s.setValue("windowState", saveState());
    e->accept();
}

// Drag-and-drop .dsproj files onto the window
void MainWindow::dragEnterEvent(QDragEnterEvent* e) {
    if (e->mimeData()->hasUrls()) e->acceptProposedAction();
}

void MainWindow::dropEvent(QDropEvent* e) {
    for (auto& url : e->mimeData()->urls()) {
        QString path = url.toLocalFile();
        if (path.endsWith(".dsproj", Qt::CaseInsensitive)) {
            if (confirmSave()) openFile(path);
            break;
        }
    }
}

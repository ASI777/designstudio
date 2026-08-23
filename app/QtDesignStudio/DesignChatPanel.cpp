#include "DesignChatPanel.h"
#include "ProjectModel.h"
#include "CoreBridge.h"
#include "CodexAppServerClient.h"
#include <QMap>

#include <QTextEdit>
#include <QLineEdit>
#include <QPushButton>
#include <QToolButton>
#include <QLabel>
#include <QProgressBar>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QGridLayout>
#include <QScrollBar>
#include <QScrollArea>
#include <QPixmap>
#include <QPainter>
#include <QWheelEvent>
#include <QProcess>
#include <QDir>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QCryptographicHash>
#include <QRegularExpression>
#include <QApplication>
#include <QFont>
#include <QFontDatabase>
#include <QDateTime>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QDate>
#include <QStandardPaths>
#include <QInputDialog>
#include <QComboBox>
#include <QFrame>
#include <QMenu>
#include <QSettings>
#include <QDialog>
#include <QDialogButtonBox>
#include <QGroupBox>
#include <QCollator>
#include <algorithm>

static QString SWARM_ROOT;

static QString swarmPath(const QString& rel) {
    if (SWARM_ROOT.isEmpty()) {
        // Walk up from executable to find swarm/agents/
        QDir d = QDir(QApplication::applicationDirPath());
        for (int i = 0; i < 6; ++i) {
            if (QDir(d.filePath("swarm/agents")).exists()) {
                SWARM_ROOT = d.absolutePath();
                break;
            }
            d.cdUp();
        }
        if (SWARM_ROOT.isEmpty()) {
            const QString agents = QStandardPaths::locate(
                QStandardPaths::GenericDataLocation,
                QStringLiteral("DesignStudio/python/swarm/agents"),
                QStandardPaths::LocateDirectory);
            if (!agents.isEmpty()) {
                QDir installed(agents);
                installed.cdUp(); // swarm
                installed.cdUp(); // python root
                SWARM_ROOT = installed.absolutePath();
            }
        }
    }
    return SWARM_ROOT + "/" + rel;
}

static QString compactModelName(const QJsonObject& model)
{
    QString name = model.value(QStringLiteral("displayName")).toString();
    if (name.isEmpty()) name = model.value(QStringLiteral("id")).toString();
    name.replace(QStringLiteral("GPT-"), QString());
    name.replace(QLatin1Char('-'), QLatin1Char(' '));
    return name.trimmed();
}

static QString prettyEffort(const QString& effort)
{
    if (effort.compare(QStringLiteral("xhigh"), Qt::CaseInsensitive) == 0)
        return QStringLiteral("Extra high");
    if (effort.compare(QStringLiteral("ultra"), Qt::CaseInsensitive) == 0)
        return QStringLiteral("Ultra");
    if (effort.compare(QStringLiteral("max"), Qt::CaseInsensitive) == 0)
        return QStringLiteral("Max");
    if (effort.isEmpty()) return QStringLiteral("Default");
    QString value = effort;
    value[0] = value[0].toUpper();
    return value;
}

static QString prettySpeed(const QString& serviceTier)
{
    if (serviceTier.isEmpty()) return QStringLiteral("Standard");
    return serviceTier;
}

using DecisionOptionList = QVector<QPair<int, QString>>;

static DecisionOptionList extractDecisionOptions(const QString& text)
{
    const QRegularExpression optionHeading(
        QStringLiteral("^\\s*#{2,4}\\s*(\\d+)[.)]\\s*(.+?)\\s*$"),
        QRegularExpression::MultilineOption);
    QRegularExpressionMatchIterator matches = optionHeading.globalMatch(text);
    DecisionOptionList options;
    while (matches.hasNext() && options.size() < 3) {
        const QRegularExpressionMatch match = matches.next();
        QString title = match.captured(2).trimmed();
        title.remove(QStringLiteral("**"));
        if (!title.isEmpty())
            options.append({match.captured(1).toInt(), title});
    }
    return options;
}

static bool isResolvedDecisionReply(const QString& text,
                                    const DecisionOptionList& options)
{
    if (options.size() < 2) return false;

    static const QRegularExpression resolvedMarker(
        QStringLiteral("\\b(selected|confirmed|retained|chosen|locked|approved|accepted)\\b"),
        QRegularExpression::CaseInsensitiveOption);
    int resolvedCount = 0;
    for (const auto& option : options) {
        if (resolvedMarker.match(option.second).hasMatch()) ++resolvedCount;
    }

    const QString lower = text.toLower();
    const bool explicitConfirmation =
        (lower.contains(QStringLiteral("all three"))
         || lower.contains(QStringLiteral("all baseline"))
         || lower.contains(QStringLiteral("every choice")))
        && (lower.contains(QStringLiteral("confirmed"))
            || lower.contains(QStringLiteral("selected"))
            || lower.contains(QStringLiteral("set"))
            || lower.contains(QStringLiteral("locked")));
    return explicitConfirmation || resolvedCount >= options.size();
}

// Use a neutral, high-legibility sans-serif for the design conversation.  The
// preference order keeps the UI familiar on workstations that already have a
// design-system font installed, while Noto Sans is available in the bundled
// Linux environment.  Monospace is intentionally reserved for code, hashes,
// and other machine-readable diagnostics.
static QString preferredDesignFontFamily()
{
    static const QString family = [] {
        const QStringList preferred{
            QStringLiteral("Inter"),
            QStringLiteral("IBM Plex Sans"),
            QStringLiteral("Aptos"),
            QStringLiteral("Noto Sans"),
            QStringLiteral("Segoe UI"),
            QStringLiteral("DejaVu Sans")
        };
        const QStringList installed = QFontDatabase::families();
        for (const QString& candidate : preferred) {
            for (const QString& available : installed) {
                if (available.compare(candidate, Qt::CaseInsensitive) == 0)
                    return available;
            }
        }
        return QApplication::font().family();
    }();
    return family;
}

// ── Constructor ───────────────────────────────────────────────────────────────
DesignChatPanel::DesignChatPanel(ProjectModel* model, QWidget* parent)
    : QWidget(parent), m_model(model)
{
    setMinimumWidth(320);

    const QString uiFontFamily = preferredDesignFontFamily();
    QFont panelFont(uiFontFamily);
    panelFont.setPointSizeF(10.5);
    setFont(panelFont);

    auto* vbox = new QVBoxLayout(this);
    vbox->setContentsMargins(0,0,0,0);
    vbox->setSpacing(0);

    // ── Header ────────────────────────────────────────────────────────────────
    auto* header = new QWidget;
    header->setStyleSheet("background:#26282c;border-bottom:1px solid #484b50;");
    auto* hlay = new QHBoxLayout(header);
    hlay->setContentsMargins(12,8,12,8);
    auto* title = new QLabel("Codex · DesignStudio");
    title->setStyleSheet("color:#F2F2F2;font-weight:600;font-size:12px;");
    auto* newBtn = new QToolButton;
    newBtn->setText("＋ New Chat");
    newBtn->setToolTip("Start a new chat (the current conversation is archived)");
    newBtn->setStyleSheet("QToolButton{background:#34363a;border:1px solid #55585d;"
                          "border-radius:5px;color:#E7E7E7;font-size:11px;padding:3px 8px;}"
                          "QToolButton:hover{background:#45484d;border-color:#73777d;}");
    connect(newBtn, &QToolButton::clicked, this, &DesignChatPanel::newChat);
    auto* stopBtn = new QToolButton;
    stopBtn->setText("■");
    stopBtn->setToolTip("Stop running agent");
    stopBtn->setStyleSheet("QToolButton{background:transparent;border:none;color:#C4261D;font-size:14px;}"
                           "QToolButton:hover{color:#D83A30;}");
    connect(stopBtn, &QToolButton::clicked, this, &DesignChatPanel::stopAgent);
    auto* technicalButton = new QToolButton;
    technicalButton->setText(QStringLiteral("Technical Details"));
    technicalButton->setCheckable(true);
    technicalButton->setToolTip(QStringLiteral("Show agent and tool diagnostics"));
    connect(technicalButton, &QToolButton::toggled, this, [this](bool visible) {
        if (m_technicalDetails) m_technicalDetails->setVisible(visible);
    });
    hlay->addWidget(title);
    hlay->addStretch();
#ifdef DESIGNSTUDIO_DEVELOPER_TOOLS
    m_providerCombo = new QComboBox;
    m_providerCombo->addItem("Codex", QStringLiteral("codex"));
    m_providerCombo->addItem("Legacy Harness", QStringLiteral("legacy"));
    m_providerCombo->setToolTip("Developer diagnostics provider override");
    m_providerCombo->setMaximumWidth(135);
    hlay->addWidget(m_providerCombo);
#endif
    hlay->addWidget(newBtn);
    hlay->addWidget(technicalButton);
    hlay->addWidget(stopBtn);
    vbox->addWidget(header);

    // ── Chat view ─────────────────────────────────────────────────────────────
    m_chatView = new QTextEdit;
    m_chatView->setReadOnly(true);
    m_chatView->setAcceptRichText(true);
    m_chatView->setStyleSheet(QStringLiteral(
        "QTextEdit{background:#202225;border:none;padding:8px;color:#E6E6E6;"
        "font-family:'%1';font-size:13px;}"
        "QTextEdit p{margin:0;padding:0;}"
    ).arg(uiFontFamily));
    const QString monoFontFamily = QFontDatabase::systemFont(
        QFontDatabase::FixedFont).family();
    m_chatView->document()->setDefaultStyleSheet(
        QStringLiteral(
        "body{font-family:'%1';font-size:13px;color:#E6E6E6;}"
        ".user{color:#D9E8FF;font-weight:600;}"
        ".assistant{color:#E6E6E6;}"
        ".tool{color:#8FC7FF;font-family:'%2';font-size:11px;}"
        ".system{color:#A8ADB4;font-style:italic;font-size:11px;}"
        ".bubble-user{background:#303844;border-left:3px solid #6EA8E5;"
        "  padding:8px 10px;margin:6px 0;}"
        ".bubble-asst{background:#2A2C30;border-left:3px solid #62666C;"
        "  padding:8px 10px;margin:6px 0;font-family:'%1';font-size:13px;}"
        ".bubble-tool{background:#25313B;border-left:3px solid #4E94CE;"
        "  padding:8px 10px;margin:4px 0;font-family:'%1';font-size:13px;}"
        ".tool-meta{color:#8FC7FF;font-family:'%2';font-size:10px;}"
        ".bubble-sys{color:#A8ADB4;font-size:11px;padding:2px 10px;}"
        "pre{white-space:pre-wrap;}"
        ).arg(uiFontFamily, monoFontFamily));
    vbox->addWidget(m_chatView, 1);

    // Decision-first surface: long Codex audit replies remain available in
    // Technical Details, while detected concept headings become one-click
    // choices in the main conversation.
    m_choiceFrame = new QFrame;
    m_choiceFrame->setObjectName(QStringLiteral("codex_decision_choices"));
    m_choiceFrame->setStyleSheet(
        "QFrame#codex_decision_choices{background:#252B32;border:1px solid #4E687F;"
        "border-radius:7px;margin:6px 8px;}"
        "QLabel{color:#D9E8F5;font-size:12px;font-weight:600;}"
        "QPushButton{background:#303D49;border:1px solid #5A7D99;border-radius:5px;"
        "padding:7px 10px;color:#F1F6FA;text-align:left;font-size:12px;}"
        "QPushButton:hover{background:#3D5870;border-color:#8BB8D8;}");
    m_choiceLayout = new QVBoxLayout(m_choiceFrame);
    m_choiceLayout->setContentsMargins(10, 8, 10, 8);
    m_choiceLayout->setSpacing(5);
    m_choiceFrame->hide();
    vbox->addWidget(m_choiceFrame);

    // ── Footprint thumbnail strip ───────────────────────────────────────────────
    // A horizontally-scrollable row of miniature extracted-component footprints.
    m_fpStrip = new QScrollArea;
    m_fpStrip->setWidgetResizable(true);
    m_fpStrip->setFixedHeight(116);
    m_fpStrip->setHorizontalScrollBarPolicy(Qt::ScrollBarAsNeeded);
    m_fpStrip->setVerticalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    m_fpStrip->setStyleSheet("QScrollArea{background:#EEF0F2;border-top:1px solid #B0B5B9;}");
    auto* stripInner = new QWidget;
    m_fpStripLayout = new QHBoxLayout(stripInner);
    m_fpStripLayout->setContentsMargins(8, 6, 8, 6);
    m_fpStripLayout->setSpacing(8);
    m_fpStripLayout->addStretch();
    m_fpStrip->setWidget(stripInner);
    m_fpStrip->viewport()->installEventFilter(this);   // wheel → horizontal scroll
    m_fpStrip->setVisible(false);                       // shown once parts exist
    vbox->addWidget(m_fpStrip);

    // Stage-level approval card. Codex tool calls remain pending while the UI
    // event loop stays live; no project mutation occurs until Approve is chosen.
    m_approvalFrame = new QFrame;
    m_approvalFrame->setObjectName("codex_stage_approval");
    m_approvalFrame->setStyleSheet(
        "QFrame#codex_stage_approval{background:#3A3427;border:1px solid #B58A35;"
        "border-radius:6px;margin:6px;}"
        "QLabel{color:#F0E7D1;}"
    );
    auto* approvalLayout = new QVBoxLayout(m_approvalFrame);
    approvalLayout->setContentsMargins(10, 8, 10, 8);
    m_approvalTitle = new QLabel;
    m_approvalTitle->setStyleSheet("font-weight:700;color:#FFD37A;");
    m_approvalSummary = new QLabel;
    m_approvalSummary->setWordWrap(true);
    auto* approvalButtons = new QHBoxLayout;
    auto* rejectButton = new QPushButton("Reject");
    auto* modifyButton = new QPushButton("Modify");
    auto* approveButton = new QPushButton("Approve");
    approveButton->setStyleSheet("background:#3974A8;color:white;padding:5px 12px;");
    approvalButtons->addStretch();
    approvalButtons->addWidget(rejectButton);
    approvalButtons->addWidget(modifyButton);
    approvalButtons->addWidget(approveButton);
    approvalLayout->addWidget(m_approvalTitle);
    approvalLayout->addWidget(m_approvalSummary);
    approvalLayout->addLayout(approvalButtons);
    connect(approveButton, &QPushButton::clicked, this, [this] {
        if (m_approvalToken.isEmpty()) return;
        const QString token = m_approvalToken;
        m_approvalToken.clear();
        m_approvalFrame->hide();
        emit codexToolApprovalResolved(token, true);
    });
    connect(rejectButton, &QPushButton::clicked, this, [this] {
        if (m_approvalToken.isEmpty()) return;
        const QString token = m_approvalToken;
        m_approvalToken.clear();
        m_approvalFrame->hide();
        emit codexToolApprovalResolved(token, false);
    });
    connect(modifyButton, &QPushButton::clicked, this, [this] {
        if (m_approvalToken.isEmpty()) return;
        const QString token = m_approvalToken;
        m_approvalToken.clear();
        m_approvalFrame->hide();
        emit codexToolApprovalResolved(token, false);
        m_input->setText(QStringLiteral("Modify the proposal: "));
        m_input->setFocus();
    });
    m_approvalFrame->hide();
    vbox->addWidget(m_approvalFrame);

    m_technicalDetails = new QTextEdit;
    m_technicalDetails->setObjectName(QStringLiteral("assistant_technical_details"));
    m_technicalDetails->setReadOnly(true);
    m_technicalDetails->setMaximumHeight(160);
    m_technicalDetails->setPlaceholderText(QStringLiteral("Tool diagnostics and raw responses appear here."));
    m_technicalDetails->hide();
    vbox->addWidget(m_technicalDetails);

    // ── Codex model controls ─────────────────────────────────────────────────
    // The catalog is fetched from the installed App Server. Until it is
    // available, the selector remains usable and reports that it is loading.
    QSettings codexSettings(QStringLiteral("DesignStudio"),
                            QStringLiteral("DesignStudio"));
    m_selectedModel = codexSettings.value(QStringLiteral("codex/model")).toString();
    m_selectedEffort = codexSettings.value(QStringLiteral("codex/effort")).toString();
    m_selectedServiceTier = codexSettings.value(QStringLiteral("codex/serviceTier")).toString();
    m_optionsButton = new QToolButton;
    m_optionsButton->setObjectName(QStringLiteral("codex_options_button"));
    m_optionsButton->setText(optionsButtonText());
    m_optionsButton->setToolTip(QStringLiteral("Choose Codex model, reasoning effort, and speed"));
    m_optionsButton->setPopupMode(QToolButton::InstantPopup);
    m_optionsButton->setMinimumWidth(154);
    m_optionsButton->setStyleSheet(
        "QToolButton{background:#36383D;border:1px solid #4A4D53;border-radius:16px;"
        "padding:7px 12px;color:#E6E6E6;text-align:left;font-size:12px;}"
        "QToolButton:hover{background:#41444A;border-color:#686D75;}"
        "QToolButton::menu-indicator{width:10px;subcontrol-position:right center;}");
    m_optionsMenu = new QMenu(m_optionsButton);
    m_optionsMenu->setStyleSheet(
        "QMenu{background:#2B2D31;color:#E6E6E6;border:1px solid #4B4E54;"
        "padding:5px;}"
        "QMenu::item{padding:7px 26px 7px 10px;}"
        "QMenu::item:selected{background:#3D5268;}"
        "QMenu::item:disabled{color:#858A92;}");
    m_optionsButton->setMenu(m_optionsMenu);
    connect(m_optionsMenu, &QMenu::aboutToShow, this, [this] {
        if (m_modelCatalog.isEmpty()) {
            startCodex();
            m_statusLabel->setText(QStringLiteral("Loading Codex models…"));
        }
        rebuildOptionsMenu();
    });
    rebuildOptionsMenu();

    // ── Input bar (clean chat input — rounded, serif text, coral send) ──────────
    // Datasheet add + footprint extraction now live in the Components panel.
    auto* inputBar = new QWidget;
    inputBar->setStyleSheet("background:#26282c;border-top:1px solid #484b50;");
    auto* ilay = new QHBoxLayout(inputBar);
    ilay->setContentsMargins(10,10,10,10);
    ilay->setSpacing(8);

    m_input = new QLineEdit;
    m_input->setPlaceholderText("How can I help you today?");
    m_input->setStyleSheet(
        "QLineEdit{background:#1F2124;border:1px solid #55585D;border-radius:18px;"
        "padding:9px 16px;color:#EEEEEE;font-size:14px;}"
        "QLineEdit:focus{border-color:#6299CC;}");
    connect(m_input, &QLineEdit::returnPressed, this, [this] {
        // Enter remains a send action only while idle. The circular control
        // itself is the explicit stop affordance while a task is running.
        if (!m_agentRunning) onSend();
    });

    // Circular send/stop control. It changes to a red square while a Codex
    // or legacy agent task is active, matching the interaction users expect
    // from the desktop Codex composer.
    m_sendBtn = new QPushButton(QStringLiteral("\u2191"));   // ↑
    m_sendBtn->setObjectName(QStringLiteral("codex_send_stop_button"));
    m_sendBtn->setCursor(Qt::PointingHandCursor);
    m_sendBtn->setFixedSize(36,36);
    updateRunControl(false);
    connect(m_sendBtn, &QPushButton::clicked, this, &DesignChatPanel::onSend);

    m_attachButton = new QToolButton;
    m_attachButton->setText(QStringLiteral("+"));
    m_attachButton->setCheckable(true);
    m_attachButton->setToolTip(QStringLiteral(
        "Attach reference images or a PDF to the next Codex turn"));
    m_attachButton->setCursor(Qt::PointingHandCursor);
    m_attachButton->setFixedSize(34, 34);
    m_attachButton->setStyleSheet(
        "QToolButton{background:#36383D;border:1px solid #4A4D53;border-radius:17px;"
        "color:#E6E6E6;font-size:16px;}"
        "QToolButton:hover{background:#41444A;border-color:#686D75;}"
        "QToolButton:checked{background:#3974A8;border-color:#6299CC;}");
    auto* attachmentMenu = new QMenu(m_attachButton);
    attachmentMenu->addAction(QStringLiteral("Reference images (PNG/JPEG/WebP)..."),
                              this, &DesignChatPanel::attachReferenceImages);
    attachmentMenu->addAction(QStringLiteral("Reference PDF..."),
                              this, &DesignChatPanel::attachReferencePdf);
    m_attachButton->setMenu(attachmentMenu);
    m_attachButton->setPopupMode(QToolButton::InstantPopup);

    ilay->addWidget(m_attachButton);
    ilay->addWidget(m_optionsButton);
    ilay->addWidget(m_input, 1);
    ilay->addWidget(m_sendBtn);
    vbox->addWidget(inputBar);

    // ── Status bar ────────────────────────────────────────────────────────────
    m_statusLabel = new QLabel("Ready");
    m_statusLabel->setStyleSheet("color:#8b949e;font-size:11px;padding:2px 10px;");
    m_progress = new QProgressBar;
    m_progress->setRange(0,0);  // indeterminate
    m_progress->setFixedHeight(3);
    m_progress->setTextVisible(false);
    m_progress->setVisible(false);
    m_progress->setStyleSheet("QProgressBar{background:#161b22;border:none;}"
                               "QProgressBar::chunk{background:#1f6feb;}");
    vbox->addWidget(m_progress);
    vbox->addWidget(m_statusLabel);

    // Restore this user's previous conversation so they continue where they left.
    loadHistory();
    if (m_history.isEmpty())
        appendSystem("Codex is ready. Describe the product or the engineering change you need. "
                     "Proposed operations will be summarized for approval before mutation.");
    else
        appendSystem("Welcome back — restored your previous conversation. "
                     "Continue where you left off.");

    connect(model, &ProjectModel::loaded, this, [this]{
        appendSystem("Project loaded — " +
                     QString::number(m_model->footprints.size()) + " components, " +
                     QString::number(m_model->nets.size()) + " nets.");
        updateFootprintStrip();
    });

#ifdef DESIGNSTUDIO_DEVELOPER_TOOLS
    connect(m_providerCombo, &QComboBox::currentIndexChanged, this, [this](int) {
        QSettings(QStringLiteral("DesignStudio"), QStringLiteral("DesignStudio"))
            .setValue(QStringLiteral("agent/provider"), m_providerCombo->currentData());
        if (usingCodex()) startCodex();
        m_statusLabel->setText(usingCodex() ? "Codex selected" : "Legacy harness selected");
    });
#endif
}

DesignChatPanel::~DesignChatPanel() {
    if (m_proc && m_proc->state() != QProcess::NotRunning) {
        disconnect(m_proc, nullptr, this, nullptr);
        m_proc->terminate();
        if (!m_proc->waitForFinished(1500)) {
            m_proc->kill();
            m_proc->waitForFinished(1500);
        }
    }
    if (m_datasheetProc && m_datasheetProc->state() != QProcess::NotRunning) {
        disconnect(m_datasheetProc, nullptr, this, nullptr);
        m_datasheetProc->terminate();
        if (!m_datasheetProc->waitForFinished(1500)) {
            m_datasheetProc->kill();
            m_datasheetProc->waitForFinished(1500);
        }
    }
    if (m_componentPublishProc && m_componentPublishProc->state() != QProcess::NotRunning) {
        disconnect(m_componentPublishProc, nullptr, this, nullptr);
        m_componentPublishProc->terminate();
        if (!m_componentPublishProc->waitForFinished(1500)) m_componentPublishProc->kill();
    }
    if (m_productBuildProc && m_productBuildProc->state() != QProcess::NotRunning) {
        disconnect(m_productBuildProc, nullptr, this, nullptr);
        m_productBuildProc->terminate();
        if (!m_productBuildProc->waitForFinished(1500)) {
            m_productBuildProc->kill();
            m_productBuildProc->waitForFinished(1500);
        }
    }
}

bool DesignChatPanel::usingCodex() const {
#ifdef DESIGNSTUDIO_DEVELOPER_TOOLS
    return !m_providerCombo
        || m_providerCombo->currentData().toString() == QStringLiteral("codex");
#else
    return true;
#endif
}

void DesignChatPanel::updateRunControl(bool running)
{
    if (!m_sendBtn) return;

    m_sendBtn->setEnabled(true);
    m_sendBtn->setText(running ? QStringLiteral("\u25A0")
                               : QStringLiteral("\u2191"));
    m_sendBtn->setToolTip(running ? QStringLiteral("Stop current task")
                                  : QStringLiteral("Send"));
    m_sendBtn->setAccessibleName(running ? QStringLiteral("Stop current task")
                                         : QStringLiteral("Send message"));
    m_sendBtn->setStyleSheet(running
        ? QStringLiteral(
            "QPushButton{background:#B83A3A;border:none;border-radius:18px;"
            "color:#FFFFFF;font-size:15px;font-weight:700;}"
            "QPushButton:hover{background:#D24A4A;}"
            "QPushButton:pressed{background:#8F2D2D;}")
        : QStringLiteral(
            "QPushButton{background:#3974A8;border:none;border-radius:18px;"
            "color:#FFFFFF;font-size:18px;font-weight:700;}"
            "QPushButton:hover{background:#4A89C0;}"
            "QPushButton:pressed{background:#2E5E89;}")
    );
}

void DesignChatPanel::startCodex() {
    if (!m_codex) {
        m_codex = new CodexAppServerClient(this);
        connect(m_codex, &CodexAppServerClient::stateChanged, this,
                [this](CodexAppServerClient::State state, const QString& detail) {
            if (usingCodex()) m_statusLabel->setText(QStringLiteral("Codex: %1").arg(detail));
            if (state == CodexAppServerClient::State::Failed) {
                appendSystem(
                    QStringLiteral("Codex unavailable: %1. Retry after checking the local App Server.")
                        .arg(detail));
                if (m_agentRunning) {
                    finalizeCurrentBubble();
                    m_agentRunning = false;
                    updateRunControl(false);
                    m_progress->hide();
                    emit agentFinished(QStringLiteral("codex"), false);
                }
            }
        });
        connect(m_codex, &CodexAppServerClient::assistantDelta, this,
                [this](const QString& delta) {
            // A hard stop can leave one already-buffered delta in Qt's event
            // queue.  Never let that stale data restart the stopped UI.
            if (!m_codex || m_codex->state() == CodexAppServerClient::State::Stopped)
                return;
            if (!m_agentRunning) {
                m_agentRunning = true;
                startAssistantBubble(QStringLiteral("codex"));
            }
            appendToCurrentBubble(delta);
            emit agentOutput(QStringLiteral("codex"), delta);
        });
        connect(m_codex, &CodexAppServerClient::reasoningDelta, this,
                [this](const QString& delta) {
            if (!delta.trimmed().isEmpty()) m_statusLabel->setText(delta.trimmed());
        });
        connect(m_codex, &CodexAppServerClient::activity, this,
                [this](const QString& text) {
            emit agentOutput(QStringLiteral("codex"), text);
            if (!text.trimmed().isEmpty()) m_statusLabel->setText(text.trimmed());
        });
        connect(m_codex, &CodexAppServerClient::modelsAvailable,
                this, &DesignChatPanel::updateModelCatalog);
        connect(m_codex, &CodexAppServerClient::toolCallRequested,
                this, &DesignChatPanel::codexToolRequested);
        connect(m_codex, &CodexAppServerClient::turnFinished, this,
                [this](bool ok, const QString& detail) {
            // Stop finalizes the UI synchronously.  Ignore a late completion
            // notification from the process being terminated so it cannot
            // flip the button back to a completed/running state.
            if (!m_agentRunning) return;
            finalizeCurrentBubble();
            m_agentRunning = false;
            updateRunControl(false);
            m_progress->hide();
            m_statusLabel->setText(ok ? "Codex complete" : "Codex failed: " + detail);
            emit agentFinished(QStringLiteral("codex"), ok);
        });
    }
    if (m_codex->state() == CodexAppServerClient::State::Stopped
        || m_codex->state() == CodexAppServerClient::State::Failed) {
        QFileInfo projectInfo(m_projectPath);
        const QString root = m_projectPath.isEmpty()
            ? QDir::home().filePath(QStringLiteral("DesignStudio"))
            : (projectInfo.isDir() ? projectInfo.absoluteFilePath()
                                   : projectInfo.absolutePath());
        QDir().mkpath(root);
        applyCodexOptions();
        m_codex->start(root, codexDynamicTools(), codexInstructions());
    }
}

QString DesignChatPanel::referenceWorkspaceRoot() const
{
    QFileInfo projectInfo(m_projectPath);
    const QString root = m_projectPath.isEmpty()
        ? QDir::home().filePath(QStringLiteral("DesignStudio"))
        : (projectInfo.isDir() ? projectInfo.absoluteFilePath()
                               : projectInfo.absolutePath());
    return QDir(root).absolutePath();
}

QJsonArray DesignChatPanel::referenceInputItems() const
{
    QJsonArray items;
    QFile manifestFile(m_attachedReferenceManifest);
    if (manifestFile.open(QIODevice::ReadOnly)) {
        const QJsonObject manifest = QJsonDocument::fromJson(manifestFile.readAll()).object();
        manifestFile.close();
        if (manifest.value(QStringLiteral("schema")).toString()
            == QStringLiteral("design-studio.reference-image-set/1")) {
            const QDir root(QFileInfo(m_attachedReferenceManifest).absolutePath());
            for (const QJsonValue& value : manifest.value(QStringLiteral("sources")).toArray()) {
                const QJsonObject source = value.toObject();
                const QString path = root.filePath(source.value(QStringLiteral("cropped_path")).toString());
                items.append(QJsonObject{
                    {QStringLiteral("type"), QStringLiteral("localImage")},
                    {QStringLiteral("path"), QFileInfo(path).absoluteFilePath()},
                    {QStringLiteral("detail"), source.value(QStringLiteral("input_detail")).toString(QStringLiteral("low"))}
                });
            }
            return items;
        }
    }
    for (const QString& page : m_attachedReferencePages) {
        if (!QFileInfo::exists(page)) continue;
        items.append(QJsonObject{
            {QStringLiteral("type"), QStringLiteral("localImage")},
            {QStringLiteral("path"), QFileInfo(page).absoluteFilePath()},
            {QStringLiteral("detail"), QStringLiteral("low")}
        });
    }
    return items;
}

static QString sha256Path(const QString& path)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) return {};
    QCryptographicHash hash(QCryptographicHash::Sha256);
    if (!hash.addData(&file)) return {};
    return QString::fromLatin1(hash.result().toHex());
}

bool DesignChatPanel::validateAttachedReference(QString* error) const
{
    if (m_attachedReferenceManifest.isEmpty()) return true;
    QFile file(m_attachedReferenceManifest);
    if (!file.open(QIODevice::ReadOnly)) {
        if (error) *error = QStringLiteral("reference manifest is missing");
        return false;
    }
    QJsonParseError parseError;
    const QJsonObject manifest = QJsonDocument::fromJson(file.readAll(), &parseError).object();
    if (parseError.error != QJsonParseError::NoError) {
        if (error) *error = QStringLiteral("reference manifest is invalid JSON");
        return false;
    }
    if (manifest.value(QStringLiteral("schema")).toString()
        != QStringLiteral("design-studio.reference-image-set/1")) return true;
    if (manifest.value(QStringLiteral("authority")).toString()
            != QStringLiteral("inspiration-and-visual-evidence-only")
        || manifest.value(QStringLiteral("measurement_status")).toString()
            != QStringLiteral("uncalibrated")
        || manifest.value(QStringLiteral("millimetre_inference_allowed")).toBool(true)) {
        if (error) *error = QStringLiteral("uncalibrated images attempted to claim CAD authority");
        return false;
    }
    const QDir root(QFileInfo(m_attachedReferenceManifest).absolutePath());
    for (const QJsonValue& value : manifest.value(QStringLiteral("sources")).toArray()) {
        const QJsonObject source = value.toObject();
        const QString original = root.filePath(source.value(QStringLiteral("stored_original_path")).toString());
        const QString crop = root.filePath(source.value(QStringLiteral("cropped_path")).toString());
        if (sha256Path(original) != source.value(QStringLiteral("source_sha256")).toString()
            || sha256Path(crop) != source.value(QStringLiteral("crop_sha256")).toString()) {
            if (error) *error = QStringLiteral("reference hash is stale for %1")
                                    .arg(source.value(QStringLiteral("asset_id")).toString());
            return false;
        }
    }
    return true;
}

void DesignChatPanel::attachReferenceImages()
{
    if (m_agentRunning) {
        appendSystem(QStringLiteral("Stop the active Codex turn before attaching new references."));
        return;
    }
    const QStringList sources = QFileDialog::getOpenFileNames(
        this, QStringLiteral("Attach visual reference images"), QDir::homePath(),
        QStringLiteral("Images (*.png *.PNG *.jpg *.JPG *.jpeg *.JPEG *.webp *.WEBP)"));
    if (sources.isEmpty()) return;
    const QString importer = swarmPath(QStringLiteral("tools/reference_image_set.py"));
    const QString python = QStandardPaths::findExecutable(QStringLiteral("python3"));
    if (python.isEmpty() || !QFileInfo::exists(importer)) {
        appendSystem(QStringLiteral("Reference-image importer is not installed."));
        return;
    }
    const QString outputRoot = QDir(referenceWorkspaceRoot()).filePath(QStringLiteral("designstudio_references"));
    QStringList args{importer, QStringLiteral("--output-root"), outputRoot,
                     QStringLiteral("--set-name"), QStringLiteral("visual-reference")};
    for (const QString& source : sources)
        args << QStringLiteral("--source") << source;
    QProcess process;
    process.start(python, args);
    if (!process.waitForStarted(5000) || !process.waitForFinished(120000)) {
        process.kill();
        appendSystem(QStringLiteral("Reference-image import did not complete."));
        return;
    }
    QJsonParseError parseError;
    const QJsonObject result = QJsonDocument::fromJson(process.readAllStandardOutput(), &parseError).object();
    if (process.exitCode() != 0 || parseError.error != QJsonParseError::NoError
        || !result.value(QStringLiteral("ok")).toBool()) {
        appendSystem(QStringLiteral("Reference-image import failed: %1")
                         .arg(QString::fromUtf8(process.readAllStandardError()).trimmed()));
        return;
    }
    m_attachedReferencePdf.clear();
    m_attachedReferencePages.clear();
    m_attachedReferenceManifest = result.value(QStringLiteral("manifest_path")).toString();
    m_attachedReferenceLabel = QStringLiteral("%1 reference images").arg(sources.size());
    QString validationError;
    if (!validateAttachedReference(&validationError)) {
        m_attachedReferenceManifest.clear();
        appendSystem(QStringLiteral("Reference image set rejected: %1").arg(validationError));
        return;
    }
    m_attachButton->setChecked(true);
    m_attachButton->setText(QStringLiteral("%1 images").arg(sources.size()));
    m_attachButton->setToolTip(QStringLiteral("%1 immutable originals; %2 original-detail representatives")
                                   .arg(sources.size())
                                   .arg(result.value(QStringLiteral("representative_count")).toInt()));
    appendSystem(QStringLiteral(
        "Attached %1 immutable image originals. Browser chrome is stored only as a derived crop; "
        "%2 representative views will be sent at original detail and the remainder at low detail. "
        "Pixels remain uncalibrated visual evidence and cannot authorize millimetres.")
        .arg(sources.size()).arg(result.value(QStringLiteral("representative_count")).toInt()));
    m_input->setFocus();
}

void DesignChatPanel::attachReferencePdf()
{
    if (m_agentRunning) {
        appendSystem(QStringLiteral("Stop the active Codex turn before attaching a new reference."));
        return;
    }

    const QString source = QFileDialog::getOpenFileName(
        this, QStringLiteral("Attach visual reference PDF"), QDir::homePath(),
        QStringLiteral("PDF files (*.pdf *.PDF);;All files (*)"));
    if (source.isEmpty()) return;

    QFile sourceFile(source);
    if (!sourceFile.open(QIODevice::ReadOnly)) {
        appendSystem(QStringLiteral("Could not read the selected reference PDF: %1")
                         .arg(source));
        return;
    }
    const QByteArray digestBytes = QCryptographicHash::hash(
        sourceFile.readAll(), QCryptographicHash::Sha256);
    const QString digest = QString::fromLatin1(digestBytes.toHex());
    sourceFile.close();

    QString safeName = QFileInfo(source).completeBaseName();
    safeName.replace(QRegularExpression(QStringLiteral("[^A-Za-z0-9_.-]+")),
                     QStringLiteral("_"));
    if (safeName.isEmpty()) safeName = QStringLiteral("reference");
    const QString refDir = QDir(referenceWorkspaceRoot()).filePath(
        QStringLiteral("designstudio_references/%1-%2")
            .arg(safeName, digest.left(12)));
    if (!QDir().mkpath(refDir)) {
        appendSystem(QStringLiteral("Could not create the reference workspace: %1")
                         .arg(refDir));
        return;
    }

    const QString copiedPdf = QDir(refDir).filePath(QFileInfo(source).fileName());
    if (QFileInfo(source).canonicalFilePath() != QFileInfo(copiedPdf).canonicalFilePath()) {
        QFile::remove(copiedPdf);
        if (!QFile::copy(source, copiedPdf)) {
            appendSystem(QStringLiteral("Could not copy the reference PDF into the active workspace."));
            return;
        }
    }

    // Codex app-server accepts localImage UserInput items, so render each PDF
    // page into the same workspace. Prefer Poppler and keep ImageMagick as a
    // portable fallback for installations that do not ship pdftoppm.
    const QString pagePrefix = QDir(refDir).filePath(QStringLiteral("page"));
    const QString pdftoppm = QStandardPaths::findExecutable(QStringLiteral("pdftoppm"));
    int renderCode = -1;
    if (!pdftoppm.isEmpty()) {
        renderCode = QProcess::execute(pdftoppm,
            {QStringLiteral("-png"), QStringLiteral("-r"), QStringLiteral("110"),
             copiedPdf, pagePrefix});
    }
    if (renderCode != 0) {
        const QString magick = QStandardPaths::findExecutable(QStringLiteral("magick"));
        if (!magick.isEmpty()) {
            const QString numberedPrefix = QDir(refDir).filePath(QStringLiteral("page-%03d.png"));
            renderCode = QProcess::execute(magick,
                {QStringLiteral("-density"), QStringLiteral("110"), copiedPdf,
                 QStringLiteral("-quality"), QStringLiteral("88"), numberedPrefix});
        }
    }

    QStringList pages = QDir(refDir).entryList(
        {QStringLiteral("page-*.png")}, QDir::Files, QDir::Name);
    QCollator collator;
    collator.setNumericMode(true);
    std::sort(pages.begin(), pages.end(), [&collator](const QString& a, const QString& b) {
        return collator.compare(a, b) < 0;
    });
    if (renderCode != 0 || pages.isEmpty()) {
        appendSystem(QStringLiteral(
            "Reference copied, but page rendering failed. Install Poppler (pdftoppm) or ImageMagick, then retry."));
        return;
    }

    m_attachedReferencePdf = QFileInfo(copiedPdf).absoluteFilePath();
    m_attachedReferencePages.clear();
    for (const QString& page : pages)
        m_attachedReferencePages.append(QDir(refDir).filePath(page));

    QJsonArray pageRecords;
    for (const QString& page : m_attachedReferencePages)
        pageRecords.append(QJsonObject{{QStringLiteral("path"), page}});
    const QJsonObject referenceManifest{
        {QStringLiteral("schema"), QStringLiteral("designstudio.reference/1")},
        {QStringLiteral("pdf"), m_attachedReferencePdf},
        {QStringLiteral("sha256"), digest},
        {QStringLiteral("authority"), QStringLiteral("inspiration-only")},
        {QStringLiteral("pageCount"), m_attachedReferencePages.size()},
        {QStringLiteral("pages"), pageRecords},
        {QStringLiteral("createdAtUtc"), QDateTime::currentDateTimeUtc().toString(Qt::ISODate)}
    };
    m_attachedReferenceManifest = QDir(refDir).filePath(QStringLiteral("reference.manifest.json"));
    m_attachedReferenceLabel = QFileInfo(source).fileName();
    QFile manifestFile(m_attachedReferenceManifest);
    if (manifestFile.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        manifestFile.write(QJsonDocument(referenceManifest).toJson(QJsonDocument::Indented));
        manifestFile.close();
    }

    m_attachButton->setChecked(true);
    m_attachButton->setText(QStringLiteral("Attached %1").arg(m_attachedReferencePages.size()));
    m_attachButton->setToolTip(QStringLiteral("Reference ready: %1\nClick to replace it")
                                    .arg(m_attachedReferencePdf));
    appendSystem(QStringLiteral(
        "Attached %1 (%2 page images) to the next Codex turn.\n"
        "The original PDF and SHA-256 manifest stay in the active workspace; pages are visual evidence only.\n"
        "Send a prompt to have Codex interpret the reference and propose approval-gated CAD/electronics work.")
                     .arg(QFileInfo(source).fileName())
                     .arg(m_attachedReferencePages.size()));
    m_input->setFocus();
}

void DesignChatPanel::presentCodexApproval(const QString& token, const QString& stage,
                                           const QString& summary,
                                           const QJsonObject& arguments) {
    m_approvalToken = token;
    m_approvalTitle->setText(QStringLiteral("Proposed operation: %1").arg(stage));
    QStringList parameterLines;
    for (auto it = arguments.begin(); it != arguments.end(); ++it) {
        QString label = it.key();
        label.replace(QLatin1Char('_'), QLatin1Char(' '));
        QString value;
        if (it.value().isDouble()) value = QString::number(it.value().toDouble(), 'g', 8);
        else if (it.value().isString()) value = it.value().toString();
        else value = QStringLiteral("See Technical Details");
        if (it.key().endsWith(QStringLiteral("_mm"))) value += QStringLiteral(" mm");
        else if (it.key().endsWith(QStringLiteral("_v"))) value += QStringLiteral(" V");
        parameterLines.append(QStringLiteral("%1: %2").arg(label, value));
    }
    QString dimensionDiagram;
    if (arguments.contains(QStringLiteral("width_mm"))
        && arguments.contains(QStringLiteral("depth_mm"))
        && arguments.contains(QStringLiteral("height_mm"))) {
        dimensionDiagram = QStringLiteral(
            "\n\nDimension order: Width × Depth × Height\n"
            "        ┌──────── Width ────────┐\n"
            "       /                       /│\n"
            "      └───────────────────────┘ │ Height\n"
            "       └────── Depth ───────────┘\n"
            "%1 × %2 × %3 mm")
            .arg(arguments.value(QStringLiteral("width_mm")).toDouble())
            .arg(arguments.value(QStringLiteral("depth_mm")).toDouble())
            .arg(arguments.value(QStringLiteral("height_mm")).toDouble());
    }
    m_approvalSummary->setText(summary + QStringLiteral("\n\n")
        + parameterLines.join(QLatin1Char('\n')) + dimensionDiagram);
    if (m_technicalDetails)
        m_technicalDetails->append(QString::fromUtf8(
            QJsonDocument(arguments).toJson(QJsonDocument::Indented)));
    m_approvalFrame->show();
    m_approvalFrame->raise();
}

void DesignChatPanel::completeCodexTool(const QString& token, bool success,
                                        const QString& text) {
    if (m_codex) m_codex->completeToolCall(token, success, text);
    if (m_technicalDetails) {
        m_technicalDetails->append(
            QStringLiteral("%1\n%2").arg(success ? QStringLiteral("SUCCESS")
                                                  : QStringLiteral("FAILED"), text));
    }
    QString summary = success ? QStringLiteral("Operation completed")
                              : QStringLiteral("Operation failed");
    const QJsonDocument document = QJsonDocument::fromJson(text.toUtf8());
    if (document.isObject())
        summary = document.object().value(QStringLiteral("message")).toString(summary);
    else if (!text.trimmed().isEmpty() && text.size() < 240)
        summary = text.trimmed();
    addMessage(ChatMessage::Tool, summary, QStringLiteral("DesignStudio"));
}

void DesignChatPanel::completeCodexTool(const QString& token, bool success,
                                        const QJsonArray& contentItems) {
    if (m_codex) m_codex->completeToolCall(token, success, contentItems);
}

void DesignChatPanel::runCodexDatasheetExtraction(const QString& token,
                                                  const QString& source,
                                                  const QString& mpn) {
    if (m_datasheetProc && m_datasheetProc->state() != QProcess::NotRunning) {
        completeCodexTool(token, false, "Another datasheet extraction is already running.");
        return;
    }
    QString safeMpn = mpn.trimmed();
    safeMpn.replace(QRegularExpression(QStringLiteral("[^A-Za-z0-9_.-]")), QStringLiteral("_"));
    if (safeMpn.isEmpty()) {
        completeCodexTool(token, false, "An exact manufacturer part number is required.");
        return;
    }
    const QString outputDir = QDir::home().filePath(
        QStringLiteral(".local/share/designstudio/datasets/component-previews/proposals"));
    if (!QDir().mkpath(outputDir)) {
        completeCodexTool(token, false, "Could not create the component proposal directory.");
        return;
    }
    const QString output = QDir(outputDir).filePath(safeMpn + QStringLiteral(".json"));
    const QString script = swarmPath("tools/extract_component_datasheet.py");
    if (!QFile::exists(script)) {
        completeCodexTool(token, false, "The controlled datasheet extraction tool is not installed.");
        return;
    }

    m_datasheetProc = new QProcess(this);
    m_datasheetProc->setProcessChannelMode(QProcess::SeparateChannels);
    m_datasheetProc->setWorkingDirectory(SWARM_ROOT);
    QProcessEnvironment environment = QProcessEnvironment::systemEnvironment();
    environment.insert(QStringLiteral("PYTHONPATH"), SWARM_ROOT);
    m_datasheetProc->setProcessEnvironment(environment);
    connect(m_datasheetProc, &QProcess::readyReadStandardOutput, this, [this] {
        const QString outputText = QString::fromUtf8(m_datasheetProc->readAllStandardOutput());
        if (m_technicalDetails) m_technicalDetails->append(outputText.toHtmlEscaped());
        emit agentOutput(QStringLiteral("codex-datasheet-extractor"), outputText);
    });
    connect(m_datasheetProc, &QProcess::readyReadStandardError, this, [this] {
        const QString errorText = QString::fromUtf8(m_datasheetProc->readAllStandardError());
        if (m_technicalDetails) m_technicalDetails->append(errorText.toHtmlEscaped());
        emit agentOutput(QStringLiteral("codex-datasheet-extractor"), errorText);
    });
    connect(m_datasheetProc,
            qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this,
            [this, token, output](int code, QProcess::ExitStatus status) {
        const bool ok = code == 0 && status == QProcess::NormalExit && QFile::exists(output);
        QJsonObject verification;
        QString manifestPath;
        QString previewState;
        if (ok) {
            QFile proposal(output);
            if (proposal.open(QIODevice::ReadOnly)) {
                const QJsonObject component = QJsonDocument::fromJson(proposal.readAll()).object();
                verification = component.value(QStringLiteral("extraction")).toObject()
                                   .value(QStringLiteral("verification")).toObject();
                manifestPath = component.value(QStringLiteral("preview")).toObject()
                                   .value(QStringLiteral("manifest_uri")).toString();
                previewState = component.value(QStringLiteral("preview")).toObject()
                                   .value(QStringLiteral("state")).toString();
            }
        }
        if (ok && !manifestPath.isEmpty()) showComponentPreview(manifestPath);
        QJsonObject result{{QStringLiteral("ok"), ok},
                           {QStringLiteral("proposal_path"), output},
                           {QStringLiteral("preview_manifest"), manifestPath},
                           {QStringLiteral("preview_state"), previewState},
                           {QStringLiteral("publishable"), previewState == QStringLiteral("ready")},
                           {QStringLiteral("placement_allowed"),
                                verification.value(QStringLiteral("placement_allowed")).toBool(false)},
                           {QStringLiteral("review_required"), true},
                           {QStringLiteral("warnings"),
                                verification.value(QStringLiteral("warnings")).toArray()}};
        result.insert(QStringLiteral("message"), ok
            ? (previewState == QStringLiteral("ready")
                ? QStringLiteral("Component preview is ready. Review the 2D footprint, 3D package, assumptions, and validation results before publishing.")
                : previewState == QStringLiteral("published")
                ? QStringLiteral("Existing approved footprint and STEP assets were reused from the component library.")
                : QStringLiteral("A partial component preview was created, but missing or unproven dimensions block STEP generation and publication."))
            : QStringLiteral("Datasheet extraction failed with exit code %1.").arg(code));
        completeCodexTool(token, ok,
            QString::fromUtf8(QJsonDocument(result).toJson(QJsonDocument::Compact)));
        m_statusLabel->setText(result.value(QStringLiteral("message")).toString());
        m_datasheetProc->deleteLater();
        m_datasheetProc = nullptr;
    });

    QStringList arguments{script};
    if (!source.trimmed().isEmpty()) arguments << source.trimmed();
    arguments << QStringLiteral("--mpn") << mpn.trimmed()
              << QStringLiteral("--output") << output;
    m_statusLabel->setText(QStringLiteral("Sol medium is inspecting %1; Luna xhigh will author and execute its CAD program…").arg(mpn));
    m_datasheetProc->start(QStringLiteral("python3"), arguments);
    if (!m_datasheetProc->waitForStarted(3000)) {
        completeCodexTool(token, false, "Could not start the controlled datasheet extractor.");
        m_datasheetProc->deleteLater();
        m_datasheetProc = nullptr;
    }
}

void DesignChatPanel::showComponentPreview(const QString& manifestPath) {
    QFile file(manifestPath);
    if (!file.open(QIODevice::ReadOnly)) return;
    const QJsonObject manifest = QJsonDocument::fromJson(file.readAll()).object();
    if (manifest.value(QStringLiteral("schema")).toString()
            != QStringLiteral("design-studio.component-preview/1")) return;
    QDialog dialog(this);
    dialog.setWindowTitle(QStringLiteral("Component CAD Preview · %1")
                          .arg(manifest.value(QStringLiteral("component_mpn")).toString()));
    dialog.resize(980, 620);
    auto* layout = new QVBoxLayout(&dialog);
    const QString manifestState = manifest.value(QStringLiteral("state")).toString();
    const bool blocked = manifestState == QStringLiteral("blocked");
    auto* summary = new QLabel(blocked
        ? QStringLiteral("Partial preview — publication is blocked until the missing datasheet evidence below is supplied.")
        : manifestState == QStringLiteral("published")
        ? QStringLiteral("Approved assets are already published in the component library; the active PCB is unchanged until placement is approved.")
        : QStringLiteral("Preview only — the component library and active PCB are unchanged. Publication requires a separate approval."));
    summary->setWordWrap(true);
    layout->addWidget(summary);
    auto* images = new QHBoxLayout;
    auto addImage = [&](const QString& title, const QString& path) {
        auto* group = new QGroupBox(title, &dialog);
        auto* box = new QVBoxLayout(group);
        auto* image = new QLabel(group);
        image->setAlignment(Qt::AlignCenter);
        const QPixmap pixmap(path);
        image->setPixmap(pixmap.scaled(430, 430, Qt::KeepAspectRatio,
                                       Qt::SmoothTransformation));
        box->addWidget(image);
        images->addWidget(group, 1);
    };
    addImage(QStringLiteral("2D footprint and courtyard"),
             manifest.value(QStringLiteral("footprint_preview")).toString());
    const QJsonObject views = manifest.value(QStringLiteral("model_previews")).toObject();
    addImage(QStringLiteral("3D package · top projection"),
             views.value(QStringLiteral("top")).toString());
    layout->addLayout(images, 1);
    const QJsonObject validation = manifest.value(QStringLiteral("validation")).toObject();
    auto* details = new QLabel(QStringLiteral(
        "Footprint: %1   Pin mapping: %2   B-Rep: %3   STEP round-trip: %4   Solids: %5")
        .arg(validation.value(QStringLiteral("footprint")).toString(),
             validation.value(QStringLiteral("pin_pad_map")).toString(),
             validation.value(QStringLiteral("brep")).toString(),
             validation.value(QStringLiteral("step_roundtrip")).toString())
        .arg(validation.value(QStringLiteral("solid_count")).toInt()));
    details->setWordWrap(true);
    layout->addWidget(details);
    QStringList evidenceLines;
    for (const auto& value : manifest.value(QStringLiteral("assumptions")).toArray()) {
        const QJsonObject assumption = value.toObject();
        evidenceLines.append(QStringLiteral("Assumption · %1: %2 [%3]")
            .arg(assumption.value(QStringLiteral("domain")).toString(),
                 assumption.value(QStringLiteral("statement")).toString(),
                 assumption.value(QStringLiteral("status")).toString()));
    }
    const QJsonObject engineering = manifest.value(QStringLiteral("engineering_checks")).toObject();
    for (auto it = engineering.begin(); it != engineering.end(); ++it) {
        const QString status = it.value().toObject().value(QStringLiteral("status")).toString();
        if (status == QStringLiteral("fail") || status == QStringLiteral("incomplete"))
            evidenceLines.append(QStringLiteral("Unresolved %1 gate: %2").arg(it.key(), status));
    }
    if (!evidenceLines.isEmpty()) {
        auto* evidenceLabel = new QLabel(evidenceLines.join('\n'));
        evidenceLabel->setWordWrap(true);
        evidenceLabel->setStyleSheet(QStringLiteral("color:#8A4B08;"));
        layout->addWidget(evidenceLabel);
    }
    if (blocked) {
        QStringList blockers;
        for (const auto& value : manifest.value(QStringLiteral("blockers")).toArray())
            blockers.append(QStringLiteral("• %1").arg(value.toString()));
        auto* blockerLabel = new QLabel(blockers.join('\n'));
        blockerLabel->setWordWrap(true);
        blockerLabel->setStyleSheet(QStringLiteral("color:#A61B1B;font-weight:600;"));
        layout->addWidget(blockerLabel);
    }
    auto* buttons = new QDialogButtonBox(QDialogButtonBox::Close, &dialog);
    connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    layout->addWidget(buttons);
    dialog.exec();
}

void DesignChatPanel::runCodexComponentPublication(const QString& token,
                                                    const QString& manifestPath) {
    if (m_componentPublishProc && m_componentPublishProc->state() != QProcess::NotRunning) {
        completeCodexTool(token, false, "Another component publication is already running.");
        return;
    }
    const QString script = swarmPath(QStringLiteral("tools/publish_component_assets.py"));
    if (!QFile::exists(script) || !QFile::exists(manifestPath)) {
        completeCodexTool(token, false, "The component preview or publication tool is unavailable.");
        return;
    }
    m_componentPublishProc = new QProcess(this);
    m_componentPublishProc->setWorkingDirectory(SWARM_ROOT);
    m_componentPublishProc->setProcessChannelMode(QProcess::SeparateChannels);
    QProcessEnvironment environment = QProcessEnvironment::systemEnvironment();
    environment.insert(QStringLiteral("PYTHONPATH"), SWARM_ROOT);
    m_componentPublishProc->setProcessEnvironment(environment);
    connect(m_componentPublishProc,
            qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this,
            [this, token](int code, QProcess::ExitStatus status) {
        const QString output = QString::fromUtf8(
            m_componentPublishProc->readAllStandardOutput()).trimmed();
        const QString error = QString::fromUtf8(
            m_componentPublishProc->readAllStandardError()).trimmed();
        const bool ok = code == 0 && status == QProcess::NormalExit;
        completeCodexTool(token, ok, ok ? output : error);
        m_statusLabel->setText(ok
            ? QStringLiteral("Component symbol, footprint, and STEP model published.")
            : QStringLiteral("Component publication failed."));
        m_componentPublishProc->deleteLater();
        m_componentPublishProc = nullptr;
    });
    m_statusLabel->setText(QStringLiteral("Publishing approved component assets…"));
    m_componentPublishProc->start(QStringLiteral("python3"),
                                  {script, QFileInfo(manifestPath).absoluteFilePath()});
    if (!m_componentPublishProc->waitForStarted(3000)) {
        completeCodexTool(token, false, "Could not start the controlled component publisher.");
        m_componentPublishProc->deleteLater();
        m_componentPublishProc = nullptr;
    }
}

void DesignChatPanel::runCodexDatasheetProductBuild(const QString& token,
                                                     const QString& intent) {
    if (m_productBuildProc && m_productBuildProc->state() != QProcess::NotRunning) {
        completeCodexTool(token, false, "Another product datasheet build is already running.");
        return;
    }
    if (m_projectPath.isEmpty() || intent.trimmed().isEmpty()) {
        completeCodexTool(token, false,
            "An open electronics document and a nonempty product intent are required.");
        return;
    }
    const QString script = swarmPath("swarm/memory/harness_cli.py");
    if (!QFile::exists(script)) {
        completeCodexTool(token, false, "The controlled datasheet product builder is not installed.");
        return;
    }

    m_productBuildProc = new QProcess(this);
    m_productBuildProc->setProcessChannelMode(QProcess::SeparateChannels);
    m_productBuildProc->setWorkingDirectory(SWARM_ROOT);
    QProcessEnvironment environment = QProcessEnvironment::systemEnvironment();
    environment.insert(QStringLiteral("PYTHONPATH"), SWARM_ROOT);
    m_productBuildProc->setProcessEnvironment(environment);
    connect(m_productBuildProc, &QProcess::readyReadStandardOutput, this, [this] {
        const QString output = QString::fromUtf8(m_productBuildProc->readAllStandardOutput());
        if (m_technicalDetails) m_technicalDetails->append(output.toHtmlEscaped());
        emit agentOutput(QStringLiteral("datasheet-product-builder"), output);
    });
    connect(m_productBuildProc, &QProcess::readyReadStandardError, this, [this] {
        const QString output = QString::fromUtf8(m_productBuildProc->readAllStandardError());
        if (m_technicalDetails) m_technicalDetails->append(output.toHtmlEscaped());
        emit agentOutput(QStringLiteral("datasheet-product-builder"), output);
    });
    connect(m_productBuildProc,
            qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this,
            [this, token](int code, QProcess::ExitStatus status) {
        // The harness deliberately returns a non-zero code for a partial supplier
        // result, even though it has atomically written every footprint it could
        // extract. Reload that useful draft as well; unavailable parts stay in the
        // unresolved list and must not discard the parts Luna did produce.
        if (status == QProcess::NormalExit)
            emit nativeJobRequested(QStringLiteral("reload-generated-project"));
        const int extractedCount = std::count_if(
            m_model->footprints.cbegin(), m_model->footprints.cend(),
            [](const ProjFootprint& footprint) {
                return !footprint.mpn.isEmpty()
                    && !footprint.mpn.startsWith(QStringLiteral("PROVISIONAL-"))
                    && !footprint.pads.isEmpty();
            });
        const bool ok = status == QProcess::NormalExit && extractedCount > 0;
        const bool partial = ok && (code != 0 || !m_model->unresolvedComponents.isEmpty());
        const QJsonObject result{
            {QStringLiteral("ok"), ok},
            {QStringLiteral("message"), ok
                ? (partial
                    ? QStringLiteral("Available SMT/THT footprints were extracted and placed; unavailable components remain visibly unresolved.")
                    : QStringLiteral("Supplier selection and component previews completed; approved published SMT/THT assets were placed."))
                : QStringLiteral("The datasheet product build did not produce placeable footprints (exit code %1).").arg(code)},
            {QStringLiteral("components"), extractedCount},
            {QStringLiteral("unresolved_components"), m_model->unresolvedComponents.size()},
            {QStringLiteral("partial"), partial},
            {QStringLiteral("placement_mode"), m_model->placementState.value(QStringLiteral("mode"))}
        };
        completeCodexTool(token, ok,
            QString::fromUtf8(QJsonDocument(result).toJson(QJsonDocument::Compact)));
        m_statusLabel->setText(result.value(QStringLiteral("message")).toString());
        m_productBuildProc->deleteLater();
        m_productBuildProc = nullptr;
    });
    m_statusLabel->setText(QStringLiteral("Selecting exact parts and extracting datasheets…"));
    m_productBuildProc->start(QStringLiteral("python3"), QStringList{
        script, intent.trimmed(), QStringLiteral("--project"), m_projectPath});
    if (!m_productBuildProc->waitForStarted(3000)) {
        completeCodexTool(token, false, "Could not start the controlled datasheet product builder.");
        m_productBuildProc->deleteLater();
        m_productBuildProc = nullptr;
    }
}

QJsonArray DesignChatPanel::codexDynamicTools() {
    auto function = [](const QString& name, const QString& description,
                       const QJsonObject& properties, const QJsonArray& required) {
        return QJsonObject{
            {"type", "function"}, {"name", name}, {"description", description},
            {"inputSchema", QJsonObject{{"type", "object"},
                {"additionalProperties", false}, {"properties", properties},
                {"required", required}}}
        };
    };
    auto number = [](double minimum, double maximum) {
        return QJsonObject{{"type", "number"}, {"minimum", minimum}, {"maximum", maximum}};
    };
    const QJsonObject mechanicalProgramProperties{
        {QStringLiteral("program_path"), QJsonObject{
            {QStringLiteral("type"), QStringLiteral("string")},
            {QStringLiteral("minLength"), 1},
            {QStringLiteral("maxLength"), 4096},
            {QStringLiteral("description"), QStringLiteral(
                "Workspace-relative path to an existing design-studio.mechanical-cad-program/1 JSON file.")}}},
        {QStringLiteral("program"), QJsonObject{
            {QStringLiteral("type"), QStringLiteral("object")},
            {QStringLiteral("description"), QStringLiteral(
                "Inline design-studio.mechanical-cad-program/1 object. The host validates and persists it before construction.")}}}
    };
    return QJsonArray{
        function("read_product_state", "Read authoritative workspace, CAD, electronics and gate state.", {}, {}),
        function("capture_workspace_view", "Capture the visible mechanical, schematic or PCB view for visual verification.",
                 {{"view", QJsonObject{{"type", "string"}, {"enum", QJsonArray{"mechanical", "schematic", "pcb", "evidence"}}}}},
                 {"view"}),
        function("create_product_workspace", "Propose creation of one unified DesignStudio industrial-product workspace.",
                 {{"name", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 120},
                                        {"pattern", QStringLiteral("^[^/\\\\]+$")},
                                        {"description", "Product display name only; never a filesystem path."}}},
                  {"description", QJsonObject{{"type", "string"}, {"maxLength", 2000}}}},
                 {"name", "description"}),
        function("apply_mechanical_stage", "Propose a deterministic sealed enclosure and legal PCB volume.",
                 {{"template", QJsonObject{{"type", "string"}, {"enum", QJsonArray{"rectangular", "injection_clamshell", "handheld"}}}},
                  {"width_mm", number(40, 400)}, {"depth_mm", number(30, 300)},
                  {"height_mm", number(15, 160)}, {"wall_mm", number(1, 8)}},
                 {"template", "width_mm", "depth_mm", "height_mm", "wall_mm"}),
        function("apply_mechanical_cad_program",
                 "Apply an approved typed mechanical CAD program as an editable FreeCAD feature tree, including native Sketcher constraints and assembly fit checks. Provide program for a new inline program, or program_path for an existing workspace JSON file.",
                 mechanicalProgramProperties,
                 {}),
        function("create_physical_design_session",
                 "Validate and persist an inside-out, outside-in or co-design session. Photos, renders and sketches are reference evidence only; verified measurements and FreeCAD B-Rep remain authoritative.",
                 {{"session_path", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 4096},
                     {"description", "Workspace-relative design-studio.physical-design-session/1 JSON path."}}},
                  {"session", QJsonObject{{"type", "object"},
                     {"description", "Inline design-studio.physical-design-session/1 object."}}}},
                 {}),
        function("create_physical_design_session_v2",
                 "Capture calibrated image/sketch evidence, hardware-first volumes, ergonomics, materials and manufacturing constraints for exactly three editable physical-design alternatives.",
                 {{"session_path", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 4096},
                     {"description", "Workspace-relative design-studio.physical-design-session/2 JSON path."}}},
                  {"session", QJsonObject{{"type", "object"},
                     {"description", "Inline design-studio.physical-design-session/2 object. Raster/sketch geometry_authority must be false."}}}},
                 {}),
        function("create_guided_physical_design_session",
                 "Convert the dedicated photo/sketch calibration, locked-volume, material and process form into a strict physical-design-session/2 without hand-authoring JSON.",
                 {{"capture", QJsonObject{{"type", "object"},
                     {"description", "Structured values emitted by the Physical Design panel; evidence is copied into the workspace after approval and remains non-authoritative."}}}},
                 {"capture"}),
        function("generate_constraint_candidates",
                 "Generate exactly compact, balanced and comfort controller alternatives (or three outcome-named alternatives for another product) as editable FreeCAD B-Reps. Hard failures are rejected before ranking; missing analyses remain incomplete.",
                 {{"session_path", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 4096},
                     {"description", "Workspace-relative physical-design-session/2 JSON path returned by create_physical_design_session_v2."}}}},
                 {"session_path"}),
        function("create_pcb_topology_study",
                 "Generate exactly three provisional electronics topologies—single rigid, rigid-flex and multiple rigid islands—from measured component positions, functional groups, nets and physical constraints. The user must approve any split.",
                 {{"source_path", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 4096},
                     {"description", "Workspace-relative dsproj, or JSON containing product_id, components, nets and topology constraints."}}},
                  {"study_id", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 128}}}},
                 {"source_path"}),
        function("derive_mechanical_component_requirements",
                 "Derive fastener, carrier, bearing, thermal and cable-retention requirements from recorded PCB islands, loads, motion, life and temperature inputs. Catalog selections and exact solver gates remain incomplete until evidenced.",
                 {{"source_path", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 4096},
                     {"description", "Workspace-relative JSON containing product_id and measured physical/mechanical inputs."}}},
                  {"requirements_id", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 128}}}},
                 {"source_path"}),
        function("export_physical_test_plan",
                 "Export one candidate-specific FreeCAD, STEP and STL grip-only buck for compact, balanced and comfort from explicit semantic regions or user-confirmed section planes. Creates a provisional digest-bound physical-test-plan/1.",
                 {{"session_path", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 4096}}},
                  {"request", QJsonObject{{"type", "object"},
                     {"description", "Structured plan metadata, material/print settings and explicit grip regions; synthetic_fixture must truthfully describe test fixtures."}}}},
                 {"session_path", "request"}),
        function("record_physical_observation",
                 "Record one anonymous adult participant/candidate observation using the fixed six-field 1-5 form. Candidate and grip-buck digests are taken from the selected plan, never from caller claims.",
                 {{"plan_path", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 4096}}},
                  {"observation", QJsonObject{{"type", "object"}}}},
                 {"plan_path", "observation"}),
        function("create_physical_candidate_decision",
                 "Create a physical candidate decision only after real, digest-bound small/medium/large adult observations prove that each participant tested all three candidates in counterbalanced order. Synthetic evidence is always rejected.",
                 {{"plan_path", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 4096}}},
                  {"observation_paths", QJsonObject{{"type", "array"}, {"minItems", 9},
                     {"items", QJsonObject{{"type", "string"}, {"minLength", 1}}}}},
                  {"decision_id", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 128}}},
                  {"observer", QJsonObject{{"type", "string"}, {"minLength", 1}}},
                  {"created_utc", QJsonObject{{"type", "string"}, {"minLength", 1}}}},
                 {"plan_path", "observation_paths", "decision_id", "observer", "created_utc"}),
        function("capture_selected_design_region",
                 "Capture exactly one currently selected FreeCAD object or face set as a digest-bound local redesign scope. This records intent but does not generate or replace geometry.",
                 {{"intent", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 32000}}},
                  {"max_expansion_mm", number(0, 10000)},
                  {"protected_semantic_ids", QJsonObject{{"type", "array"}, {"maxItems", 256},
                      {"items", QJsonObject{{"type", "string"}, {"minLength", 1}}}}},
                  {"physical_design_session_path", QJsonObject{{"type", "string"},
                      {"minLength", 1}, {"maxLength", 4096}}}},
                 {"intent", "max_expansion_mm"}),
        function("preview_local_redesign",
                 "Submit an optional inline typed CAD program for a captured local redesign, rebuild it as an editable sibling FreeCAD branch, prove selection scope/protected clearance/unselected identity, and generate six measured SVG plus six DXF B-Rep projections. Baseline remains visible.",
                 {{"redesign_path", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 4096}}},
                  {"program", QJsonObject{{"type", "object"},
                      {"description", "Inline design-studio.mechanical-cad-program/1 candidate for a captured contract. Omit only when the path is already program_ready."}}},
                  {"candidate_command_id", QJsonObject{{"type", "string"}, {"minLength", 1},
                      {"maxLength", 128}, {"pattern", "^[A-Za-z][A-Za-z0-9_.-]{0,127}$"}}}},
                 {"redesign_path"}),
        function("commit_local_redesign",
                 "Commit a previously verified local-redesign preview after rechecking all digests. Transfers the stable semantic ID, hides the original, and never deletes the baseline.",
                 {{"preview_receipt_path", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 4096}}}},
                 {"preview_receipt_path"}),
        function("capture_local_redesign_v2",
                 "Capture selected FaceN topology for a bounded local-redesign/2 patch. Records boundary-edge signatures, adjacent faces, a local frame, continuity and manufacturing constraints; it does not replace geometry.",
                 {{"intent", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 32000}}},
                  {"permitted_expansion_mm", number(0, 10000)},
                  {"continuity_required", QJsonObject{{"type", "string"}, {"enum", QJsonArray{"G0", "G1", "G2"}}}},
                  {"protected_objects", QJsonObject{{"type", "array"}, {"maxItems", 256},
                      {"items", QJsonObject{{"type", "object"}}}}},
                  {"manufacturing", QJsonObject{{"type", "object"}}},
                  {"redesign_id", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 128}}}},
                 {"intent", "permitted_expansion_mm"}),
        function("preview_local_redesign_v2",
                 "Build a topology-aware sibling patch from a typed candidate program, sew it with unchanged B-Rep topology, and verify boundary deviation, continuity, protected clearance and unrelated-object digests. Candidate faces are FaceN IDs in the candidate result.",
                 {{"redesign_path", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 4096}}},
                  {"program", QJsonObject{{"type", "object"}, {"description", "Inline mechanical-cad-program/2 candidate; omit only when path is already program_ready."}}},
                  {"candidate_command_id", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 128}}},
                  {"candidate_faces", QJsonObject{{"type", "array"}, {"minItems", 1}, {"items", QJsonObject{{"type", "string"}, {"pattern", "^Face[1-9][0-9]*$"}}}}}},
                 {"redesign_path"}),
        function("commit_local_redesign_v2",
                 "Commit a verified topology-aware patch after rechecking all digests. Transfers the stable semantic identity, hides but retains the baseline, and leaves a rollback receipt.",
                 {{"preview_receipt_path", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 4096}}}},
                 {"preview_receipt_path"}),
        function("discard_local_redesign_v2",
                 "Reject an uncommitted topology-aware sibling preview. Removes only objects recorded as preview-created, retains the baseline and audit receipt, and refuses committed branches.",
                 {{"preview_receipt_path", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 4096}}}},
                 {"preview_receipt_path"}),
        function("rollback_local_redesign_v2",
                 "Restore the retained baseline from a topology-aware local-redesign/2 commit receipt and hide the committed patch branch.",
                 {{"commit_receipt_path", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 4096}}}},
                 {"commit_receipt_path"}),
        function("generate_authoritative_drawings",
                 "Generate six OCCT HLR orthographic SVG/DXF views plus deterministic section, continuity, tolerance, interface and five-sheet engineering outputs from a FreeCAD B-Rep.",
                 {{"source_semantic_id", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 128}}},
                  {"output_directory", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 2048}}},
                  {"sections", QJsonObject{{"type", "array"}, {"maxItems", 32}}},
                  {"selected_faces", QJsonObject{{"type", "array"}, {"items", QJsonObject{{"type", "string"}, {"pattern", "^Face[1-9][0-9]*$"}}}}},
                  {"selected_edges", QJsonObject{{"type", "array"}, {"items", QJsonObject{{"type", "string"}, {"pattern", "^Edge[1-9][0-9]*$"}}}}},
                  {"package_id", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 128}}}},
                 {"source_semantic_id", "output_directory"}),
        function("generate_interaction_structure",
                 "Generate exact protected cavities and deterministic parametric ribs from an approved, revision-bound support specification.",
                 {{"spec_path", QJsonObject{{"type", "string"}, {"minLength", 1},
                     {"maxLength", 4096},
                     {"description", "Workspace-relative path to a design-studio.support-generation/1 JSON file."}}},
                  {"refinement", QJsonObject{{"type", "string"},
                     {"enum", QJsonArray{"local", "cloud"}},
                     {"description", "local is deterministic; cloud requests a non-authoritative MI300X candidate before exact host reconstruction."}}}},
                 {"spec_path"}),
        function("apply_electrical_stage", "Propose the concept schematic and evidence-marked industrial electronics.",
                 {{"input_min_v", number(5, 60)}, {"input_max_v", number(5, 60)},
                  {"family", QJsonObject{{"type", "string"}, {"enum", QJsonArray{
                      "synchronous_buck_converter", "precision_acquisition_board",
                      "wireless_sensor_2_4ghz", "usb_gigabit_high_speed_board",
                      "bldc_servo_controller", "industrial_condition_monitor",
                      "robotic_joint_capstone", "custom_concept"}}}},
                  {"interface", QJsonObject{{"type", "string"}, {"enum", QJsonArray{"RS-485", "CAN", "Ethernet"}}}},
                  {"axes", QJsonObject{{"type", "integer"}, {"minimum", 1}, {"maximum", 6}}},
                  {"motor_current_a", number(0.25, 10)},
                  {"sensors", QJsonObject{{"type", "array"}, {"minItems", 1}, {"maxItems", 8},
                      {"items", QJsonObject{{"type", "string"}}}}}},
                 {"input_min_v", "input_max_v", "interface"}),
        function("apply_pcb_stage", "Create a PCB placement draft first, or explicitly request optimized placement and routing.",
                 {{"width_mm", QJsonObject{{"type", "number"}, {"minimum", 1}}},
                  {"height_mm", QJsonObject{{"type", "number"}, {"minimum", 1}}},
                  {"layers", QJsonObject{{"type", "integer"}, {"enum", QJsonArray{2, 4, 6}}}},
                  {"placement_mode", QJsonObject{{"type", "string"},
                      {"enum", QJsonArray{"draft", "optimized"}},
                      {"description", "draft leaves placement unconstrained until /layout; optimized legalizes and routes"}}}},
                 {"width_mm", "height_mm", "layers"}),
        function("run_engineering_checks", "Run deterministic ERC, DRC, power, thermal, clearance and collision checks.", {}, {}),
        function("extract_component_datasheet", "Inspect an exact-MPN datasheet with Sol medium and build a review-only SMT/THT symbol, footprint, and STEP preview with Luna xhigh.",
                 {{"mpn", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 120}}},
                  {"source", QJsonObject{{"type", "string"}, {"maxLength", 2048},
                      {"description", "Optional direct HTTPS PDF URL or local PDF path. Omit to query configured DigiKey, Mouser and Nexar credentials."}}}},
                 {"mpn"}),
        function("publish_component_assets", "Publish an explicitly approved component preview containing its symbol, SMT/THT footprint, pin mapping, and validated STEP model.",
                 {{"manifest_path", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 4096},
                     {"description", "Exact preview_manifest path returned by extract_component_datasheet."}}}},
                 {"manifest_path"}),
        function("build_electronics_from_datasheets", "Select exact manufacturer parts for the approved product, query configured suppliers, let GPT-5.6 Luna extract SMT/THT footprints from datasheets, and place them as a PCB draft.",
                 {{"intent", QJsonObject{{"type", "string"}, {"minLength", 8}, {"maxLength", 4000},
                     {"description", "Complete approved product intent including family, supply, channels, interfaces, load and safety requirements."}}}},
                 {"intent"}),
        function("show_workspace_view", "Switch the live DesignStudio UI to the requested engineering view.",
                 {{"view", QJsonObject{{"type", "string"}, {"enum", QJsonArray{"mechanical", "schematic", "pcb", "pcb_3d", "evidence"}}}}},
                 {"view"}),
        function("commit_configuration", "Propose saving the current approved stages as an immutable child configuration.",
                 {{"message", QJsonObject{{"type", "string"}, {"minLength", 1}, {"maxLength", 500}}}},
                 {"message"})
    };
}

QString DesignChatPanel::codexInstructions() {
    return QStringLiteral(
        "You are the DesignStudio industrial-product agent. Use only the supplied DesignStudio "
        "dynamic tools for project mutations; never edit project files or execute FreeCAD Python. "
        "Read state first. Call create_product_workspace at most once in a turn and pass a short "
        "product display name, never a path. If it reports reused=true, continue that workspace. "
        "For a new product, preserve the discovered application family when creating the workspace "
        "and electrical stage; never substitute a condition monitor for another product. Then create "
        "mechanical, electrical, "
        "PCB draft, placement optimization, verification and commit stages in that order. Each mutating tool waits for user "
        "approval. After every approved stage, show and capture the relevant view. "
        "If a tool fails, immediately read_product_state, preserve every completed stage, and retry "
        "only the failed tool with corrected arguments. Never create another workspace or repeat a "
        "successful mechanical/electrical stage as error recovery. Respect allowed dimensions "
        "returned by an optimized PCB error. Mechanical generation must not create generic component "
        "cylinders. For detailed mechanical construction, use apply_mechanical_cad_program with an "
        "inline design-studio.mechanical-cad-program/1 object; keep every dimension in millimetres, "
        "reference prior command IDs in order, attach drawing or image provenance, and include valid_shape "
        "or volume checks. For editable parametric profiles prefer sketch.create, sketch.line, sketch.circle, sketch.rectangle, and sketch.constraint.add; for multi-part fit use assembly.create, assembly.component, assembly.mate, assembly.check_clearance, and assembly.check_interference. Use FaceN/EdgeN first_reference and second_reference for face/edge mates only when the referenced topology is established by the preceding feature program. Use tolerance.stack.create, tolerance.stack.item, and tolerance.stack.check for signed nominal dimensions and worst_case or rss limits. Never emit arbitrary FreeCAD Python or GUI clicks. GPT-5.6 Sol medium visually selects exact package evidence; GPT-5.6 Luna xhigh "
        "For image-, object-photo-, or drawn-sketch-first work, author a physical-design-session contract before CAD. "
        "Treat every raster and sketch as inspiration, silhouette, layout, or measurement evidence only; it is never geometry authority. "
        "For a constraint-driven alternative study, call create_physical_design_session_v2 with calibrated image/sketch evidence, verified dimensions, locked hardware/PCB/battery/display/connector/cable volumes, human/service clearances, interaction objectives, desire priorities, materials, process rules, hard constraints and scoring weights; then call generate_constraint_candidates. The result is exactly three editable FreeCAD candidates. Hard failures are rejected before ranking, while absent exact solvers remain visible as incomplete and never count as passes. "
        "After the user confirms explicit grip regions, call export_physical_test_plan. Record only observations actually entered by a user with record_physical_observation. Never invent hand measurements, ratings, timestamps, observers or provenance. Keep the ranking provisional until create_physical_candidate_decision verifies at least one small, medium and large adult participant, all three candidates per participant, counterbalanced order, fresh geometry/buck digests and no synthetic evidence. Balanced is only the provisional choice or exact-score tie-breaker. "
        "After exact component bodies and locked interaction volumes exist, call create_pcb_topology_study before committing a board split. Compare single rigid, rigid-flex and multi-rigid harness layouts using measured connection length and interconnect penalties; incomplete SI, thermal, structural or manufacturing evidence never contributes a pass. Only the user may approve an island topology. Then call derive_mechanical_component_requirements using recorded loads, motion, life and thermal inputs. Source standard parts from verified manufacturer evidence and generate only custom interfaces in FreeCAD; never select a bearing, fastener, heatsink or mechanism from an AI estimate alone. "
        "For a selected-area improvement, use capture_local_redesign_v2 when exact FaceN topology replacement is requested; then call preview_local_redesign_v2 with the captured path, an inline mechanical-cad-program/2 candidate, its candidate command ID, and candidate FaceN IDs. Never edit a captured JSON directly. The v1 capture/preview tools remain available for legacy receipts. "
        "After a verified FreeCAD B-Rep exists, call generate_authoritative_drawings with its semantic ID and a workspace-relative output directory. The result is six OCCT-HLR SVG/DXF views, true sections, selected-boundary/continuity details, dimensions, tolerances, and five deterministic engineering sheets; drawings are evidence and never CAD input. "
        "Do not call commit_local_redesign until the user has inspected the sibling preview and measured SVG/DXF projections and explicitly approves replacement. If the user rejects a topology-aware preview, call discard_local_redesign_v2; if they request changes, discard the old preview and create a new typed preview from the unchanged captured scope. "
        "Never broaden selection scope, change protected objects, overwrite the target digest, delete the baseline, or infer unknown dimensions from pixels. "
        "authors typed component CAD commands, which DesignStudio executes into validated STEP and GLB previews. After completion, show "
        "pcb_3d so the substrate and placed component assets are visible together. "
        "For a PCB draft, grow the provisional substrate to contain every component and defer routing. "
        "Run /layout before routing so edge connectors, "
        "connected groups, overlap clearances and component rotations are optimized. After the electrical "
        "architecture is selected, call build_electronics_from_datasheets with the complete product intent "
        "before PCB optimization. It selects exact parts, queries suppliers, extracts footprints, and replaces "
        "architecture placeholders with datasheet-derived geometry. For a requested individual component, call "
        "extract_component_datasheet with the exact MPN; include source only when the user gives a "
        "direct PDF URL or local path, otherwise let configured vendor APIs resolve it. Missing "
        "vendor or datasheet evidence is incomplete, never invent geometry. Sol/Luna-generated SMT/THT "
        "footprints remain preview-only after schema, numeric-geometry, B-Rep, STEP round-trip, and exact-MPN checks. "
        "Call publish_component_assets with the returned preview manifest only when publishable=true and after explicit user approval. "
        "When the user starts the Industrial Design Flow, treat its eight stages as the canonical order: "
        "intent/evidence, requirements contract, concept/system architecture, mechanical form/CAD, exact electronics, "
        "PCB and harness integration, visual/engineering verification, and drawings/manufacturing release. "
        "Do not silently skip a stage; if a stage is not applicable, record that decision and its evidence. "
        "Start every product request with the plain-language application discovery workflow. "
        "Match it to a reference family when confidence is sufficient, otherwise disclose a "
        "custom concept. Ask no more than three questions, and only ask questions whose answers "
        "materially change size, performance, cost, architecture, or safety. Keep missing values "
        "visible as assumptions. Use a compact decision-first reply: one short state sentence, "
        "then exactly three options using headings `### 1. Title`, `### 2. Title`, and `### 3. Title`. "
        "Each option gets one short best-for line and one short tradeoff line. Keep the main chat "
        "under 400 words; do not print long gate inventories or raw evidence there. Keep raw keys, "
        "component IDs, calculations, assumptions, and evidence references in tool output or Technical "
        "Details. DesignStudio renders the three headings as choice buttons. A preview must be non-mutating; begin mechanical, schematic, PCB, component "
        "binding, and verification work only after explicit approval. Never treat an estimate or "
        "unverified gate as a manufacturing guarantee. Explain concise progress in chat.");
}

void DesignChatPanel::setProjectPath(const QString& path) {
    if (m_projectPath == path) return;
    m_projectPath = path;
    // A Codex thread is scoped to its product directory. Force a clean lazy
    // restart when the active product changes so tools cannot target a stale cwd.
    if (m_codex) m_codex->stop();
}

QJsonObject DesignChatPanel::selectedModelInfo() const
{
    for (const QJsonValue& value : m_modelCatalog) {
        const QJsonObject model = value.toObject();
        if (model.value(QStringLiteral("id")).toString() == m_selectedModel)
            return model;
    }
    return {};
}

QString DesignChatPanel::optionsButtonText() const
{
    const QJsonObject model = selectedModelInfo();
    QString modelName = model.isEmpty() ? m_selectedModel : compactModelName(model);
    if (modelName.isEmpty()) modelName = QStringLiteral("Codex default");
    if (m_selectedEffort.isEmpty() && m_selectedServiceTier.isEmpty()
        && m_selectedModel.isEmpty()) {
        return modelName;
    }
    return QStringLiteral("%1 %2").arg(modelName, prettyEffort(m_selectedEffort));
}

void DesignChatPanel::persistCodexOptions() const
{
    QSettings settings(QStringLiteral("DesignStudio"), QStringLiteral("DesignStudio"));
    settings.setValue(QStringLiteral("codex/model"), m_selectedModel);
    settings.setValue(QStringLiteral("codex/effort"), m_selectedEffort);
    settings.setValue(QStringLiteral("codex/serviceTier"), m_selectedServiceTier);
}

void DesignChatPanel::applyCodexOptions()
{
    if (m_codex)
        m_codex->setOptions(m_selectedModel, m_selectedEffort, m_selectedServiceTier);
    if (m_optionsButton) m_optionsButton->setText(optionsButtonText());
}

void DesignChatPanel::updateModelCatalog(const QJsonArray& models)
{
    m_modelCatalog = models;
    m_defaultModel.clear();
    m_defaultEffort.clear();
    m_defaultServiceTier.clear();

    QJsonObject firstVisible;
    QJsonObject defaultModel;
    for (const QJsonValue& value : m_modelCatalog) {
        const QJsonObject model = value.toObject();
        if (model.value(QStringLiteral("hidden")).toBool(false)) continue;
        if (firstVisible.isEmpty()) firstVisible = model;
        if (model.value(QStringLiteral("isDefault")).toBool(false)) {
            defaultModel = model;
            break;
        }
    }
    if (defaultModel.isEmpty()) defaultModel = firstVisible;
    m_defaultModel = defaultModel.value(QStringLiteral("id")).toString();
    m_defaultEffort = defaultModel.value(QStringLiteral("defaultReasoningEffort")).toString();
    m_defaultServiceTier = defaultModel.value(QStringLiteral("defaultServiceTier")).toString();
    if (m_selectedModel.isEmpty()) m_selectedModel = m_defaultModel;

    QJsonObject selected = selectedModelInfo();
    if (selected.isEmpty()) {
        m_selectedModel = m_defaultModel;
        selected = selectedModelInfo();
    }
    const QJsonArray effortOptions = selected.value(QStringLiteral("supportedReasoningEfforts")).toArray();
    bool effortValid = m_selectedEffort.isEmpty();
    for (const QJsonValue& value : effortOptions) {
        if (value.toObject().value(QStringLiteral("reasoningEffort")).toString()
            == m_selectedEffort) {
            effortValid = true;
            break;
        }
    }
    if (!effortValid || m_selectedEffort.isEmpty())
        m_selectedEffort = selected.value(QStringLiteral("defaultReasoningEffort")).toString();

    bool speedValid = m_selectedServiceTier.isEmpty();
    for (const QJsonValue& value : selected.value(QStringLiteral("serviceTiers")).toArray()) {
        if (value.toObject().value(QStringLiteral("id")).toString()
            == m_selectedServiceTier) {
            speedValid = true;
            break;
        }
    }
    if (!speedValid) {
        m_selectedServiceTier = selected.value(QStringLiteral("defaultServiceTier")).toString();
    }
    persistCodexOptions();
    applyCodexOptions();
    rebuildOptionsMenu();
    m_statusLabel->setText(QStringLiteral("Codex models available"));
}

void DesignChatPanel::rebuildOptionsMenu()
{
    if (!m_optionsMenu) return;
    m_optionsMenu->clear();

    QJsonObject selected = selectedModelInfo();
    auto* modelMenu = m_optionsMenu->addMenu(
        QStringLiteral("Model    %1").arg(selected.isEmpty()
            ? (m_selectedModel.isEmpty() ? QStringLiteral("Loading…")
                                         : compactModelName(QJsonObject{{QStringLiteral("displayName"), m_selectedModel}}))
            : compactModelName(selected)));
    if (m_modelCatalog.isEmpty()) {
        QAction* loading = modelMenu->addAction(QStringLiteral("Load available models…"));
        loading->setEnabled(false);
    } else {
        for (const QJsonValue& value : m_modelCatalog) {
            const QJsonObject model = value.toObject();
            if (model.value(QStringLiteral("hidden")).toBool(false)) continue;
            const QString id = model.value(QStringLiteral("id")).toString();
            QAction* action = modelMenu->addAction(compactModelName(model));
            action->setCheckable(true);
            action->setChecked(id == m_selectedModel);
            action->setToolTip(model.value(QStringLiteral("description")).toString());
            connect(action, &QAction::triggered, this, [this, id] {
                m_selectedModel = id;
                updateModelCatalog(m_modelCatalog);
            });
        }
    }

    auto* effortMenu = m_optionsMenu->addMenu(
        QStringLiteral("Effort    %1").arg(prettyEffort(m_selectedEffort)));
    const QJsonArray efforts = selected.value(QStringLiteral("supportedReasoningEfforts")).toArray();
    if (efforts.isEmpty()) {
        QAction* effort = effortMenu->addAction(QStringLiteral("Default"));
        effort->setEnabled(false);
    } else {
        for (const QJsonValue& value : efforts) {
            const QJsonObject option = value.toObject();
            const QString id = option.value(QStringLiteral("reasoningEffort")).toString();
            QAction* action = effortMenu->addAction(prettyEffort(id));
            action->setCheckable(true);
            action->setChecked(id == m_selectedEffort);
            action->setToolTip(option.value(QStringLiteral("description")).toString());
            connect(action, &QAction::triggered, this, [this, id] {
                m_selectedEffort = id;
                persistCodexOptions();
                applyCodexOptions();
                rebuildOptionsMenu();
            });
        }
    }

    auto* speedMenu = m_optionsMenu->addMenu(
        QStringLiteral("Speed    %1").arg(prettySpeed(m_selectedServiceTier)));
    QAction* standard = speedMenu->addAction(QStringLiteral("Standard"));
    standard->setCheckable(true);
    standard->setChecked(m_selectedServiceTier.isEmpty());
    connect(standard, &QAction::triggered, this, [this] {
        m_selectedServiceTier.clear();
        persistCodexOptions();
        applyCodexOptions();
        rebuildOptionsMenu();
    });
    for (const QJsonValue& value : selected.value(QStringLiteral("serviceTiers")).toArray()) {
        const QJsonObject tier = value.toObject();
        const QString id = tier.value(QStringLiteral("id")).toString();
        QAction* action = speedMenu->addAction(tier.value(QStringLiteral("name")).toString(id));
        action->setCheckable(true);
        action->setChecked(id == m_selectedServiceTier);
        action->setToolTip(tier.value(QStringLiteral("description")).toString());
        connect(action, &QAction::triggered, this, [this, id] {
            m_selectedServiceTier = id;
            persistCodexOptions();
            applyCodexOptions();
            rebuildOptionsMenu();
        });
    }

    m_optionsMenu->addSeparator();
    QAction* reset = m_optionsMenu->addAction(QStringLiteral("Reset to default"));
    reset->setEnabled(m_selectedModel != m_defaultModel
                      || m_selectedEffort != m_defaultEffort
                      || m_selectedServiceTier != m_defaultServiceTier);
    connect(reset, &QAction::triggered, this, [this] {
        m_selectedModel = m_defaultModel;
        m_selectedEffort = m_defaultEffort;
        m_selectedServiceTier = m_defaultServiceTier;
        persistCodexOptions();
        applyCodexOptions();
        rebuildOptionsMenu();
    });
    QAction* refresh = m_optionsMenu->addAction(QStringLiteral("Refresh models"));
    connect(refresh, &QAction::triggered, this, [this] {
        startCodex();
        if (m_codex) m_codex->requestModelList();
        m_statusLabel->setText(QStringLiteral("Refreshing Codex models…"));
    });
}

// Post a finished native (in-process) job result into the chat + activity feed.
void DesignChatPanel::postToolResult(const QString& tool, const QString& text, bool ok) {
    emit agentOutput(tool, text);
    addMessage(ChatMessage::Tool, text, tool);
    m_statusLabel->setText(ok ? (tool + " complete.") : (tool + " failed."));
    emit agentFinished(tool, ok);
}

void DesignChatPanel::runPostRouteCleanup() {
    if (m_projectPath.isEmpty()) return;
    // After native AutoRoute, clear traces/vias that landed inside component
    // courtyard areas (foreign-net violations).  board_fit_agent does this
    // without re-routing, so existing routes are preserved.
    addMessage(ChatMessage::System,
        "Post-route cleanup: removing traces/vias inside component courtyard areas…");
    runAgent(swarmPath("swarm/agents/board_fit_agent.py"),
             {m_projectPath}, "courtyard-cleanup");
}

// ── Message rendering ─────────────────────────────────────────────────────────
QString DesignChatPanel::compactAssistantText(const QString& assistantText) const
{
    const DecisionOptionList decisionOptions = extractDecisionOptions(assistantText);
    QStringList options;
    for (const auto& option : decisionOptions) options.append(option.second);
    if (options.size() < 2) return assistantText;

    QString summary = assistantText.section(QStringLiteral("### 1."), 0, 0).trimmed();
    const QStringList paragraphs = summary.split(
        QRegularExpression(QStringLiteral("\\n\\s*\\n")), Qt::SkipEmptyParts);
    summary = paragraphs.isEmpty() ? QStringLiteral("Codex prepared design directions.")
                                   : paragraphs.first().trimmed();
    if (summary.size() > 420)
        summary = summary.left(417).trimmed() + QStringLiteral("…");

    if (isResolvedDecisionReply(assistantText, decisionOptions)) {
        QString compact = summary + QStringLiteral("\n\nSelections confirmed:");
        for (int i = 0; i < options.size(); ++i)
            compact += QStringLiteral("\n%1. %2").arg(i + 1).arg(options.at(i));
        compact += QStringLiteral(
            "\n\nNo additional concept choice is required. Continue to the approval gate. "
            "Full evidence, assumptions, and release gates remain available in Technical Details.");
        return compact;
    }

    QString compact = summary + QStringLiteral("\n\nChoose a direction:");
    for (int i = 0; i < options.size(); ++i)
        compact += QStringLiteral("\n%1. %2").arg(i + 1).arg(options.at(i));
    compact += QStringLiteral(
        "\n\nSelect an option below. Full evidence, assumptions, and release gates "
        "remain available in Technical Details.");
    return compact;
}

void DesignChatPanel::updateDecisionChoices(const QString& assistantText)
{
    if (!m_choiceFrame || !m_choiceLayout) return;

    while (QLayoutItem* item = m_choiceLayout->takeAt(0)) {
        delete item->widget();
        delete item;
    }

    const DecisionOptionList options = extractDecisionOptions(assistantText);
    if (options.size() < 2 || isResolvedDecisionReply(assistantText, options)) {
        m_choiceFrame->hide();
        return;
    }

    auto* heading = new QLabel(QStringLiteral("Choose a direction"), m_choiceFrame);
    heading->setToolTip(QStringLiteral(
        "Choose a concept to continue. This does not modify CAD or electronics yet."));
    m_choiceLayout->addWidget(heading);
    for (const auto& option : options) {
        auto* button = new QPushButton(
            QStringLiteral("%1  %2").arg(option.first).arg(option.second), m_choiceFrame);
        button->setCursor(Qt::PointingHandCursor);
        button->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Preferred);
        const QString title = option.second;
        const int number = option.first;
        connect(button, &QPushButton::clicked, this, [this, number, title] {
            // These buttons are also used for non-concept decisions (application
            // family, envelope, power/link, role, etc.).  Calling every choice a
            // "concept" made the agent reopen the same question after a click.
            // State explicitly that this is the numbered option from the latest
            // question and ask the agent to advance to the next unresolved one.
            submitPrompt(QStringLiteral(
                "I choose option %1 exactly: %2. Treat this as the numbered option "
                "in your immediately preceding question, not as a new concept. "
                "Record it as selected, do not repeat that question or reopen prior "
                "choices, and continue from the next incomplete decision with only "
                "the minimum questions and a compact approval-gated preview.")
                .arg(number).arg(title));
        });
        m_choiceLayout->addWidget(button);
    }
    m_choiceFrame->show();
    m_choiceFrame->raise();
}

void DesignChatPanel::appendSystem(const QString& msg) {
    ChatMessage m;
    m.role = ChatMessage::System;
    m.text = msg;
    m_history.append(m);
    logMessage(m);
    renderAll();
}

void DesignChatPanel::addMessage(ChatMessage::Role role, const QString& text, const QString& tool) {
    ChatMessage m;
    m.role = role; m.text = text; m.toolName = tool;
    m_history.append(m);
    logMessage(m);
    renderAll();
}

void DesignChatPanel::startAssistantBubble(const QString& tool) {
    ChatMessage m;
    m.role = tool.isEmpty() ? ChatMessage::Assistant : ChatMessage::Tool;
    m.toolName = tool;
    m.text = "";
    m.streaming = true;
    m_history.append(m);
    m_streamingMessageIndex = m_history.size() - 1;
    m_streamingBuf.clear();
    if (m_choiceFrame) m_choiceFrame->hide();
    renderAll();
}

void DesignChatPanel::appendToCurrentBubble(const QString& text) {
    if (m_streamingMessageIndex < 0
        || m_streamingMessageIndex >= m_history.size()) return;
    m_streamingBuf += text;
    m_history[m_streamingMessageIndex].text = m_streamingBuf;
    // Forward each line to the AgentActivityPanel live log
    for (const QString& line : text.split('\n'))
        if (!line.trimmed().isEmpty())
            emit agentOutput(m_activeAgent, line.trimmed());
    // Re-render fully every ~40 chars for simplicity (no flicker at human reading speed)
    if (m_streamingBuf.length() % 40 == 0)
        renderAll();
}

void DesignChatPanel::finalizeCurrentBubble() {
    if (m_streamingMessageIndex >= 0
        && m_streamingMessageIndex < m_history.size()) {
        m_history[m_streamingMessageIndex].streaming = false;
        logMessage(m_history[m_streamingMessageIndex]);   // log the completed streamed agent reply
        const ChatMessage& completed = m_history[m_streamingMessageIndex];
        const bool codexReply = completed.role == ChatMessage::Assistant
            || (completed.role == ChatMessage::Tool
                && (completed.toolName.compare(QStringLiteral("codex"), Qt::CaseInsensitive) == 0
                    || (completed.text.contains(QStringLiteral("### 1."))
                        && completed.text.contains(QStringLiteral("### 2.")))));
        if (codexReply) {
            updateDecisionChoices(completed.text);
            if (m_technicalDetails && completed.text.size() > 1000) {
                m_technicalDetails->append(QStringLiteral(
                    "<b>Full Codex response (compact view shown in chat)</b><br>%1")
                    .arg(completed.text.toHtmlEscaped().replace("\n", "<br>")));
            }
        }
    }
    m_streamingMessageIndex = -1;
    renderAll();
}

QString DesignChatPanel::bubbleHtml(const ChatMessage& msg) const {
    QString displayText = msg.text;
    const bool codexReply = msg.role == ChatMessage::Assistant
        || (msg.role == ChatMessage::Tool
            && (msg.toolName.compare(QStringLiteral("codex"), Qt::CaseInsensitive) == 0
                || (msg.text.contains(QStringLiteral("### 1."))
                    && msg.text.contains(QStringLiteral("### 2.")))));
    const QString compactText = codexReply && !msg.streaming
        ? compactAssistantText(msg.text) : msg.text;
    const bool compacted = compactText != msg.text;
    if (compacted) displayText = compactText;
    QString escaped = displayText.toHtmlEscaped()
        .replace("\n", "<br>")
        .replace("  ", "&nbsp;&nbsp;");
    if (compacted)
        escaped += QStringLiteral(
            "<br><small style='color:#8b949e'>Full audit response is in Technical Details.</small>");

    switch (msg.role) {
    case ChatMessage::User:
        return QString("<div class='bubble-user'><span class='user'>You</span><br>%1</div>").arg(escaped);
    case ChatMessage::Assistant:
        return QString("<div class='bubble-asst'><span class='assistant'>Assistant%2</span><br>%1</div>")
            .arg(escaped)
            .arg(msg.streaming ? " <small style='color:#8b949e'>▋</small>" : "");
    case ChatMessage::Tool:
        return QString("<div class='bubble-tool'><span style='color:#3fb950'>⚙ %2%3</span><br>%1</div>")
            .arg(escaped)
            .arg(msg.toolName)
            .arg(msg.streaming ? " <span style='color:#8b949e'>…</span>" : "");
    case ChatMessage::System:
        return QString("<div class='bubble-sys'>%1</div>").arg(escaped);
    }
    return {};
}

// ── Persistent chat log (daily JSONL) ─────────────────────────────────────────
// One JSON object per line in ~/.local/share/DesignStudio/chat-YYYYMMDD.jsonl
// so conversations (and agent/DRC output) survive across sessions and can be
// grepped or replayed later.
// Per-user persistent chat log: one continuous file per user id (default
// "user1", overridable via DESIGNSTUDIO_USER) so each user resumes their own
// conversation across sessions.
QString DesignChatPanel::chatLogPath() const {
    const QString dir = QDir::homePath() + "/.local/share/DesignStudio";
    QDir().mkpath(dir);
    QString user = qEnvironmentVariable("DESIGNSTUDIO_USER", "user1");
    user.replace(QRegularExpression("[^A-Za-z0-9_-]"), "_");
    return dir + "/chat-" + user + ".jsonl";
}

void DesignChatPanel::loadHistory() {
    const QString path = chatLogPath();
    // One-time migration: seed the per-user log from any pre-existing daily logs
    // (chat-YYYYMMDD.jsonl) so existing conversations carry over.
    if (!QFile::exists(path)) {
        QDir dir(QDir::homePath() + "/.local/share/DesignStudio");
        QStringList daily = dir.entryList({"chat-20??????.jsonl"}, QDir::Files, QDir::Name);
        if (!daily.isEmpty()) {
            QFile out(path);
            if (out.open(QIODevice::WriteOnly | QIODevice::Text)) {
                for (const QString& d : daily) {
                    QFile in(dir.filePath(d));
                    if (in.open(QIODevice::ReadOnly)) { out.write(in.readAll()); in.close(); }
                }
                out.close();
            }
        }
    }
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly | QIODevice::Text)) return;
    QVector<ChatMessage> all;
    while (!f.atEnd()) {
        const QByteArray line = f.readLine().trimmed();
        if (line.isEmpty()) continue;
        QJsonObject o = QJsonDocument::fromJson(line).object();
        if (o.isEmpty()) continue;
        const QString r = o["role"].toString();
        ChatMessage m;
        m.role = r == "user"      ? ChatMessage::User
               : r == "assistant" ? ChatMessage::Assistant
               : r == "tool"      ? ChatMessage::Tool
                                  : ChatMessage::System;
        m.text     = o["text"].toString();
        m.toolName = o["tool"].toString();
        if (!m.text.trimmed().isEmpty()) all.append(m);
    }
    f.close();
    // Keep the most recent slice so the view stays responsive on long histories.
    const int KEEP = 80;
    const int start = all.size() > KEEP ? all.size() - KEEP : 0;
    for (int i = start; i < all.size(); ++i) m_history.append(all[i]);
    if (!m_history.isEmpty()) {
        for (int i = m_history.size() - 1; i >= 0; --i) {
            const ChatMessage& candidate = m_history.at(i);
            const bool codexReply = candidate.role == ChatMessage::Assistant
                || (candidate.role == ChatMessage::Tool
                    && (candidate.toolName.compare(QStringLiteral("codex"), Qt::CaseInsensitive) == 0
                        || (candidate.text.contains(QStringLiteral("### 1."))
                            && candidate.text.contains(QStringLiteral("### 2.")))));
            if (!codexReply) continue;
            updateDecisionChoices(candidate.text);
            if (m_technicalDetails && candidate.text.size() > 1000) {
                m_technicalDetails->append(QStringLiteral(
                    "<b>Full Codex response (compact view shown in chat)</b><br>%1")
                    .arg(candidate.text.toHtmlEscaped().replace("\n", "<br>")));
            }
            break;
        }
        renderAll();
    }

    // Recover the last real build intent so "/build" (or /datasheets) resumes it
    // across restarts. Scan the FULL history, newest first, skipping bare/bogus ones.
    for (int i = all.size() - 1; i >= 0 && m_lastBuildIntent.isEmpty(); --i) {
        if (all[i].role != ChatMessage::User) continue;
        QString t = all[i].text.trimmed();
        if (!t.startsWith("/build", Qt::CaseInsensitive)) continue;
        QString p = t.mid(QString("/build").length()).trimmed();
        while (p.startsWith("/build", Qt::CaseInsensitive))
            p = p.mid(QString("/build").length()).trimmed();
        if (p.length() >= 8) m_lastBuildIntent = p;   // a real prompt, not "/build"
    }
}

void DesignChatPanel::logMessage(const ChatMessage& msg) {
    if (msg.text.trimmed().isEmpty()) return;

    QFile f(chatLogPath());
    if (!f.open(QIODevice::Append | QIODevice::Text)) return;

    const char* role =
        msg.role == ChatMessage::User      ? "user"   :
        msg.role == ChatMessage::Assistant ? "assistant" :
        msg.role == ChatMessage::Tool      ? "tool"   : "system";

    QJsonObject o{
        {"ts",      QDateTime::currentDateTime().toString(Qt::ISODate)},
        {"role",    role},
        {"tool",    msg.toolName},
        {"project", m_projectPath},
        {"text",    msg.text},
    };
    f.write(QJsonDocument(o).toJson(QJsonDocument::Compact));
    f.write("\n");
    f.close();
}

void DesignChatPanel::renderAll() {
    QString html = "<html><body>";
    for (auto& m : m_history)
        html += bubbleHtml(m);
    html += "</body></html>";
    m_chatView->setHtml(html);
    m_chatView->verticalScrollBar()->setValue(m_chatView->verticalScrollBar()->maximum());
}

// ── Input handling ────────────────────────────────────────────────────────────
void DesignChatPanel::submitPrompt(const QString& text) {
    if (text.trimmed().isEmpty() || m_agentRunning) return;
    if (m_choiceFrame) m_choiceFrame->hide();
    m_input->setText(text);
    onSend();
}

void DesignChatPanel::onSend() {
    if (m_agentRunning) {
        stopAgent();
        return;
    }

    QString text = m_input->text().trimmed();
    if (text.isEmpty()) return;

    const QJsonArray referenceItems = referenceInputItems();
    const QString referencePdf = m_attachedReferencePdf;
    const QString referenceManifest = m_attachedReferenceManifest;
    QString codexText = text;
    QString displayText = text;
    QString validationError;
    if (!validateAttachedReference(&validationError)) {
        appendSystem(QStringLiteral("Attached reference rejected: %1. Reattach the source files.").arg(validationError));
        return;
    }
    if (!referencePdf.isEmpty()) {
        displayText += QStringLiteral("\n\n[Attached visual reference: %1]")
                           .arg(QFileInfo(referencePdf).fileName());
        codexText += QStringLiteral(
            "\n\nA visual reference PDF is attached by the DesignStudio host. "
            "The original PDF is preserved at %1 and its audit manifest is at %2. "
            "The following localImage inputs are rendered pages from that PDF. "
            "Use them only as inspiration/evidence: do not infer manufacturing dimensions, "
            "GD&T, electrical ratings, or component geometry from pixels. "
            "State assumptions and unknowns, then propose approval-gated work.")
                         .arg(referencePdf, referenceManifest);
    } else if (!referenceManifest.isEmpty()) {
        displayText += QStringLiteral("\n\n[Attached visual references: %1]")
                           .arg(m_attachedReferenceLabel);
        codexText += QStringLiteral(
            "\n\nA design-studio.reference-image-set/1 manifest is attached at %1. "
            "Every localImage is a non-authoritative crop linked to an immutable source hash. "
            "Use design-studio.visual-evidence/2 for silhouettes, seams, interfaces, texture, "
            "occlusion and cross-view links. Browser controls are not product geometry. "
            "Scale, hidden construction and every unevidenced feature must remain explicit unknowns; "
            "never infer millimetres from these uncalibrated pixels."
        ).arg(referenceManifest);
    }
    m_input->clear();

    addMessage(ChatMessage::User, displayText);

    // The attachment is scoped to one turn so a later design change cannot
    // accidentally reuse stale visual evidence.
    m_attachedReferencePdf.clear();
    m_attachedReferencePages.clear();
    m_attachedReferenceManifest.clear();
    m_attachedReferenceLabel.clear();
    if (m_attachButton) {
        m_attachButton->setChecked(false);
        m_attachButton->setText(QStringLiteral("+"));
        m_attachButton->setToolTip(QStringLiteral(
            "Attach reference images or a PDF to the next Codex turn"));
    }

#ifndef DESIGNSTUDIO_DEVELOPER_TOOLS
    startCodex();
    if (!m_codex || m_codex->state() == CodexAppServerClient::State::Failed) {
        appendSystem("Design Assistant is unavailable. Run `codex login`, then retry.");
        return;
    }
    m_agentRunning = true;
    m_activeAgent = QStringLiteral("codex");
    updateRunControl(true);
    m_progress->show();
    m_statusLabel->setText("Understanding the product request…");
    startAssistantBubble(QStringLiteral("codex"));
    emit agentStarted(QStringLiteral("codex"));
    m_codex->submit(codexText, referenceItems);
    return;
#else
    if (usingCodex()) {
        startCodex();
        if (!m_codex || m_codex->state() == CodexAppServerClient::State::Failed) {
            appendSystem("Codex is unavailable; check the developer agent service.");
            return;
        }
        m_agentRunning = true;
        m_activeAgent = QStringLiteral("codex");
        updateRunControl(true);
        m_progress->show();
        m_statusLabel->setText("Codex is understanding the product request…");
        startAssistantBubble(QStringLiteral("codex"));
        emit agentStarted(QStringLiteral("codex"));
        m_codex->submit(codexText, referenceItems);
        return;
    }

    // Route the message
    QString route = routeInput(text);
    if (route == "advise") {
        onQuickAction("advise");
    } else if (route == "sipi") {
        onQuickAction("sipi");
    } else if (route == "extract") {
        onQuickAction("extract");
    } else if (route == "drc") {
        onQuickAction("drc");
    } else if (route == "drcfix") {
        onQuickAction("drcfix");
    } else if (route == "reroute") {
        onQuickAction("reroute");
    } else if (route == "fitboard") {
        onQuickAction("fitboard");
    } else if (route == "viaedge") {
        onQuickAction("viaedge");
    } else if (route == "route" || route == "layout" || route == "netplace"
               || route == "subsystem" || route == "bom") {
        onQuickAction(route);
    } else if (route == "accept" || route == "reject"
               || route == "manufactured" || route == "exported") {
        recordOutcome(route);
    } else if (route == "fabexport") {
        onQuickAction("fabexport");
    } else if (route == "forget") {
        addMessage(ChatMessage::User, "/forget — clear the design memory (start fresh)");
        runAgent(swarmPath("swarm/memory/harness_cli.py"), {"--forget"}, "memory-reset");
    } else if (route == "datasheets") {
        provideDatasheets();
    } else if (route == "refresh") {
        if (m_projectPath.isEmpty()) {
            addMessage(ChatMessage::System,
                "Open a project (or /build one) first — /refresh regenerates an "
                "existing board's footprints.");
        } else {
            addMessage(ChatMessage::User,
                "/refresh — regenerate footprints from the saved design (no rebuild) "
                "and re-route");
            runAgent(swarmPath("swarm/agents/reapply_agent.py"),
                     {m_projectPath}, "reapply");
        }
    } else if (route == "build") {
        m_pendingQuery = text;   // the whole prompt is the design intent
        onQuickAction("build");
    } else if (route == "digikey") {
        m_pendingQuery = text;   // carry the MPN/description into the job
        onQuickAction("digikey");
    } else {
        // General Q&A: pass the text as context to the advisor
        QString statePath = exportCircuitState();
        if (statePath.isEmpty()) {
            addMessage(ChatMessage::System, "Open a project first (File → Open) to ask design questions.");
            return;
        }
        QString script = swarmPath("swarm/agents/circuit_advisor_agent.py");
        runAgent(script, {statePath}, "circuit-advisor");
    }
#endif
}

void DesignChatPanel::onQuickAction(const QString& action) {
    if (m_agentRunning) {
        addMessage(ChatMessage::System, "An agent is already running. Stop it first (■).");
        return;
    }

    if (action == "design") {
        if (m_projectPath.isEmpty()) {
            addMessage(ChatMessage::System,
                "Open a project first (File → Open .dsproj), then click Continue Design.");
            return;
        }
        addMessage(ChatMessage::User,
            "/design — assign nets in the external project file (explicit reload required)");
        // Legacy agent: writes the .dsproj on disk. MainWindow deliberately does
        // not auto-accept that unvalidated rewrite into its in-memory model.
        runAgent(swarmPath("swarm/agents/live_design_agent.py"),
                 {m_projectPath}, "live-design");

    } else if (action == "advise") {
        QString statePath = exportCircuitState();
        if (statePath.isEmpty()) return;
        addMessage(ChatMessage::User, "/advise — full circuit analysis");
        runAgent(swarmPath("swarm/agents/circuit_advisor_agent.py"),
                 {statePath}, "circuit-advisor");

    } else if (action == "sipi") {
        QString statePath = exportCircuitState();
        if (statePath.isEmpty()) return;
        addMessage(ChatMessage::User, "/sipi — deterministic SI/PI check");
        runAgent(swarmPath("swarm/agents/sipi_advisor.py"),
                 {statePath}, "sipi-advisor");

    } else if (action == "extract") {
        QString pdf = QFileDialog::getOpenFileName(this,
            "Select Datasheet PDF", QString(), "PDF Files (*.pdf);;All Files (*)");
        if (pdf.isEmpty()) return;
        addMessage(ChatMessage::User, "/extract " + QFileInfo(pdf).fileName());
        runAgent(swarmPath("swarm/agents/datasheet_extractor_agent.py"),
                 {pdf}, "datasheet-extractor");

    } else if (action == "drc") {
        if (m_projectPath.isEmpty()) {
            addMessage(ChatMessage::System, "Open a project first."); return;
        }
        addMessage(ChatMessage::User,
            "/drc — run native DRC engine and report violations by root cause");
        emit nativeJobRequested("save");
        runAgent(swarmPath("swarm/agents/drc_agent.py"),
                 {m_projectPath}, "drc-analysis");

    } else if (action == "drcfix") {
        if (m_projectPath.isEmpty()) {
            addMessage(ChatMessage::System, "Open a project first."); return;
        }
        addMessage(ChatMessage::User,
            "/drcfix — deduplicate routes, clear stale traces, add per-footprint clearances");
        emit nativeJobRequested("save");
        runAgent(swarmPath("swarm/agents/drc_fix_agent.py"),
                 {m_projectPath}, "drc-fix");

    } else if (action == "reroute") {
        if (m_projectPath.isEmpty()) {
            addMessage(ChatMessage::System, "Open a project first."); return;
        }
        addMessage(ChatMessage::User,
            "/reroute — clear all routes and re-route every net with 0.1 mm grid");
        emit nativeJobRequested("save");
        runAgent(swarmPath("swarm/agents/bga_reroute_agent.py"),
                 {m_projectPath}, "bga-reroute");

    } else if (action == "fitboard") {
        if (m_projectPath.isEmpty()) {
            addMessage(ChatMessage::System, "Open a project first."); return;
        }
        addMessage(ChatMessage::User,
            "/fitboard — remove routes under component bodies, shrink board to fit components");
        emit nativeJobRequested("save");
        runAgent(swarmPath("swarm/agents/board_fit_agent.py"),
                 {m_projectPath}, "board-fit");

    } else if (action == "viaedge") {
        if (m_projectPath.isEmpty()) {
            addMessage(ChatMessage::System, "Open a project first."); return;
        }
        addMessage(ChatMessage::User,
            "/viaedge — move all vias to board edge channels, merge collinear trace segments");
        emit nativeJobRequested("save");
        runAgent(swarmPath("swarm/agents/via_edge_agent.py"),
                 {m_projectPath}, "via-edge");

    } else if (action == "route") {
        if (!m_model || m_model->footprints.isEmpty()) {
            addMessage(ChatMessage::System, "Open a project first."); return;
        }
        addMessage(ChatMessage::User, "/route — auto-route the board");
        m_statusLabel->setText("Auto-routing…");
        emit agentStarted("router");
        emit nativeJobRequested("route");   // MainWindow runs it on the live model

    } else if (action == "production") {
        if (m_projectPath.isEmpty()) {
            addMessage(ChatMessage::System, "Open a project first."); return;
        }
        addMessage(ChatMessage::User,
            "/production — GND/power copper pours on all layers, widen power trace widths, "
            "relocate vias under component bodies, run DRC");
        emit nativeJobRequested("save");
        runAgent(swarmPath("swarm/agents/production_fix_agent.py"),
                 {m_projectPath}, "production-fix");

    } else if (action == "layout") {
        if (!m_model || m_model->footprints.isEmpty()) {
            addMessage(ChatMessage::System, "Open a project first."); return;
        }
        addMessage(ChatMessage::User,
            "/layout — floor-plan board, ring-place decoupling caps, clear old routes");
        // Flush model first so the agent reads latest pad/net assignments.
        emit nativeJobRequested("save");
        runAgent(swarmPath("swarm/agents/layout_taste_agent.py"),
                 {m_projectPath}, "layout-taste");

    } else if (action == "netplace") {
        if (!m_model || m_model->footprints.isEmpty()) {
            addMessage(ChatMessage::System, "Open a project first."); return;
        }
        addMessage(ChatMessage::User,
            "/netplace — detect sub-systems from netlist topology, place ICs and passives at pin locations");
        emit nativeJobRequested("save");
        runAgent(swarmPath("swarm/agents/netlist_placer_agent.py"),
                 {m_projectPath}, "netlist-placer");

    } else if (action == "subsystem") {
        if (m_projectPath.isEmpty()) {
            addMessage(ChatMessage::System, "Open a project first."); return;
        }
        addMessage(ChatMessage::User,
            "/subsystem — weight nets by importance, cluster into sub-systems, "
            "floor-plan each as an island ordered by inter-cluster coupling");
        emit nativeJobRequested("save");
        runAgent(swarmPath("swarm/agents/subsystem_placer_agent.py"),
                 {m_projectPath}, "subsystem-placer");

    } else if (action == "bom") {
        if (m_projectPath.isEmpty()) {
            addMessage(ChatMessage::System,
                "Open a project first (File → Open .dsproj), then click Source BOM.");
            return;
        }
        addMessage(ChatMessage::User,
            "/bom — identify missing passives & connectors, source from DigiKey, add to design");
        // Flush model to disk so the agent reads the latest state
        emit nativeJobRequested("save");
        runAgent(swarmPath("swarm/agents/bom_completion_agent.py"),
                 {m_projectPath}, "bom-completion");

    } else if (action == "fabexport") {
        if (m_projectPath.isEmpty()) {
            addMessage(ChatMessage::System, "Open a project first."); return;
        }
        addMessage(ChatMessage::User,
            "/export — generate Gerbers, Excellon drill, priced BOM (₹), pick-and-place");
        emit nativeJobRequested("save");   // flush latest model to disk
        runAgent(swarmPath("swarm/agents/fab_export_agent.py"),
                 {m_projectPath}, "fab-export");

    } else if (action == "build") {
        // Design-from-prompt via the memory harness. Deliberately does NOT require
        // a project to be open — this is how a user starts a NEW design.
        QString intent = m_pendingQuery.trimmed();
        m_pendingQuery.clear();
        // Strip a leading "/build" command word: "/build foo" → "foo", "/build" → "".
        while (intent.startsWith("/build", Qt::CaseInsensitive))
            intent = intent.mid(QString("/build").length()).trimmed();
        // No prompt → resume the last design intent (e.g. after adding datasheets,
        // or "/build" alone). Only ask if we have no prior intent to resume.
        bool resuming = false;
        if (intent.isEmpty() && !m_lastBuildIntent.isEmpty()) {
            intent = m_lastBuildIntent;
            resuming = true;
        }
        if (intent.isEmpty())
            intent = QInputDialog::getText(this, "Build a design",
                "Describe the board to build (e.g. \"BLDC robotic arm controller, 24V, FOC\"):");
        if (intent.trimmed().isEmpty()) return;
        m_lastBuildIntent = intent;          // so /datasheets and "/build" can resume this
        addMessage(ChatMessage::User, "/build " + intent);
        if (resuming)
            addMessage(ChatMessage::System,
                "Resuming your last design (using any newly-added datasheets):\n" + intent);
        // The design needs a destination file. If nothing is open, create a new
        // project so the board lands and the canvas shows it (handler is synchronous,
        // so m_projectPath is set before we read it back below).
        if (m_projectPath.isEmpty()) {
            addMessage(ChatMessage::System,
                "No project open — creating a new one for this design.");
            emit nativeJobRequested("newproject");
        }
        QStringList args{intent};
        if (!m_projectPath.isEmpty())
            args << "--project" << m_projectPath;
        runAgent(swarmPath("swarm/memory/harness_cli.py"), args, "design-harness");

    } else if (action == "digikey") {
        // The query (MPN / part text) is whatever the user typed after the verb.
        QString q = m_pendingQuery.trimmed();
        if (q.isEmpty())
            q = QInputDialog::getText(this, "DigiKey lookup",
                                      "Manufacturer part number or description:");
        m_pendingQuery.clear();
        if (q.trimmed().isEmpty()) return;
        addMessage(ChatMessage::User, "/digikey " + q);
        runAgent(swarmPath("swarm/agents/digikey_agent.py"), {q}, "digikey");
    }
}

QString DesignChatPanel::routeInput(const QString& text) {
    QString t = text.toLower();
    // Design-outcome feedback (closes the self-improvement loop). Checked first
    // so "/accept" doesn't get swallowed by other rules.
    if (t.startsWith("/accept") || t.contains("accept design") || t.contains("approve design")
        || t.contains("looks good") || t.contains("design is good"))
        return "accept";
    if (t.startsWith("/reject") || t.contains("reject design") || t.contains("discard design")
        || t.contains("design is bad") || t.contains("scrap this design"))
        return "reject";
    if (t.startsWith("/manufactured") || t.contains("manufactured") || t.contains("ordered the board")
        || t.contains("sent to fab") || t.contains("built the board"))
        return "manufactured";
    if (t.startsWith("/forget") || t.contains("clear memory") || t.contains("reset memory")
        || t.contains("forget designs") || t.contains("clear design memory"))
        return "forget";
    if (t.startsWith("/datasheets") || t.startsWith("/datasheet")
        || t.contains("provide datasheet") || t.contains("select datasheet")
        || t.contains("browse datasheet") || t.contains("add datasheet")
        || t.contains("i have the datasheet") || t.contains("supply datasheet"))
        return "datasheets";
    if (t.startsWith("/refresh") || t.startsWith("/reapply")
        || t.contains("regenerate footprint") || t.contains("refresh footprint")
        || t.contains("update footprint") || t.contains("apply the fixes")
        || t.contains("apply fixes") || t.contains("reapply"))
        return "refresh";
    if (t.startsWith("/exported") || t.contains("exported the design"))
        return "exported";   // manual outcome feedback
    if (t.startsWith("/export") || t.contains("export gerber") || t.contains("fab export")
        || t.contains("generate gerber") || t.contains("fabrication") || t.contains("manufacturing files"))
        return "fabexport";  // generate fab output (then auto-records 'exported')
    // Build-from-prompt: a new product design driven by the memory harness.
    // Checked FIRST so "build me a robotic arm controller" reaches the design
    // pipeline instead of falling through to Q&A. (Distinct from "/design",
    // which assigns nets on an already-open board.)
    if (t.startsWith("/build") || t.contains("build a ") || t.contains("build me")
        || t.contains("i want to build") || t.contains("i want a ")
        || t.contains("design and build") || t.contains("design a ")
        || t.contains("create a ") || t.contains("make me a")
        || t.contains("pcb for") || t.contains("controller for")
        || t.contains("board for") || t.contains("design pcb"))
        return "build";
    if (t.startsWith("/design") || t.startsWith("/continue") ||
        t.contains("continue design") || t.contains("assign nets") ||
        t.contains("wire up") || t.contains("netlist"))
        return "design";
    if (t.startsWith("/advise") || t.contains("advise") || t.contains("analyze") || t.contains("review"))
        return "advise";
    if (t.startsWith("/sipi") || t.contains("si/pi") || t.contains("signal integrity")
        || t.contains("impedance") || t.contains("eye") || t.contains("ddr"))
        return "sipi";
    if (t.startsWith("/extract") || t.contains("extract") || t.contains("pdf") || t.contains("datasheet"))
        return "extract";
    if (t.startsWith("/reroute") || t.contains("re-route") || t.contains("reroute all")
        || t.contains("clean route") || t.contains("re route"))
        return "reroute";
    if (t.startsWith("/production") || t.contains("production standard") || t.contains("production grade")
        || t.contains("reach production") || t.contains("copper pour") || t.contains("gnd plane")
        || t.contains("ground plane") || t.contains("power plane") || t.contains("power trace width")
        || t.contains("production ready"))
        return "production";
    if (t.startsWith("/fitboard") || t.startsWith("/fit") ||
        t.contains("fit board") || t.contains("resize board") || t.contains("shrink board")
        || t.contains("compact board") || t.contains("board size") || t.contains("under component")
        || t.contains("under pins") || t.contains("courtyard") || t.contains("board margin"))
        return "fitboard";
    if (t.startsWith("/viaedge") || t.startsWith("/edge via") ||
        t.contains("edge via") || t.contains("via edge") || t.contains("via at side")
        || t.contains("perimeter via") || t.contains("merge trace") || t.contains("collinear"))
        return "viaedge";
    if (t.startsWith("/drcfix") || t.contains("fix drc") || t.contains("drc fix")
        || t.contains("deduplicate") || t.contains("stale route") || t.contains("clear violations"))
        return "drcfix";
    if (t.startsWith("/drc") || t.contains("drc") || t.contains("clearance") || t.contains("violation"))
        return "drc";
    if (t.startsWith("/route") || t.contains("auto route") || t.contains("autoroute")
        || t.contains("route the") || t.contains("route all"))
        return "route";
    if (t.startsWith("/subsystem") || t.contains("subsystem placement") || t.contains("cluster placement")
        || t.contains("functional block") || t.contains("floor plan") || t.contains("floorplan")
        || t.contains("group by net") || t.contains("importance"))
        return "subsystem";
    if (t.startsWith("/netplace") || t.contains("place by net") || t.contains("topology place")
        || t.contains("subsystem") || t.contains("sub-system") || t.contains("pin-level"))
        return "netplace";
    if (t.startsWith("/layout") || t.contains("optimi") || t.contains("placement")
        || t.contains("auto place") || t.contains("reposition") || t.contains("rearrange"))
        return "layout";
    if (t.startsWith("/bom") || t.contains("source bom") || t.contains("bom complete")
        || t.contains("missing component") || t.contains("add capacitor") || t.contains("add passiv")
        || t.contains("add connector") || t.contains("decoupling cap") || t.contains("bypass cap"))
        return "bom";
    if (t.startsWith("/digikey") || t.contains("digikey") || t.contains("digi-key")
        || t.contains("fetch datasheet") || t.contains("find part") || t.contains("look up part"))
        return "digikey";
    return "general";
}

// ── Close the learning loop: feed a real outcome back to the design memory ────
// Reads the harness session id from the <project>.harness-result.json sidecar and
// calls harness_cli --outcome, so the memory learns which designs actually shipped.
void DesignChatPanel::recordOutcome(const QString& outcome) {
    if (m_projectPath.isEmpty()) {
        addMessage(ChatMessage::System, "Open a project first."); return;
    }
    QFileInfo fi(m_projectPath);
    const QString sidecar = fi.absolutePath() + "/" + fi.completeBaseName()
                            + ".harness-result.json";
    QFile f(sidecar);
    if (!f.open(QIODevice::ReadOnly)) {
        addMessage(ChatMessage::System,
            "No AI-designed history for this project — nothing to rate. "
            "(Outcome feedback applies to boards created with /build.)");
        return;
    }
    QJsonObject o = QJsonDocument::fromJson(f.readAll()).object();
    const QString sid = o.value("session_id").toString();
    f.close();
    if (sid.isEmpty()) {
        addMessage(ChatMessage::System, "No session id recorded for this design.");
        return;
    }
    addMessage(ChatMessage::User,
        "/" + outcome + " — recording outcome so the design memory learns from it");
    runAgent(swarmPath("swarm/memory/harness_cli.py"),
             {"--outcome", sid, outcome}, "design-outcome");
}

// ── Native DRC → chat-ready summary ───────────────────────────────────────────
QString DesignChatPanel::formatDrcReport() {
    if (!m_core || !m_core->isLoaded())
        return "Engine not loaded — native DRC unavailable.";
    if (!m_model || m_model->footprints.isEmpty())
        return "Open a project first (File → Open).";

    const QVector<DrcViolation> v = m_core->runDrc(m_model);
    if (v.isEmpty())
        return "✓ DRC clean — 0 violations.";

    QMap<QString,int> counts;
    int errors = 0, warnings = 0;
    for (const auto& d : v) {
        counts[d.rule]++;
        (d.severity == DrcViolation::Error ? errors : warnings)++;
    }

    QString s = QString("%1 violations — %2 errors, %3 warnings\n\nBy rule:\n")
                    .arg(v.size()).arg(errors).arg(warnings);
    for (auto it = counts.constBegin(); it != counts.constEnd(); ++it)
        s += QString("  • %1: %2\n").arg(it.key()).arg(it.value());

    s += "\nDetails:\n";
    int n = 0;
    for (const auto& d : v) {
        if (n++ >= 15) { s += QString("  … and %1 more\n").arg(v.size() - 15); break; }
        s += QString("  %1 [%2] %3  @(%4, %5)\n")
                 .arg(d.severity == DrcViolation::Error ? "✗" : "⚠")
                 .arg(d.rule).arg(d.description)
                 .arg(d.x_mm, 0, 'f', 2).arg(d.y_mm, 0, 'f', 2);
    }
    return s;
}

QString DesignChatPanel::exportCircuitState() {
    if (!m_model || m_model->footprints.isEmpty()) {
        addMessage(ChatMessage::System, "No project open — open a .dsproj file first.");
        return {};
    }

    // Build a minimal circuit-state JSON from the current model
    QJsonObject root;
    root["schema"]      = "design-studio.circuit-state/1";
    root["design_goal"] = "PCB design review";

    QJsonObject board;
    board["width_mm"]      = m_model->boardWidthMm;
    board["height_mm"]     = m_model->boardHeightMm;
    board["copper_layers"] = m_model->copperLayers;
    board["dielectric_er"] = m_model->erDielectric;
    root["board"]          = board;

    QJsonArray comps;
    for (auto& fp : m_model->footprints) {
        QJsonObject c;
        c["ref"] = fp.ref;
        c["lib"] = fp.lib;
        c["x_mm"]= fp.x_mm;
        c["y_mm"]= fp.y_mm;
        QJsonArray pins;
        for (auto& pad : fp.pads) {
            QJsonObject p;
            p["name"] = pad.name;
            p["net"]  = m_model->netName(pad.netId);
            pins.append(p);
        }
        c["pins"] = pins;
        comps.append(c);
    }
    root["components"] = comps;

    QJsonArray nets;
    for (auto& n : m_model->nets) {
        QJsonObject no;
        no["id"] = n.id; no["name"] = n.name;
        nets.append(no);
    }
    root["nets"] = nets;

    // Unconnected pins (pads with netId == -1)
    QJsonArray unconn;
    for (auto& fp : m_model->footprints)
        for (auto& pad : fp.pads)
            if (pad.netId < 0) {
                QJsonObject u;
                u["ref"] = fp.ref; u["pin"] = pad.name; u["net"] = "";
                unconn.append(u);
            }
    root["unconnected_pins"] = unconn;
    root["signal_integrity"]  = QJsonArray();
    root["power_integrity"]   = QJsonArray();
    root["ddr_lanes"]         = QJsonArray();

    // Real DRC results from the native engine, so the advisor reasons over the
    // actual violations (correct rule labels, microvia-aware) rather than guessing.
    QJsonArray drcArr;
    if (m_core && m_core->isLoaded()) {
        for (const auto& d : m_core->runDrc(m_model)) {
            QJsonObject o;
            o["rule"]     = d.rule;
            o["severity"] = d.severity == DrcViolation::Error ? "error" : "warning";
            o["message"]  = d.description;
            o["x_mm"]     = d.x_mm;
            o["y_mm"]     = d.y_mm;
            drcArr.append(o);
        }
    }
    root["drc"]               = drcArr;
    root["erc"]               = QJsonArray();

    QString path = QDir::tempPath() + "/designstudio-circuit-state.json";
    QFile f(path);
    if (f.open(QIODevice::WriteOnly)) {
        f.write(QJsonDocument(root).toJson());
        return path;
    }
    addMessage(ChatMessage::System, "Failed to export circuit state to " + path);
    return {};
}

// After a build, read the result sidecar and report any parts that couldn't be
// sourced (no datasheet on either vendor, no substitute, no package) — and tell
// the user they can supply the datasheets to resume.
void DesignChatPanel::checkUnrealizedParts() {
    if (m_projectPath.isEmpty()) return;
    QString sidecar = m_projectPath;
    sidecar.replace(QRegularExpression("\\.dsproj$"), ".harness-result.json");
    QFile f(sidecar);
    if (!f.open(QIODevice::ReadOnly)) return;
    QJsonArray un = QJsonDocument::fromJson(f.readAll()).object()["unrealized"].toArray();
    if (un.isEmpty()) return;
    QStringList mpns;
    for (const auto& v : un) mpns << v.toObject()["mpn"].toString();
    addMessage(ChatMessage::System,
        QString("⚠ %1 part(s) could not be sourced (no datasheet on DigiKey or "
                "Mouser, no substitute):\n  • %2\n\nDownload their datasheets, then "
                "type /datasheets to browse and select the PDFs — I'll add them to "
                "the datasheets folder and resume the build.")
            .arg(mpns.size()).arg(mpns.join("\n  • ")));
}

// Browse + select datasheet PDFs for parts the harness couldn't source, copy them
// into the datasheets folder (named so the harness matches them by MPN), then
// resume the build so those parts get extracted.
void DesignChatPanel::extractFootprintFromDatasheet() {
    // Route the selected local PDF through the same typed Assistant tool used
    // for supplier URLs, preserving preview-before-publication approval.
    QString pdf = QFileDialog::getOpenFileName(this, "Select Datasheet PDF",
        QString(), "PDF Files (*.pdf);;All Files (*)");
    if (pdf.isEmpty()) return;
    bool accepted = false;
    const QString mpn = QInputDialog::getText(
        this, "Manufacturer Part Number", "Exact MPN printed in the datasheet:",
        QLineEdit::Normal, QFileInfo(pdf).completeBaseName(), &accepted).trimmed();
    if (!accepted || mpn.isEmpty()) {
        addMessage(ChatMessage::System, "Extraction cancelled: an exact MPN is required.");
        return;
    }
    submitPrompt(QStringLiteral(
        "Create a component CAD preview for exact MPN %1 using this local manufacturer datasheet: %2. "
        "Use Sol medium inspection, Luna xhigh typed CAD execution, and show the preview before publication.")
        .arg(mpn, QFileInfo(pdf).absoluteFilePath()));
}

void DesignChatPanel::provideDatasheets() {
    // Resolve the datasheets folder (same one the harness reads). Fall back to a
    // known-writable location if the repo root couldn't be located.
    QString dir = swarmPath("datasheets");
    if (dir.isEmpty() || !dir.contains("/") || dir == "/datasheets")
        dir = QDir::homePath() + "/DesignStudio/datasheets";
    if (!QDir().mkpath(dir)) {
        addMessage(ChatMessage::System,
            "Could not create the datasheets folder:\n" + dir);
        return;
    }
    // Open the browser at the user's home so they can pick from Downloads etc.
    QStringList files = QFileDialog::getOpenFileNames(
        this, "Select datasheet PDF(s) for the missing parts",
        QDir::homePath(), "Datasheets (*.pdf *.PDF *.Pdf);;All files (*)");
    if (files.isEmpty()) {
        addMessage(ChatMessage::System, "No datasheets selected.");
        return;
    }

    QStringList added, failed;
    for (const QString& src : files) {
        QFileInfo fi(src);
        const QString dest = dir + "/" + fi.fileName();
        // Already inside the folder? Nothing to copy.
        if (QFileInfo(src).canonicalFilePath() == QFileInfo(dest).canonicalFilePath()) {
            added << fi.fileName();
            continue;
        }
        QFile::remove(dest);                       // overwrite if re-supplying
        if (QFile::copy(src, dest) && QFile::exists(dest))
            added << fi.fileName();
        else
            failed << fi.fileName();
    }

    if (!added.isEmpty())
        addMessage(ChatMessage::System,
            QString("✓ Added %1 datasheet(s) to:\n%2\n  • %3")
                .arg(added.size()).arg(dir).arg(added.join("\n  • ")));
    if (!failed.isEmpty())
        addMessage(ChatMessage::System,
            QString("⚠ Could not copy %1 file(s): %2\n(check the source still "
                    "exists and the datasheets folder is writable)")
                .arg(failed.size()).arg(failed.join(", ")));
    if (added.isEmpty()) return;

    addMessage(ChatMessage::System,
        "The AI will match each PDF to a part by its file name AND by the part "
        "number printed inside the PDF, so generically-named files still work.");

    if (m_lastBuildIntent.isEmpty()) {
        addMessage(ChatMessage::System,
            "Datasheets stored. Type /build with your prompt to use them.");
        return;
    }
    // Resume: re-run the build — the harness now finds these local datasheets.
    addMessage(ChatMessage::System, "Resuming the build with the supplied datasheets…");
    m_pendingQuery = m_lastBuildIntent;
    onQuickAction("build");
}

// Start a fresh chat: archive the current conversation (nothing is lost) and
// clear the view back to the welcome state.
void DesignChatPanel::newChat() {
    if (m_agentRunning) {
        addMessage(ChatMessage::System, "An agent is still running — stop it first, "
                                        "then start a new chat.");
        return;
    }
    const QString path = chatLogPath();
    if (QFile::exists(path)) {
        const QString stamp = QDateTime::currentDateTime().toString("yyyyMMdd-HHmmss");
        QString arch = path;
        arch.replace(QRegularExpression("\\.jsonl$"), "-" + stamp + ".jsonl");
        QFile::rename(path, arch);
    }
    m_history.clear();
    m_streamingBuf.clear();
    m_streamingMessageIndex = -1;
    if (usingCodex() && m_codex) m_codex->newThread();
    renderAll();
    appendSystem("New design session started. Describe the complete industrial product; "
                 "Codex will propose mechanical, electrical and PCB stages for approval.");
}

// ── Footprint thumbnail strip ─────────────────────────────────────────────────
// Render one footprint (pads + body outline + ref) to a small square pixmap.
QPixmap DesignChatPanel::renderFootprintThumb(const ProjFootprint& fp, int size) const {
    double xmin=1e9, ymin=1e9, xmax=-1e9, ymax=-1e9;
    for (const auto& pad : fp.pads) {
        xmin=std::min(xmin, pad.x_mm-pad.w_mm/2); xmax=std::max(xmax, pad.x_mm+pad.w_mm/2);
        ymin=std::min(ymin, pad.y_mm-pad.h_mm/2); ymax=std::max(ymax, pad.y_mm+pad.h_mm/2);
    }
    if (fp.bodyW_mm > 0.0) {
        xmin=std::min(xmin, fp.bodyCx_mm-fp.bodyW_mm/2); xmax=std::max(xmax, fp.bodyCx_mm+fp.bodyW_mm/2);
        ymin=std::min(ymin, fp.bodyCy_mm-fp.bodyH_mm/2); ymax=std::max(ymax, fp.bodyCy_mm+fp.bodyH_mm/2);
    }
    QPixmap pm(size, size);
    pm.fill(QColor("#11161f"));
    if (xmax < xmin) return pm;
    QPainter p(&pm);
    p.setRenderHint(QPainter::Antialiasing);
    const double span = std::max(xmax-xmin, ymax-ymin) * 1.18 + 0.001;
    const double sc = (size - 14) / span;
    p.translate(size/2.0, size/2.0);
    p.scale(sc, -sc);                                  // mm → px, y-up
    p.translate(-(xmin+xmax)/2.0, -(ymin+ymax)/2.0);
    p.rotate(-fp.rotDeg);
    // body outline
    if (fp.bodyW_mm > 0.0) {
        p.setPen(QPen(QColor(120,130,145), span*0.012));
        p.setBrush(Qt::NoBrush);
        p.drawRect(QRectF(fp.bodyCx_mm-fp.bodyW_mm/2, fp.bodyCy_mm-fp.bodyH_mm/2,
                          fp.bodyW_mm, fp.bodyH_mm));
    }
    // pads
    p.setPen(Qt::NoPen);
    p.setBrush(QColor("#c8a040"));
    for (const auto& pad : fp.pads) {
        if (pad.throughHole)
            p.drawEllipse(QPointF(pad.x_mm, pad.y_mm), pad.w_mm/2, pad.h_mm/2);
        else
            p.drawRect(QRectF(pad.x_mm-pad.w_mm/2, pad.y_mm-pad.h_mm/2, pad.w_mm, pad.h_mm));
    }
    return pm;
}

void DesignChatPanel::updateFootprintStrip() {
    if (!m_fpStripLayout || !m_model) return;
    // clear existing items (keep the trailing stretch)
    while (m_fpStripLayout->count() > 0) {
        QLayoutItem* it = m_fpStripLayout->takeAt(0);
        if (it->widget()) it->widget()->deleteLater();
        delete it;
    }
    const auto& fps = m_model->footprints;
    if (fps.isEmpty()) { m_fpStrip->setVisible(false); return; }
    for (const auto& fp : fps) {
        auto* cell = new QWidget;
        auto* cl = new QVBoxLayout(cell);
        cl->setContentsMargins(0,0,0,0); cl->setSpacing(2);
        auto* img = new QLabel;
        img->setPixmap(renderFootprintThumb(fp, 80));
        img->setFixedSize(80, 80);
        img->setToolTip(fp.ref + "  " + fp.lib + QString("  (%1 pads)").arg(fp.pads.size()));
        auto* cap = new QLabel(fp.ref);
        cap->setAlignment(Qt::AlignHCenter);
        cap->setStyleSheet("color:#9aa4b2;font-size:10px;");
        cl->addWidget(img); cl->addWidget(cap);
        m_fpStripLayout->addWidget(cell);
    }
    m_fpStripLayout->addStretch();
    m_fpStrip->setVisible(true);
}

// Mouse wheel over the strip scrolls it horizontally (a row, no vertical scroll).
bool DesignChatPanel::eventFilter(QObject* obj, QEvent* ev) {
    if (m_fpStrip && obj == m_fpStrip->viewport() && ev->type() == QEvent::Wheel) {
        auto* we = static_cast<QWheelEvent*>(ev);
        const int d = we->angleDelta().y() != 0 ? we->angleDelta().y() : we->angleDelta().x();
        auto* bar = m_fpStrip->horizontalScrollBar();
        bar->setValue(bar->value() - d);
        return true;
    }
    return QWidget::eventFilter(obj, ev);
}

// ── Agent process management ──────────────────────────────────────────────────
void DesignChatPanel::runAgent(const QString& script, const QStringList& args, const QString& label) {
    if (!QFile::exists(script)) {
        addMessage(ChatMessage::System,
            "Agent script not found:\n" + script +
            "\n\nMake sure you cloned the full repo and the swarm/ directory is present.");
        return;
    }

    m_agentRunning = true;
    m_activeAgent  = label;
    updateRunControl(true);
    m_progress->setVisible(true);
    m_statusLabel->setText("Running: " + label);

    startAssistantBubble(label);
    emit agentStarted(label);

    m_proc = new QProcess(this);
    m_proc->setProcessChannelMode(QProcess::SeparateChannels);

    connect(m_proc, &QProcess::readyReadStandardOutput, this, &DesignChatPanel::onProcessReadyOut);
    connect(m_proc, &QProcess::readyReadStandardError,  this, &DesignChatPanel::onProcessReadyErr);
    connect(m_proc, QOverload<int,QProcess::ExitStatus>::of(&QProcess::finished),
            this, &DesignChatPanel::onProcessFinished);

    // Set PYTHONPATH so agents can import from swarm/
    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    QString swarmAgents = swarmPath("swarm/agents");
    QString swarmMcp    = swarmPath("swarm/mcp_servers");
    QString existing    = env.value("PYTHONPATH");
    env.insert("PYTHONPATH", swarmAgents + ":" + swarmMcp +
               (existing.isEmpty() ? "" : ":" + existing));
    m_proc->setProcessEnvironment(env);
    m_proc->setWorkingDirectory(SWARM_ROOT);

    m_proc->start("python3", QStringList{script} + args);
    if (!m_proc->waitForStarted(3000)) {
        addMessage(ChatMessage::System, "Failed to start python3. Is Python 3 installed?");
        onProcessFinished(-1, QProcess::CrashExit);
    }
}

void DesignChatPanel::stopAgent() {
    bool stopped = false;
    // Stop also cancels the legacy build→placement→routing→DRC chain.  A
    // terminated child must never trigger the next automatic stage.
    m_autoFinish = false;
    if (usingCodex() && m_codex && m_agentRunning) {
        // Do not wait for turn/interrupt or turn/completed.  Some app-server
        // versions keep the transport open after an interrupt request, which
        // left the Stop square permanently visible.  Terminate the supervised
        // transport now, clear queued work, and restore the composer in the
        // same event turn.
        m_codex->stop();
        if (m_choiceFrame) m_choiceFrame->hide();
        if (m_approvalFrame) m_approvalFrame->hide();
        m_approvalToken.clear();
        appendToCurrentBubble(QStringLiteral("\n[stopped by user]"));
        finalizeCurrentBubble();
        m_agentRunning = false;
        updateRunControl(false);
        m_progress->hide();
        m_statusLabel->setText(QStringLiteral("Stopped by user"));
        emit agentFinished(QStringLiteral("codex"), false);
        emit codexRunStopped();
        stopped = true;
    }
    if (m_datasheetProc && m_datasheetProc->state() != QProcess::NotRunning) {
        m_datasheetProc->terminate();
        if (!m_datasheetProc->waitForFinished(2000)) m_datasheetProc->kill();
        if (m_datasheetProc && m_datasheetProc->state() != QProcess::NotRunning)
            m_datasheetProc->waitForFinished(1500);
        stopped = true;
    }
    if (m_productBuildProc && m_productBuildProc->state() != QProcess::NotRunning) {
        m_productBuildProc->terminate();
        if (!m_productBuildProc->waitForFinished(2000)) m_productBuildProc->kill();
        if (m_productBuildProc && m_productBuildProc->state() != QProcess::NotRunning)
            m_productBuildProc->waitForFinished(1500);
        stopped = true;
    }
    if (m_componentPublishProc && m_componentPublishProc->state() != QProcess::NotRunning) {
        m_componentPublishProc->terminate();
        if (!m_componentPublishProc->waitForFinished(2000)) m_componentPublishProc->kill();
        if (m_componentPublishProc && m_componentPublishProc->state() != QProcess::NotRunning)
            m_componentPublishProc->waitForFinished(1500);
        stopped = true;
    }
    if (m_proc && m_proc->state() != QProcess::NotRunning) {
        m_proc->terminate();
        m_proc->waitForFinished(2000);
        if (m_proc->state() != QProcess::NotRunning)
            m_proc->kill();
        if (m_proc->state() != QProcess::NotRunning)
            m_proc->waitForFinished(1500);
        stopped = true;
    }
    // The startup/no-turn branch finalizes its bubble synchronously above;
    // other workers append the marker here and finish through their callback.
    if (stopped && m_agentRunning)
        appendToCurrentBubble(QStringLiteral("\n[stopped by user]"));
}

// Map a raw agent log line to a short, human-friendly "current task" shown live
// in the status bar — so the user sees what the assistant is doing right now.
static QString friendlyTask(const QString& line) {
    const QString t = line.toLower();
    if (t.contains("session") && t.contains("started"))   return "Starting design…";
    if (t.contains("intent classified"))                  return "Understanding your request…";
    if (t.contains("similar past designs"))               return "Recalling similar designs…";
    if (t.contains("proposed") && t.contains("component")) return "Selecting components…";
    if (t.contains("reading datasheet") || t.contains("extracting")) return "Reading datasheets…";
    if (t.contains("extracted "))                         return "Reading datasheets…";
    if (t.contains("land pattern") || t.contains("footprint from")
        || t.contains("dimensions"))                      return "Building exact footprints…";
    if (t.contains("needs datasheet") || t.contains("could not source")) return "Some parts need datasheets…";
    if (t.startsWith("[harness]   ") && t.contains("→"))   return "Building footprints…";
    if (t.contains("expand:"))                            return "Adding support components…";
    if (t.contains("proposed netlist"))                   return "Designing the connections…";
    if (t.contains("verify:") || t.contains("verification")) return "Verifying the design…";
    if (t.contains("footprint review"))                   return "Checking footprints…";
    if (t.contains("applied to board"))                   return "Placing on the board…";
    if (t.contains("subsystem") || t.contains("placed"))  return "Arranging components…";
    if (t.contains("routing") || t.contains("routed"))    return "Routing the traces…";
    if (t.contains("drc"))                                return "Checking design rules…";
    if (t.contains("advisor") || t.contains("analyzing")) return "Reviewing the circuit…";
    return QString();
}

void DesignChatPanel::onProcessReadyOut() {
    if (!m_proc) return;
    QString out = QString::fromUtf8(m_proc->readAllStandardOutput());
    appendToCurrentBubble(out);
    // Surface the current task live, from the newest recognized progress line.
    const QStringList lines = out.split('\n', Qt::SkipEmptyParts);
    for (int i = lines.size() - 1; i >= 0; --i) {
        const QString task = friendlyTask(lines[i]);
        if (!task.isEmpty()) { m_statusLabel->setText("⚙ " + task); break; }
    }
}

void DesignChatPanel::onProcessReadyErr() {
    if (!m_proc) return;
    QString err = QString::fromUtf8(m_proc->readAllStandardError());
    // Show stderr in a muted color — it's usually progress info
    if (!err.trimmed().isEmpty())
        appendToCurrentBubble("\n[stderr] " + err);
}

void DesignChatPanel::onProcessFinished(int exitCode, QProcess::ExitStatus status) {
    bool ok = (exitCode == 0 && status == QProcess::NormalExit);
    finalizeCurrentBubble();

    m_agentRunning = false;
    updateRunControl(false);
    m_progress->setVisible(false);

    QString statusMsg = ok
        ? (m_activeAgent + " completed.")
        : (m_activeAgent + " exited with code " + QString::number(exitCode));
    m_statusLabel->setText(statusMsg);

    const QString finished = m_activeAgent;
    emit agentFinished(m_activeAgent, ok);

    if (m_proc) { m_proc->deleteLater(); m_proc = nullptr; }

    // ── Auto-finish chain: a successful /build → place → route → DRC ──────────
    if (ok && finished == "design-harness") {
        // Report any parts that couldn't be sourced (offer /datasheets to resume).
        checkUnrealizedParts();
        // The harness applied a board; advance to placement.
        m_autoFinish = true;
        addMessage(ChatMessage::System,
            "Design applied to the board — auto-finishing: placing → routing → DRC…");
        if (!m_projectPath.isEmpty())
            runAgent(swarmPath("swarm/agents/subsystem_placer_agent.py"),
                     {m_projectPath}, "subsystem-placer");
    } else if (ok && (finished == "subsystem-placer" || finished == "netlist-placer")) {
        emit nativeJobRequested("placement-optimized");
        if (m_autoFinish && finished == "subsystem-placer") {
        addMessage(ChatMessage::System, "Placed by sub-system — routing…");
        emit nativeJobRequested("save");
        emit nativeJobRequested("route");   // MainWindow routes; on finish calls
                                            // continueAutoFinishAfterRoute()
        }
    } else if (ok && finished == "reapply") {
        // Footprints regenerated + routing cleared in the external disk file.
        // Re-route so the via-in-pad keepout and new pads take effect, then the
        // route-completion path runs DRC + the circuit advisor.
        m_autoFinish = true;
        addMessage(ChatMessage::System,
            "Footprints refreshed and routing cleared — re-routing with the "
            "via-in-pad fix…");
        emit nativeJobRequested("route");
    } else if (m_autoFinish && finished == "subsystem-placer" && !ok) {
        m_autoFinish = false;
        addMessage(ChatMessage::System, "Placement failed — stopping auto-finish.");
    } else if (m_autoFinish && finished == "drc-analysis") {
        // Final auto-finish step: components placed and DRC done → automatically
        // run the circuit advisor over the finished design. It takes the schematic
        // + behaviour (circuit-state) and reviews it with the circuit-advisor prompt.
        m_autoFinish = false;
        addMessage(ChatMessage::System,
            "DRC done — running the circuit advisor over the finished design "
            "(schematic + behaviour state)…");
        QString statePath = exportCircuitState();
        if (!statePath.isEmpty())
            runAgent(swarmPath("swarm/agents/circuit_advisor_agent.py"),
                     {statePath}, "circuit-advisor");
    } else if (ok && finished == "fab-export") {
        // Exporting fab files is a strong "I'm committing to this design" signal —
        // record it so the design memory learns this design shipped.
        recordOutcome("exported");
    }
}

// Called by MainWindow once the native route completes, to run the final DRC
// step of the auto-finish chain.
void DesignChatPanel::continueAutoFinishAfterRoute() {
    if (!m_autoFinish) return;
    // Keep m_autoFinish TRUE through DRC so its completion triggers the final
    // step (circuit advisor) in onProcessFinished.
    addMessage(ChatMessage::System, "Routed — running DRC to finish…");
    if (m_projectPath.isEmpty()) { m_autoFinish = false; return; }
    emit nativeJobRequested("save");
    runAgent(swarmPath("swarm/agents/drc_agent.py"), {m_projectPath}, "drc-analysis");
}

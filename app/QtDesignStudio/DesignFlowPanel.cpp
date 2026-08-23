#include "DesignFlowPanel.h"

#include <QFrame>
#include <QGridLayout>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QPushButton>
#include <QScrollArea>
#include <QVBoxLayout>

namespace {
struct StageSpec {
    const char* id;
    const char* title;
    const char* description;
    const char* output;
};

constexpr StageSpec kStages[] = {
    {"intent", "Intent & evidence",
     "Capture the product purpose, users, reference files, constraints, and explicit unknowns.",
     "Intent contract + evidence manifest"},
    {"requirements", "Requirements contract",
     "Turn intent into measurable mechanical, electrical, human, safety, and manufacturing requirements.",
     "Versioned requirements + assumptions"},
    {"architecture", "Concept & system architecture",
     "Compare concepts, choose interfaces, and lock the product-level architecture before construction.",
     "Approved concept + product graph"},
    {"mechanical", "Mechanical form & CAD",
     "Place exact hardware volumes, build the enclosure and feature tree, and review exterior/interior views.",
     "FreeCAD B-Rep + visual review"},
    {"electronics", "Electronics & components",
     "Bind exact MPNs to datasheet evidence, define power/signals, and create the electrical architecture.",
     "Schematic + evidence-bound BOM"},
    {"pcb_harness", "PCB, harness & integration",
     "Place the PCB, compare topology, route signals and power, and check connectors, bend radii, and service paths.",
     "PCB + harness integration preview"},
    {"verification", "Visual & engineering review",
     "Run deterministic geometry, interference, DRC, thermal, clearance, manufacturing, and visual-observer checks.",
     "Hash-bound verification report"},
    {"release", "Drawings & manufacturing release",
     "Generate authoritative drawings, GD&T records, release artifacts, approvals, and a fail-closed manifest.",
     "Signed release package or blocked report"},
};

QString statusColor(const QString& status)
{
    if (status == QStringLiteral("Complete")) return QStringLiteral("#3fb950");
    if (status == QStringLiteral("Active")) return QStringLiteral("#58a6ff");
    if (status == QStringLiteral("Awaiting approval")) return QStringLiteral("#d29922");
    if (status == QStringLiteral("Blocked")) return QStringLiteral("#f85149");
    if (status == QStringLiteral("Ready")) return QStringLiteral("#8b949e");
    return QStringLiteral("#6e7681");
}

QString cardStyle(const QString& color)
{
    return QStringLiteral(
        "QFrame#industrial_flow_stage{background:#20252B;border:1px solid %1;"
        "border-radius:8px;}"
        "QLabel#industrial_flow_stage_title{color:#F0F6FC;font-size:14px;font-weight:700;}"
        "QLabel#industrial_flow_stage_description{color:#AAB4C0;font-size:12px;}"
        "QLabel#industrial_flow_stage_output{color:#8B949E;font-size:11px;}"
        "QLabel#industrial_flow_stage_status{color:%1;font-size:11px;font-weight:700;}"
    ).arg(color);
}
} // namespace

DesignFlowPanel::DesignFlowPanel(QWidget* parent) : QWidget(parent)
{
    setObjectName(QStringLiteral("industrial_design_flow"));
    auto* root = new QVBoxLayout(this);
    root->setContentsMargins(28, 24, 28, 24);
    root->setSpacing(12);

    auto* title = new QLabel(QStringLiteral("Industrial Design Flow"), this);
    title->setStyleSheet(QStringLiteral("font-size:22px;font-weight:700;color:#F0F6FC;"));
    root->addWidget(title);
    auto* subtitle = new QLabel(
        QStringLiteral("One visible path from product intent to manufacturing release. "
                       "Codex proposes and explains; typed CAD/EDA tools calculate; you approve every mutation."),
        this);
    subtitle->setWordWrap(true);
    subtitle->setStyleSheet(QStringLiteral("color:#AAB4C0;font-size:13px;"));
    root->addWidget(subtitle);

    auto* startCard = new QFrame(this);
    startCard->setObjectName(QStringLiteral("industrial_flow_start"));
    startCard->setStyleSheet(QStringLiteral(
        "QFrame#industrial_flow_start{background:#252B32;border:1px solid #4E687F;border-radius:8px;}"));
    auto* startLayout = new QVBoxLayout(startCard);
    startLayout->setContentsMargins(14, 12, 14, 12);
    auto* startTitle = new QLabel(QStringLiteral("Start or resume a product flow"), startCard);
    startTitle->setStyleSheet(QStringLiteral("font-weight:700;color:#D9E8F5;font-size:14px;"));
    startLayout->addWidget(startTitle);
    auto* startRow = new QHBoxLayout;
    m_productInput = new QLineEdit(startCard);
    m_productInput->setObjectName(QStringLiteral("industrial_flow_product_input"));
    m_productInput->setPlaceholderText(
        QStringLiteral("Product idea, function, or reference link (e.g. electric scooter controller)"));
    m_productInput->setMinimumHeight(36);
    m_startButton = new QPushButton(QStringLiteral("Start guided flow"), startCard);
    m_startButton->setObjectName(QStringLiteral("industrial_flow_start_button"));
    m_startButton->setMinimumHeight(36);
    m_startButton->setStyleSheet(QStringLiteral(
        "QPushButton{background:#3974A8;color:#FFFFFF;border:none;border-radius:6px;padding:7px 14px;font-weight:700;}"
        "QPushButton:hover{background:#4A89C0;}"));
    m_resumeButton = new QPushButton(QStringLiteral("Resume active flow"), startCard);
    m_resumeButton->setObjectName(QStringLiteral("industrial_flow_resume_button"));
    m_resumeButton->setMinimumHeight(36);
    startRow->addWidget(m_productInput, 1);
    startRow->addWidget(m_startButton);
    startRow->addWidget(m_resumeButton);
    startLayout->addLayout(startRow);
    m_flowState = new QLabel(
        QStringLiteral("Not started · no CAD or electronics will be changed until approval."), startCard);
    m_flowState->setObjectName(QStringLiteral("industrial_flow_state"));
    m_flowState->setWordWrap(true);
    m_flowState->setStyleSheet(QStringLiteral("color:#8B949E;font-size:11px;"));
    startLayout->addWidget(m_flowState);
    root->addWidget(startCard);

    auto* scroll = new QScrollArea(this);
    scroll->setObjectName(QStringLiteral("industrial_flow_stage_scroll"));
    scroll->setWidgetResizable(true);
    scroll->setFrameShape(QFrame::NoFrame);
    auto* stageHost = new QWidget(scroll);
    auto* stageGrid = new QGridLayout(stageHost);
    stageGrid->setContentsMargins(0, 0, 0, 0);
    stageGrid->setHorizontalSpacing(10);
    stageGrid->setVerticalSpacing(10);

    for (int i = 0; i < int(sizeof(kStages) / sizeof(kStages[0])); ++i) {
        const StageSpec& spec = kStages[i];
        auto* card = new QFrame(stageHost);
        card->setObjectName(QStringLiteral("industrial_flow_stage"));
        card->setMinimumHeight(142);
        card->setStyleSheet(cardStyle(QStringLiteral("#6e7681")));
        auto* cardLayout = new QVBoxLayout(card);
        cardLayout->setContentsMargins(12, 10, 12, 10);
        cardLayout->setSpacing(5);
        auto* header = new QHBoxLayout;
        auto* number = new QLabel(QStringLiteral("%1").arg(i + 1), card);
        number->setFixedSize(24, 24);
        number->setAlignment(Qt::AlignCenter);
        number->setStyleSheet(QStringLiteral(
            "background:#303945;color:#D9E8F5;border-radius:12px;font-weight:700;"));
        auto* stageTitle = new QLabel(QString::fromLatin1(spec.title), card);
        stageTitle->setObjectName(QStringLiteral("industrial_flow_stage_title"));
        auto* status = new QLabel(QStringLiteral("Not started"), card);
        status->setObjectName(QStringLiteral("industrial_flow_stage_status"));
        status->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
        header->addWidget(number);
        header->addWidget(stageTitle, 1);
        header->addWidget(status);
        cardLayout->addLayout(header);
        auto* description = new QLabel(QString::fromLatin1(spec.description), card);
        description->setObjectName(QStringLiteral("industrial_flow_stage_description"));
        description->setWordWrap(true);
        cardLayout->addWidget(description, 1);
        auto* output = new QLabel(QStringLiteral("Output · %1").arg(QString::fromLatin1(spec.output)), card);
        output->setObjectName(QStringLiteral("industrial_flow_stage_output"));
        output->setWordWrap(true);
        cardLayout->addWidget(output);
        stageGrid->addWidget(card, i / 2, i % 2);
        m_stages.append({QString::fromLatin1(spec.id), card, status, description});
    }
    stageGrid->setColumnStretch(0, 1);
    stageGrid->setColumnStretch(1, 1);
    scroll->setWidget(stageHost);
    root->addWidget(scroll, 1);

    auto* rules = new QLabel(
        QStringLiteral("Flow rules · unknowns stay visible · references never become CAD authority · "
                       "deterministic checks can block release · every mutating operation requires approval."),
        this);
    rules->setWordWrap(true);
    rules->setStyleSheet(QStringLiteral(
        "color:#8B949E;background:#1F242B;border:1px solid #30363D;border-radius:5px;padding:8px;font-size:11px;"));
    root->addWidget(rules);

    connect(m_startButton, &QPushButton::clicked, this, [this] {
        m_flowActive = true;
        m_currentStage = 0;
        setStageStatus(0, QStringLiteral("Active"),
                       QStringLiteral("Collecting intent, reference evidence, and unknowns."));
        m_flowState->setText(QStringLiteral(
            "Flow active · Codex will ask only the questions that change architecture, cost, safety, or manufacturing."));
        emit startRequested(QStringLiteral(
            "Start the automatic DesignStudio industrial design flow for: %1. "
            "Follow the visible stages in order: intent/evidence, requirements contract, concept and system architecture, "
            "mechanical CAD with exterior/interior visual review, exact electronics and datasheet binding, PCB and harness integration, "
            "deterministic engineering verification, authoritative drawings/GD&T, and fail-closed manufacturing release. "
            "Ask no more than three high-impact questions at a time. Keep assumptions and unknowns explicit. "
            "Do not mutate any workspace until I approve the proposed operation.").arg(flowProduct()));
    });
    connect(m_resumeButton, &QPushButton::clicked, this, [this] {
        m_flowActive = true;
        if (m_currentStage < 0) m_currentStage = 0;
        m_flowState->setText(QStringLiteral(
            "Resuming the active flow · read the authoritative workspace state before proposing the next stage."));
        emit resumeRequested(QStringLiteral(
            "Resume the active DesignStudio industrial design flow from the authoritative workspace state. "
            "Identify the first incomplete stage, show its inputs and missing evidence, and continue only with an approval-gated proposal."));
    });
}

QString DesignFlowPanel::flowProduct() const
{
    const QString product = m_productInput ? m_productInput->text().trimmed() : QString();
    return product.isEmpty() ? QStringLiteral("a new industrial electromechanical product") : product;
}

void DesignFlowPanel::setWorkspaceState(bool open, const QString& detail)
{
    if (!m_flowState) return;
    if (open) {
        m_flowState->setText(detail.isEmpty()
            ? QStringLiteral("Workspace open · resume the active flow or start a new product flow.")
            : QStringLiteral("Workspace open · %1").arg(detail));
        if (!m_flowActive && m_currentStage < 0)
            setStageStatus(0, QStringLiteral("Ready"), QStringLiteral("Workspace is available for intent review."));
    } else if (!m_flowActive) {
        m_flowState->setText(QStringLiteral(
            "Not started · describe a product above. No CAD or electronics will be changed until approval."));
    }
}

void DesignFlowPanel::setToolPending(const QString& tool, const QJsonObject& arguments)
{
    const int index = stageForTool(tool);
    if (index < 0) return;
    m_flowActive = true;
    for (int i = 0; i < index; ++i) {
        const QString priorStatus = m_stages.at(i).status->text();
        if (priorStatus != QStringLiteral("Blocked")
            && priorStatus != QStringLiteral("Complete"))
            setStageStatus(i, QStringLiteral("Complete"),
                           QStringLiteral("Previous stage completed before this operation."));
    }
    m_currentStage = index;
    QString detail = QStringLiteral("Proposed operation · %1").arg(tool);
    if (!arguments.isEmpty()) detail += QStringLiteral(" · review the approval card in Codex.");
    setStageStatus(index, QStringLiteral("Awaiting approval"), detail);
    m_flowState->setText(QStringLiteral("Flow paused at stage %1 · review the typed operation in Codex.")
                         .arg(index + 1));
}

void DesignFlowPanel::setToolResult(const QString& tool, bool ok, const QString& detail)
{
    const int index = stageForTool(tool);
    if (index < 0) return;
    if (ok) {
        setStageStatus(index, QStringLiteral("Complete"),
                       detail.isEmpty() ? QStringLiteral("Typed operation completed.") : detail);
        if (index + 1 < m_stages.size()
            && m_stages.at(index + 1).status->text() == QStringLiteral("Not started"))
            setStageStatus(index + 1, QStringLiteral("Ready"), QStringLiteral("Waiting for the preceding flow output."));
        m_flowState->setText(QStringLiteral("Stage %1 complete · the next stage is ready when its inputs are valid.")
                             .arg(index + 1));
    } else {
        setStageStatus(index, QStringLiteral("Blocked"),
                       detail.isEmpty() ? QStringLiteral("Operation failed or evidence is incomplete.") : detail);
        m_flowState->setText(QStringLiteral("Flow blocked at stage %1 · resolve the evidence or revise the proposal.")
                             .arg(index + 1));
    }
}

void DesignFlowPanel::setStageStatus(int index, const QString& status, const QString& detail)
{
    if (index < 0 || index >= m_stages.size()) return;
    StageView& stage = m_stages[index];
    const QString color = statusColor(status);
    stage.status->setText(status);
    stage.status->setStyleSheet(QStringLiteral(
        "color:%1;font-size:11px;font-weight:700;").arg(color));
    stage.detail->setText(detail);
    stage.detail->setStyleSheet(QStringLiteral(
        "color:%1;font-size:12px;").arg(status == QStringLiteral("Blocked") ? QStringLiteral("#F85149") : QStringLiteral("#AAB4C0")));
    stage.card->setStyleSheet(cardStyle(color));
}

int DesignFlowPanel::stageForTool(const QString& tool) const
{
    const QString name = tool.trimmed().toLower();
    if (name == QStringLiteral("read_product_state")) return -1;
    if (name == QStringLiteral("create_requirements_contract")
        || name == QStringLiteral("derive_requirements")
        || name == QStringLiteral("lock_requirements")) return 1;
    if (name == QStringLiteral("create_product_workspace")) return 2;
    if (name.contains(QStringLiteral("physical"))
        || name.contains(QStringLiteral("mechanical"))
        || name.contains(QStringLiteral("enclosure"))
        || name.contains(QStringLiteral("interaction"))
        || name.contains(QStringLiteral("redesign"))
        || name == QStringLiteral("apply_mechanical_stage")
        || name == QStringLiteral("apply_mechanical_cad_program")) return 3;
    if (name.contains(QStringLiteral("datasheet"))
        || name.contains(QStringLiteral("electrical"))
        || name.contains(QStringLiteral("component"))
        || name == QStringLiteral("build_electronics_from_datasheets")) return 4;
    if (name.contains(QStringLiteral("pcb"))
        || name.contains(QStringLiteral("topology"))
        || name.contains(QStringLiteral("harness"))
        || name == QStringLiteral("apply_pcb_stage")) return 5;
    if (name.contains(QStringLiteral("draw"))
        || name.contains(QStringLiteral("release"))
        || name == QStringLiteral("generate_authoritative_drawings")
        || name.contains(QStringLiteral("commit_configuration"))) return 7;
    if (name.contains(QStringLiteral("check"))
        || name.contains(QStringLiteral("verification"))) return 6;
    return -1;
}

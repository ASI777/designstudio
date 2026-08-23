#include "PhysicalDesignPanel.h"

#include <QCheckBox>
#include <QComboBox>
#include <QCryptographicHash>
#include <QDateTime>
#include <QDoubleSpinBox>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFormLayout>
#include <QGroupBox>
#include <QHeaderView>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QLabel>
#include <QLineEdit>
#include <QPushButton>
#include <QSet>
#include <QSpinBox>
#include <QStringList>
#include <QTabWidget>
#include <QTableWidget>
#include <QVBoxLayout>

namespace {
QWidget* page(const QString& title, const QString& explanation, QTabWidget* tabs,
              const QString& objectName)
{
    auto* widget = new QWidget(tabs);
    widget->setObjectName(objectName);
    auto* layout = new QVBoxLayout(widget);
    auto* heading = new QLabel(QStringLiteral("<h2>%1</h2>").arg(title), widget);
    auto* help = new QLabel(explanation, widget);
    help->setWordWrap(true);
    layout->addWidget(heading);
    layout->addWidget(help);
    tabs->addTab(widget, title);
    return widget;
}

QJsonArray point(double x, double y, double z)
{
    return QJsonArray{x, y, z};
}

QJsonObject operationPayload(const QJsonObject& result)
{
    // MainWindow wraps the FreeCAD dispatcher result so the chat transport can
    // retain both host and native status.  Tests and future callers may pass
    // either form, therefore unwrap only while a nested data object exists.
    QJsonObject payload = result;
    for (int depth = 0; depth < 2; ++depth) {
        const QJsonObject nested = payload.value(QStringLiteral("data")).toObject();
        if (nested.isEmpty()) break;
        payload = nested;
    }
    return payload;
}

QString selectedFacesText(const QJsonObject& payload)
{
    QJsonObject contract = payload.value(QStringLiteral("redesign")).toObject();
    if (contract.isEmpty())
        contract = payload.value(QStringLiteral("contract")).toObject();
    const QJsonArray faces = contract.value(QStringLiteral("target")).toObject()
                                 .value(QStringLiteral("selected_faces")).toArray();
    QStringList names;
    names.reserve(faces.size());
    for (const QJsonValue& face : faces) {
        if (face.isString()) names.append(face.toString());
    }
    return names.join(QStringLiteral(", "));
}
}

PhysicalDesignPanel::PhysicalDesignPanel(QWidget* parent) : QWidget(parent)
{
    setObjectName(QStringLiteral("physical_design_panel"));
    auto* outer = new QVBoxLayout(this);
    auto* notice = new QLabel(QStringLiteral(
        "Guided physical design · images and sketches are evidence only; verified dimensions and FreeCAD B-Reps control geometry."), this);
    notice->setObjectName(QStringLiteral("physical_design_authority_notice"));
    notice->setWordWrap(true);
    outer->addWidget(notice);
    m_steps = new QTabWidget(this);
    m_steps->setObjectName(QStringLiteral("physical_design_steps"));
    outer->addWidget(m_steps, 1);

    QWidget* evidence = page(QStringLiteral("1 Evidence"),
        QStringLiteral("Choose an object photo, render, scan or drawn sketch. The immutable SHA-256 is shown; pixels never become CAD authority."),
        m_steps, QStringLiteral("physical_step_evidence"));
    auto* evidenceForm = new QFormLayout;
    m_evidenceKind = new QComboBox(evidence);
    m_evidenceKind->setObjectName(QStringLiteral("physical_evidence_kind"));
    m_evidenceKind->addItems({QStringLiteral("object_photo"), QStringLiteral("hand_sketch"),
                              QStringLiteral("scan"), QStringLiteral("engineering_drawing")});
    m_evidencePath = new QLineEdit(evidence);
    m_evidencePath->setObjectName(QStringLiteral("physical_evidence_path"));
    m_evidencePath->setReadOnly(true);
    auto* choose = new QPushButton(QStringLiteral("Upload photo or sketch…"), evidence);
    choose->setObjectName(QStringLiteral("physical_evidence_upload"));
    m_evidenceDigest = new QLabel(QStringLiteral("No evidence selected"), evidence);
    m_evidenceDigest->setObjectName(QStringLiteral("physical_evidence_sha256"));
    evidenceForm->addRow(QStringLiteral("Evidence kind"), m_evidenceKind);
    evidenceForm->addRow(choose, m_evidencePath);
    evidenceForm->addRow(QStringLiteral("SHA-256"), m_evidenceDigest);
    qobject_cast<QVBoxLayout*>(evidence->layout())->addLayout(evidenceForm);
    connect(choose, &QPushButton::clicked, this, &PhysicalDesignPanel::chooseEvidence);

    QWidget* calibration = page(QStringLiteral("2 Calibration"),
        QStringLiteral("Enter a known physical length or verified drawing scale. Unverified dimensions remain unresolved."),
        m_steps, QStringLiteral("physical_step_calibration"));
    auto* calibrationForm = new QFormLayout;
    m_knownLength = new QDoubleSpinBox(calibration);
    m_knownLength->setObjectName(QStringLiteral("physical_known_length_mm"));
    m_knownLength->setRange(0.01, 10000); m_knownLength->setValue(160); m_knownLength->setSuffix(QStringLiteral(" mm"));
    m_pixelsPerMm = new QDoubleSpinBox(calibration);
    m_pixelsPerMm->setObjectName(QStringLiteral("physical_pixels_per_mm"));
    m_pixelsPerMm->setRange(0.001, 10000); m_pixelsPerMm->setValue(4.0);
    calibrationForm->addRow(QStringLiteral("Known length"), m_knownLength);
    calibrationForm->addRow(QStringLiteral("Calibrated pixels/mm"), m_pixelsPerMm);
    qobject_cast<QVBoxLayout*>(calibration->layout())->addLayout(calibrationForm);

    QWidget* workflow = page(QStringLiteral("3 Workflow"),
        QStringLiteral("Choose hardware-first (inside-out), appearance-first (outside-in), or coupled co-design."),
        m_steps, QStringLiteral("physical_step_workflow"));
    m_workflowMode = new QComboBox(workflow);
    m_workflowMode->setObjectName(QStringLiteral("physical_workflow_mode"));
    m_workflowMode->addItem(QStringLiteral("Hardware first · inside-out"), QStringLiteral("inside_out"));
    m_workflowMode->addItem(QStringLiteral("Appearance first · outside-in"), QStringLiteral("outside_in"));
    m_workflowMode->addItem(QStringLiteral("Coupled co-design"), QStringLiteral("co_design"));
    qobject_cast<QVBoxLayout*>(workflow->layout())->addWidget(m_workflowMode);

    QWidget* hardware = page(QStringLiteral("4 Locked Volumes"),
        QStringLiteral("Lock hardware, mechanism, PCB, battery, display, connector, cable, hand-clearance and service volumes before enclosure generation."),
        m_steps, QStringLiteral("physical_step_locked_volumes"));
    m_volumeKind = new QComboBox(hardware);
    m_volumeKind->setObjectName(QStringLiteral("physical_locked_volume_kind"));
    m_volumeKind->addItems({QStringLiteral("hardware"), QStringLiteral("mechanism"), QStringLiteral("pcb"),
        QStringLiteral("battery"), QStringLiteral("display"), QStringLiteral("connector"),
        QStringLiteral("cable"), QStringLiteral("human_clearance"), QStringLiteral("service")});
    m_volumes = new QTableWidget(0, 8, hardware);
    m_volumes->setObjectName(QStringLiteral("physical_locked_volumes"));
    m_volumes->setHorizontalHeaderLabels({QStringLiteral("Kind"), QStringLiteral("Semantic ID"),
        QStringLiteral("X min"), QStringLiteral("Y min"), QStringLiteral("Z min"),
        QStringLiteral("X max"), QStringLiteral("Y max"), QStringLiteral("Z max")});
    m_volumes->horizontalHeader()->setSectionResizeMode(QHeaderView::Stretch);
    auto* addVolume = new QPushButton(QStringLiteral("Add locked measured volume"), hardware);
    addVolume->setObjectName(QStringLiteral("physical_add_locked_volume"));
    connect(addVolume, &QPushButton::clicked, this, [this] {
        const int row = m_volumes->rowCount(); m_volumes->insertRow(row);
        const QStringList values{m_volumeKind->currentText(), QStringLiteral("measured-object-%1").arg(row + 1),
            QStringLiteral("0"), QStringLiteral("0"), QStringLiteral("0"),
            QStringLiteral("10"), QStringLiteral("10"), QStringLiteral("10")};
        for (int column = 0; column < values.size(); ++column)
            m_volumes->setItem(row, column, new QTableWidgetItem(values[column]));
    });
    qobject_cast<QVBoxLayout*>(hardware->layout())->addWidget(m_volumeKind);
    qobject_cast<QVBoxLayout*>(hardware->layout())->addWidget(addVolume);
    qobject_cast<QVBoxLayout*>(hardware->layout())->addWidget(m_volumes, 1);

    QWidget* intent = page(QStringLiteral("5 Intent & Process"),
        QStringLiteral("Capture desire priorities, material, manufacturing process, minimum 2 mm wall, envelope and hand objectives."),
        m_steps, QStringLiteral("physical_step_intent_process"));
    auto* intentForm = new QFormLayout;
    m_desire = new QLineEdit(intent); m_desire->setObjectName(QStringLiteral("physical_desire"));
    m_desire->setPlaceholderText(QStringLiteral("Comfort, serviceability, visual character…"));
    m_material = new QLineEdit(QStringLiteral("PETG"), intent); m_material->setObjectName(QStringLiteral("physical_material"));
    m_process = new QComboBox(intent); m_process->setObjectName(QStringLiteral("physical_process"));
    m_process->addItems({QStringLiteral("FDM"), QStringLiteral("SLA"), QStringLiteral("CNC"), QStringLiteral("Injection moulding")});
    auto* wall = new QDoubleSpinBox(intent); wall->setObjectName(QStringLiteral("physical_wall_mm"));
    wall->setRange(2.0, 20.0); wall->setValue(2.0); wall->setSuffix(QStringLiteral(" mm"));
    auto* envelope = new QLineEdit(QStringLiteral("160 × 106 × 66 mm"), intent);
    envelope->setObjectName(QStringLiteral("physical_locked_envelope"));
    intentForm->addRow(QStringLiteral("Desired outcome"), m_desire);
    intentForm->addRow(QStringLiteral("Material"), m_material);
    intentForm->addRow(QStringLiteral("Process"), m_process);
    intentForm->addRow(QStringLiteral("Minimum wall"), wall);
    intentForm->addRow(QStringLiteral("Locked envelope"), envelope);
    qobject_cast<QVBoxLayout*>(intent->layout())->addLayout(intentForm);
    auto* createSession = new QPushButton(QStringLiteral("Create guided physical-design session"), intent);
    createSession->setObjectName(QStringLiteral("physical_create_guided_session"));
    connect(createSession, &QPushButton::clicked, this, &PhysicalDesignPanel::createGuidedSession);
    qobject_cast<QVBoxLayout*>(intent->layout())->addWidget(createSession);

    QWidget* comparison = page(QStringLiteral("6 Candidates"),
        QStringLiteral("Generate the three labels declared by the physical-design session. Hard failures reject; incomplete gates are visible and never score."),
        m_steps, QStringLiteral("physical_step_candidates"));
    m_sessionPath = new QLineEdit(comparison);
    m_sessionPath->setObjectName(QStringLiteral("physical_session_path"));
    m_sessionPath->setPlaceholderText(QStringLiteral("contracts/physical-design-session-…json"));
    auto* generate = new QPushButton(QStringLiteral("Generate and compare three editable candidates"), comparison);
    generate->setObjectName(QStringLiteral("physical_generate_candidates"));
    m_candidateTable = new QTableWidget(3, 7, comparison);
    m_candidateTable->setObjectName(QStringLiteral("physical_candidate_comparison"));
    m_candidateTable->setHorizontalHeaderLabels({QStringLiteral("Candidate"), QStringLiteral("Hard gates"),
        QStringLiteral("Reach"), QStringLiteral("Wall"), QStringLiteral("Manufacturing"),
        QStringLiteral("Mass/material"), QStringLiteral("Ranking")});
    for (int row = 0; row < 3; ++row) {
        m_candidateTable->setItem(row, 0, new QTableWidgetItem(QStringList{QStringLiteral("compact"), QStringLiteral("balanced"), QStringLiteral("comfort")}[row]));
        m_candidateTable->setItem(row, 1, new QTableWidgetItem(QStringLiteral("pending")));
        for (int col = 2; col < 6; ++col) m_candidateTable->setItem(row, col, new QTableWidgetItem(QStringLiteral("incomplete")));
        m_candidateTable->setItem(row, 6, new QTableWidgetItem(QStringLiteral("provisional")));
    }
    m_candidateTable->horizontalHeader()->setSectionResizeMode(QHeaderView::Stretch);
    connect(generate, &QPushButton::clicked, this, [this] {
        emit operationRequested(QStringLiteral("generate_constraint_candidates"),
            QJsonObject{{QStringLiteral("session_path"), m_sessionPath->text().trimmed()}});
    });
    qobject_cast<QVBoxLayout*>(comparison->layout())->addWidget(m_sessionPath);
    qobject_cast<QVBoxLayout*>(comparison->layout())->addWidget(generate);
    qobject_cast<QVBoxLayout*>(comparison->layout())->addWidget(m_candidateTable, 1);

    QWidget* testing = page(QStringLiteral("7 Grip Testing"),
        QStringLiteral("Export grip-only B-Rep bucks, then enter real anonymous small/medium/large adult observations on the fixed 1–5 form."),
        m_steps, QStringLiteral("physical_step_grip_testing"));
    auto* testForm = new QFormLayout;
    m_planId = new QLineEdit(QStringLiteral("controller-grip-test"), testing); m_planId->setObjectName(QStringLiteral("physical_plan_id"));
    m_planPath = new QLineEdit(testing); m_planPath->setObjectName(QStringLiteral("physical_plan_path"));
    m_planPath->setPlaceholderText(QStringLiteral("physical-validation/…/physical-test-plan.json"));
    m_participantId = new QLineEdit(testing); m_participantId->setObjectName(QStringLiteral("physical_participant_id"));
    m_handGroup = new QComboBox(testing); m_handGroup->setObjectName(QStringLiteral("physical_hand_group"));
    m_handGroup->addItems({QStringLiteral("small"), QStringLiteral("medium"), QStringLiteral("large")});
    m_handLength = new QDoubleSpinBox(testing); m_handLength->setRange(1, 400); m_handLength->setValue(175);
    m_handLength->setObjectName(QStringLiteral("physical_hand_length_mm"));
    m_handBreadth = new QDoubleSpinBox(testing); m_handBreadth->setRange(1, 250); m_handBreadth->setValue(82);
    m_handBreadth->setObjectName(QStringLiteral("physical_hand_breadth_mm"));
    m_candidate = new QComboBox(testing); m_candidate->setObjectName(QStringLiteral("physical_observation_candidate"));
    m_candidate->addItems({QStringLiteral("compact"), QStringLiteral("balanced"), QStringLiteral("comfort")});
    m_testOrder = new QSpinBox(testing); m_testOrder->setRange(1, 3); m_testOrder->setObjectName(QStringLiteral("physical_test_order"));
    testForm->addRow(QStringLiteral("Plan ID"), m_planId); testForm->addRow(QStringLiteral("Plan path"), m_planPath);
    testForm->addRow(QStringLiteral("Anonymous participant"), m_participantId); testForm->addRow(QStringLiteral("Hand group"), m_handGroup);
    testForm->addRow(QStringLiteral("Hand length (mm)"), m_handLength); testForm->addRow(QStringLiteral("Hand breadth (mm)"), m_handBreadth);
    testForm->addRow(QStringLiteral("Candidate"), m_candidate); testForm->addRow(QStringLiteral("Test order"), m_testOrder);
    const QStringList metricLabels{QStringLiteral("Comfort"), QStringLiteral("Finger reach"), QStringLiteral("Pressure points"),
        QStringLiteral("Wrist angle"), QStringLiteral("Slip resistance"), QStringLiteral("Preference")};
    for (const QString& label : metricLabels) { auto* rating = new QSpinBox(testing); rating->setRange(1, 5); rating->setValue(3); m_ratings.append(rating); testForm->addRow(label + QStringLiteral(" (1–5)"), rating); }
    auto* exportButton = new QPushButton(QStringLiteral("Export three grip buck sets"), testing);
    exportButton->setObjectName(QStringLiteral("physical_export_grip_bucks"));
    auto* recordButton = new QPushButton(QStringLiteral("Record physical observation"), testing);
    recordButton->setObjectName(QStringLiteral("physical_record_observation"));
    connect(exportButton, &QPushButton::clicked, this, &PhysicalDesignPanel::exportBucks);
    connect(recordButton, &QPushButton::clicked, this, &PhysicalDesignPanel::recordObservation);
    qobject_cast<QVBoxLayout*>(testing->layout())->addLayout(testForm);
    qobject_cast<QVBoxLayout*>(testing->layout())->addWidget(exportButton);
    qobject_cast<QVBoxLayout*>(testing->layout())->addWidget(recordButton);

    QWidget* redesign = page(QStringLiteral("8 Local Redesign"),
        QStringLiteral("Select one or more faces in the 3D view, describe the change, and let Codex build a verified sibling preview. The baseline remains visible until you apply the edit."),
        m_steps, QStringLiteral("physical_step_local_redesign"));
    auto* redesignForm = new QFormLayout;
    m_faceIntent = new QLineEdit(redesign); m_faceIntent->setObjectName(QStringLiteral("physical_face_intent"));
    m_faceIntent->setPlaceholderText(QStringLiteral("Example: soften this grip surface while preserving the seam and screw bosses"));
    m_faceId = new QLineEdit(QStringLiteral("No faces captured"), redesign); m_faceId->setObjectName(QStringLiteral("physical_face_id"));
    m_faceId->setReadOnly(true);
    m_continuity = new QComboBox(redesign); m_continuity->setObjectName(QStringLiteral("physical_face_continuity"));
    m_continuity->addItems({QStringLiteral("G1"), QStringLiteral("G0"), QStringLiteral("G2")});
    m_expansion = new QDoubleSpinBox(redesign); m_expansion->setObjectName(QStringLiteral("physical_face_expansion_mm"));
    m_expansion->setRange(0, 100); m_expansion->setValue(3);
    redesignForm->addRow(QStringLiteral("Change intent"), m_faceIntent); redesignForm->addRow(QStringLiteral("Captured selection"), m_faceId);
    redesignForm->addRow(QStringLiteral("Continuity"), m_continuity); redesignForm->addRow(QStringLiteral("Maximum expansion (mm)"), m_expansion);
    m_redesignState = new QLabel(
        QStringLiteral("1. Select faces in the 3D view.  2. Describe the edit.  3. Capture the selection."),
        redesign);
    m_redesignState->setObjectName(QStringLiteral("physical_redesign_state"));
    m_redesignState->setWordWrap(true);
    m_captureRedesignButton = new QPushButton(QStringLiteral("1  Capture selected faces"), redesign);
    m_captureRedesignButton->setObjectName(QStringLiteral("physical_capture_face_redesign"));
    m_askCodexRedesignButton = new QPushButton(QStringLiteral("2  Ask Codex to create preview"), redesign);
    m_askCodexRedesignButton->setObjectName(QStringLiteral("physical_ask_codex_redesign"));
    m_applyRedesignButton = new QPushButton(QStringLiteral("Apply"), redesign);
    m_applyRedesignButton->setObjectName(QStringLiteral("physical_apply_redesign"));
    m_modifyRedesignButton = new QPushButton(QStringLiteral("Modify"), redesign);
    m_modifyRedesignButton->setObjectName(QStringLiteral("physical_modify_redesign"));
    m_modifyRedesignButton->setToolTip(QStringLiteral(
        "Edit Change intent above, then click Modify to replace the current preview."));
    m_rejectRedesignButton = new QPushButton(QStringLiteral("Reject"), redesign);
    m_rejectRedesignButton->setObjectName(QStringLiteral("physical_reject_redesign"));
    m_rollbackRedesignButton = new QPushButton(QStringLiteral("Rollback applied edit"), redesign);
    m_rollbackRedesignButton->setObjectName(QStringLiteral("physical_rollback_redesign"));
    connect(m_captureRedesignButton, &QPushButton::clicked,
            this, &PhysicalDesignPanel::requestFaceRedesign);
    connect(m_askCodexRedesignButton, &QPushButton::clicked,
            this, &PhysicalDesignPanel::requestCodexRedesign);
    connect(m_applyRedesignButton, &QPushButton::clicked,
            this, &PhysicalDesignPanel::applyRedesign);
    connect(m_modifyRedesignButton, &QPushButton::clicked,
            this, &PhysicalDesignPanel::modifyRedesign);
    connect(m_rejectRedesignButton, &QPushButton::clicked,
            this, &PhysicalDesignPanel::rejectRedesign);
    connect(m_rollbackRedesignButton, &QPushButton::clicked,
            this, &PhysicalDesignPanel::rollbackRedesign);
    auto* captureRow = new QHBoxLayout;
    captureRow->addWidget(m_captureRedesignButton);
    captureRow->addWidget(m_askCodexRedesignButton);
    auto* reviewRow = new QHBoxLayout;
    reviewRow->addWidget(m_applyRedesignButton);
    reviewRow->addWidget(m_modifyRedesignButton);
    reviewRow->addWidget(m_rejectRedesignButton);
    reviewRow->addWidget(m_rollbackRedesignButton);
    qobject_cast<QVBoxLayout*>(redesign->layout())->addLayout(redesignForm);
    qobject_cast<QVBoxLayout*>(redesign->layout())->addWidget(m_redesignState);
    qobject_cast<QVBoxLayout*>(redesign->layout())->addLayout(captureRow);
    qobject_cast<QVBoxLayout*>(redesign->layout())->addLayout(reviewRow);
    updateRedesignActions();

    QWidget* topology = page(QStringLiteral("9 PCB Topology & Routing"),
        QStringLiteral("Compare one rigid PCB, rigid-flex and separated rigid islands. Rankings use only measured placement/routing evidence; thermal, SI and manufacturing evidence remain incomplete until exact checks run."),
        m_steps, QStringLiteral("physical_step_pcb_topology"));
    m_topologySourcePath = new QLineEdit(topology);
    m_topologySourcePath->setObjectName(QStringLiteral("physical_topology_source_path"));
    m_topologySourcePath->setPlaceholderText(QStringLiteral("electronics/product.dsproj or contracts/topology-input.json"));
    auto* topologyButton = new QPushButton(QStringLiteral("Generate three PCB topology alternatives"), topology);
    topologyButton->setObjectName(QStringLiteral("physical_generate_topology_study"));
    m_topologyCandidates = new QTableWidget(3, 6, topology);
    m_topologyCandidates->setObjectName(QStringLiteral("physical_topology_candidates"));
    m_topologyCandidates->setHorizontalHeaderLabels({QStringLiteral("Topology"), QStringLiteral("Hard gates"),
        QStringLiteral("Connection length"), QStringLiteral("Interconnects"),
        QStringLiteral("Exact physics"), QStringLiteral("Approval")});
    const QStringList topologies{QStringLiteral("single_rigid"), QStringLiteral("rigid_flex"),
                                 QStringLiteral("multi_rigid_harness")};
    for (int row = 0; row < topologies.size(); ++row) {
        m_topologyCandidates->setItem(row, 0, new QTableWidgetItem(topologies[row]));
        m_topologyCandidates->setItem(row, 1, new QTableWidgetItem(QStringLiteral("pending")));
        m_topologyCandidates->setItem(row, 2, new QTableWidgetItem(QStringLiteral("pending")));
        m_topologyCandidates->setItem(row, 3, new QTableWidgetItem(QStringLiteral("pending")));
        m_topologyCandidates->setItem(row, 4, new QTableWidgetItem(QStringLiteral("incomplete")));
        m_topologyCandidates->setItem(row, 5, new QTableWidgetItem(QStringLiteral("user required")));
    }
    m_topologyCandidates->horizontalHeader()->setSectionResizeMode(QHeaderView::Stretch);
    connect(topologyButton, &QPushButton::clicked, this, [this] {
        emit operationRequested(QStringLiteral("create_pcb_topology_study"),
            QJsonObject{{QStringLiteral("source_path"), m_topologySourcePath->text().trimmed()}});
    });
    qobject_cast<QVBoxLayout*>(topology->layout())->addWidget(m_topologySourcePath);
    qobject_cast<QVBoxLayout*>(topology->layout())->addWidget(topologyButton);
    qobject_cast<QVBoxLayout*>(topology->layout())->addWidget(m_topologyCandidates, 1);

    QWidget* mechanics = page(QStringLiteral("10 Mechanical System"),
        QStringLiteral("Derive fasteners, carriers, bearings, thermal hardware, cable retention and custom FreeCAD interfaces from recorded loads, motion, life, temperature and service constraints."),
        m_steps, QStringLiteral("physical_step_mechanical_system"));
    m_mechanicalSourcePath = new QLineEdit(mechanics);
    m_mechanicalSourcePath->setObjectName(QStringLiteral("physical_mechanical_source_path"));
    m_mechanicalSourcePath->setPlaceholderText(QStringLiteral("contracts/physical-product-mechanical-input.json"));
    auto* mechanicsButton = new QPushButton(QStringLiteral("Derive mechanical component requirements"), mechanics);
    mechanicsButton->setObjectName(QStringLiteral("physical_derive_mechanical_requirements"));
    m_mechanicalRequirements = new QTableWidget(0, 5, mechanics);
    m_mechanicalRequirements->setObjectName(QStringLiteral("physical_mechanical_requirements"));
    m_mechanicalRequirements->setHorizontalHeaderLabels({QStringLiteral("Component kind"),
        QStringLiteral("Load/model"), QStringLiteral("Catalog/custom"),
        QStringLiteral("Evidence"), QStringLiteral("Selection")});
    m_mechanicalRequirements->horizontalHeader()->setSectionResizeMode(QHeaderView::Stretch);
    connect(mechanicsButton, &QPushButton::clicked, this, [this] {
        emit operationRequested(QStringLiteral("derive_mechanical_component_requirements"),
            QJsonObject{{QStringLiteral("source_path"), m_mechanicalSourcePath->text().trimmed()}});
    });
    qobject_cast<QVBoxLayout*>(mechanics->layout())->addWidget(m_mechanicalSourcePath);
    qobject_cast<QVBoxLayout*>(mechanics->layout())->addWidget(mechanicsButton);
    qobject_cast<QVBoxLayout*>(mechanics->layout())->addWidget(m_mechanicalRequirements, 1);
}

void PhysicalDesignPanel::chooseEvidence()
{
    const QString path = QFileDialog::getOpenFileName(this, QStringLiteral("Choose physical-design evidence"), {},
        QStringLiteral("Evidence (*.png *.jpg *.jpeg *.webp *.svg *.pdf)"));
    if (path.isEmpty()) return;
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly) || file.size() <= 0 || file.size() > 64 * 1024 * 1024) {
        emit statusMessage(QStringLiteral("Evidence must be a readable file no larger than 64 MiB.")); return;
    }
    const QByteArray digest = QCryptographicHash::hash(file.readAll(), QCryptographicHash::Sha256).toHex();
    m_evidencePath->setText(QFileInfo(path).absoluteFilePath());
    m_evidenceDigest->setText(QString::fromLatin1(digest));
    emit statusMessage(QStringLiteral("Evidence hashed. Calibrate a verified dimension before generation."));
}

void PhysicalDesignPanel::createGuidedSession()
{
    QJsonArray volumes;
    for (int row = 0; row < m_volumes->rowCount(); ++row) {
        auto text = [this, row](int column) {
            const auto* item = m_volumes->item(row, column);
            return item ? item->text().trimmed() : QString{};
        };
        volumes.append(QJsonObject{{QStringLiteral("kind"), text(0)},
            {QStringLiteral("semantic_id"), text(1)},
            {QStringLiteral("min_mm"), point(text(2).toDouble(), text(3).toDouble(), text(4).toDouble())},
            {QStringLiteral("max_mm"), point(text(5).toDouble(), text(6).toDouble(), text(7).toDouble())}});
    }
    const QString sessionId = QStringLiteral("guided-controller-%1")
        .arg(QDateTime::currentSecsSinceEpoch());
    const QJsonObject capture{{QStringLiteral("session_id"), sessionId},
        {QStringLiteral("product_family"), QStringLiteral("controller")},
        {QStringLiteral("workflow_mode"), m_workflowMode->currentData().toString()},
        {QStringLiteral("evidence_path"), m_evidencePath->text()},
        {QStringLiteral("evidence_sha256"), m_evidenceDigest->text()},
        {QStringLiteral("evidence_kind"), m_evidenceKind->currentText()},
        {QStringLiteral("known_length_mm"), m_knownLength->value()},
        {QStringLiteral("pixels_per_mm"), m_pixelsPerMm->value()},
        {QStringLiteral("occupied_volumes"), volumes},
        {QStringLiteral("desire"), m_desire->text().trimmed()},
        {QStringLiteral("material"), m_material->text().trimmed()},
        {QStringLiteral("process"), m_process->currentText()},
        {QStringLiteral("created_utc"), QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs)}};
    emit operationRequested(QStringLiteral("create_guided_physical_design_session"),
        QJsonObject{{QStringLiteral("capture"), capture}});
}

void PhysicalDesignPanel::exportBucks()
{
    const QJsonObject region{{QStringLiteral("method"), QStringLiteral("user_confirmed_section_planes")},
        {QStringLiteral("semantic_ids"), QJsonArray{QStringLiteral("candidate-grip-region")}},
        {QStringLiteral("section_boxes_mm"), QJsonArray{
            QJsonObject{{QStringLiteral("min_mm"), point(0, 0, 0)}, {QStringLiteral("max_mm"), point(48, 106, 66)}},
            QJsonObject{{QStringLiteral("min_mm"), point(112, 0, 0)}, {QStringLiteral("max_mm"), point(160, 106, 66)}}}},
        {QStringLiteral("confirmed_by_user"), true}};
    QJsonObject regions;
    for (const QString& label : {QStringLiteral("compact"), QStringLiteral("balanced"), QStringLiteral("comfort")})
        regions.insert(label, region);
    const QJsonObject request{{QStringLiteral("plan_id"), m_planId->text().trimmed()},
        {QStringLiteral("observer"), QStringLiteral("DesignStudio user")},
        {QStringLiteral("created_utc"), QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs)},
        {QStringLiteral("material"), m_material->text().trimmed()}, {QStringLiteral("synthetic_fixture"), false},
        {QStringLiteral("print_settings"), QJsonObject{{QStringLiteral("process"), QStringLiteral("FDM")},
            {QStringLiteral("nozzle_mm"), 0.4}, {QStringLiteral("layer_height_mm"), 0.2},
            {QStringLiteral("wall_mm"), 2.0}, {QStringLiteral("infill_percent"), 15.0},
            {QStringLiteral("orientation"), QStringLiteral("split-plane-down")}}},
        {QStringLiteral("grip_regions"), regions}};
    emit operationRequested(QStringLiteral("export_physical_test_plan"),
        QJsonObject{{QStringLiteral("session_path"), m_sessionPath->text().trimmed()},
                    {QStringLiteral("request"), request}});
}

void PhysicalDesignPanel::recordObservation()
{
    QJsonObject ratings;
    const QStringList names{QStringLiteral("comfort"), QStringLiteral("finger_reach"), QStringLiteral("pressure_points"),
        QStringLiteral("wrist_angle"), QStringLiteral("slip_resistance"), QStringLiteral("preference")};
    for (int index = 0; index < names.size(); ++index) ratings.insert(names[index], m_ratings[index]->value());
    const QString participant = m_participantId->text().trimmed();
    const QString label = m_candidate->currentText();
    const QString now = QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs);
    const QJsonObject observation{{QStringLiteral("observation_id"), QStringLiteral("obs-%1-%2-%3")
            .arg(participant, label, QString::number(QDateTime::currentSecsSinceEpoch()))},
        {QStringLiteral("candidate_label"), label},
        {QStringLiteral("participant"), QJsonObject{{QStringLiteral("anonymous_id"), participant},
            {QStringLiteral("adult_confirmed"), true}, {QStringLiteral("hand_size_group"), m_handGroup->currentText()},
            {QStringLiteral("hand_length_mm"), m_handLength->value()}, {QStringLiteral("hand_breadth_mm"), m_handBreadth->value()}}},
        {QStringLiteral("test_order"), m_testOrder->value()}, {QStringLiteral("ratings"), ratings},
        {QStringLiteral("tested_at"), now}, {QStringLiteral("observer"), QStringLiteral("DesignStudio user")},
        {QStringLiteral("provenance"), QJsonObject{{QStringLiteral("entered_by"), QStringLiteral("DesignStudio user")},
            {QStringLiteral("entered_utc"), now}, {QStringLiteral("source"), QStringLiteral("guided physical test form")},
            {QStringLiteral("synthetic_fixture"), false}}}};
    emit operationRequested(QStringLiteral("record_physical_observation"),
        QJsonObject{{QStringLiteral("plan_path"), m_planPath->text().trimmed()},
                    {QStringLiteral("observation"), observation}});
}

void PhysicalDesignPanel::requestFaceRedesign()
{
    const QString intent = m_faceIntent->text().trimmed();
    if (intent.isEmpty()) {
        m_redesignState->setText(QStringLiteral(
            "Describe what should change before capturing the selected faces."));
        m_faceIntent->setFocus();
        emit statusMessage(QStringLiteral("A local redesign intent is required."));
        return;
    }
    m_redesignBusy = true;
    m_requestCodexAfterDiscard = false;
    m_redesignState->setText(QStringLiteral(
        "Waiting for approval to capture the active FreeCAD face selection…"));
    updateRedesignActions();
    emit operationRequested(QStringLiteral("capture_local_redesign_v2"), QJsonObject{
        {QStringLiteral("intent"), intent},
        {QStringLiteral("permitted_expansion_mm"), m_expansion->value()},
        {QStringLiteral("continuity_required"), m_continuity->currentText()},
        {QStringLiteral("protected_objects"), QJsonArray{}},
        {QStringLiteral("manufacturing"), QJsonObject{{QStringLiteral("process"), QStringLiteral("fdm")},
            {QStringLiteral("minimum_wall_mm"), 2.0},
            {QStringLiteral("minimum_blend_radius_mm"), 1.2},
            {QStringLiteral("max_overhang_deg"), 45.0}}}});
}

void PhysicalDesignPanel::requestCodexRedesign()
{
    if (m_redesignPath.isEmpty()) {
        m_redesignState->setText(QStringLiteral(
            "Capture the selected faces before asking Codex to create a preview."));
        return;
    }
    const QString prompt = QStringLiteral(
        "Create one collaborative local CAD edit for the captured DesignStudio "
        "local-redesign/2 contract at `%1`. The user intent is: %2\n\n"
        "Read the captured contract, preserve its exact selected FaceN scope and constraints, "
        "author a typed design-studio.mechanical-cad-program/2 candidate, and call "
        "preview_local_redesign_v2 with its candidate command ID and candidate FaceN IDs. "
        "Do not commit the edit. Stop after the verified sibling preview so the user can "
        "choose Apply, Modify, or Reject in Physical Design.")
        .arg(m_redesignPath, m_faceIntent->text().trimmed());
    m_redesignState->setText(QStringLiteral(
        "Codex is preparing a bounded edit. The baseline remains unchanged until Apply."));
    emit codexPromptRequested(prompt);
    emit statusMessage(QStringLiteral("Local redesign request sent to Codex."));
}

void PhysicalDesignPanel::applyRedesign()
{
    if (m_previewReceiptPath.isEmpty()) return;
    m_redesignBusy = true;
    m_redesignState->setText(QStringLiteral(
        "Waiting for approval to apply the verified preview…"));
    updateRedesignActions();
    emit operationRequested(QStringLiteral("commit_local_redesign_v2"),
        QJsonObject{{QStringLiteral("preview_receipt_path"), m_previewReceiptPath}});
}

void PhysicalDesignPanel::modifyRedesign()
{
    if (m_previewReceiptPath.isEmpty()) {
        requestCodexRedesign();
        return;
    }
    if (m_faceIntent->text().trimmed() == m_previewIntent) {
        m_redesignState->setText(QStringLiteral(
            "Describe how the preview should change in Change intent, then click Modify again."));
        m_faceIntent->setFocus();
        m_faceIntent->selectAll();
        return;
    }
    m_requestCodexAfterDiscard = true;
    rejectRedesign();
}

void PhysicalDesignPanel::rejectRedesign()
{
    if (m_previewReceiptPath.isEmpty()) return;
    m_redesignBusy = true;
    m_redesignState->setText(QStringLiteral(
        "Waiting for approval to discard the uncommitted preview…"));
    updateRedesignActions();
    emit operationRequested(QStringLiteral("discard_local_redesign_v2"),
        QJsonObject{{QStringLiteral("preview_receipt_path"), m_previewReceiptPath}});
}

void PhysicalDesignPanel::rollbackRedesign()
{
    if (m_commitReceiptPath.isEmpty()) return;
    m_redesignBusy = true;
    m_redesignState->setText(QStringLiteral(
        "Waiting for approval to restore the retained baseline…"));
    updateRedesignActions();
    emit operationRequested(QStringLiteral("rollback_local_redesign_v2"),
        QJsonObject{{QStringLiteral("commit_receipt_path"), m_commitReceiptPath}});
}

void PhysicalDesignPanel::updateRedesignActions()
{
    if (!m_captureRedesignButton) return;
    const bool hasCapture = !m_redesignPath.isEmpty();
    const bool hasPreview = !m_previewReceiptPath.isEmpty()
        && m_commitReceiptPath.isEmpty();
    const bool hasCommit = !m_commitReceiptPath.isEmpty();
    m_captureRedesignButton->setEnabled(!m_redesignBusy && !hasPreview);
    m_askCodexRedesignButton->setEnabled(!m_redesignBusy && hasCapture
                                         && !hasPreview && !hasCommit);
    m_applyRedesignButton->setEnabled(!m_redesignBusy && hasPreview);
    m_modifyRedesignButton->setEnabled(!m_redesignBusy && hasPreview);
    m_rejectRedesignButton->setEnabled(!m_redesignBusy && hasPreview);
    m_rollbackRedesignButton->setEnabled(!m_redesignBusy && hasCommit);
}

void PhysicalDesignPanel::setOperationResult(const QString& operation, bool ok,
                                             const QJsonObject& result)
{
    if (operation == QStringLiteral("generate_constraint_candidates")) {
        if (!m_candidateTable) return;
        if (!ok) {
            for (int row = 0; row < m_candidateTable->rowCount(); ++row) {
                m_candidateTable->setItem(row, 1, new QTableWidgetItem(QStringLiteral("generation failed")));
                m_candidateTable->setItem(row, 6, new QTableWidgetItem(QStringLiteral("not ranked")));
            }
            return;
        }
        const QJsonArray candidates = operationPayload(result).value(QStringLiteral("candidates")).toArray();
        if (candidates.size() != 3) {
            for (int row = 0; row < m_candidateTable->rowCount(); ++row)
                m_candidateTable->setItem(row, 1, new QTableWidgetItem(QStringLiteral("invalid result")));
            return;
        }
        m_candidateTable->setRowCount(candidates.size());
        m_candidate->clear();
        for (int row = 0; row < candidates.size(); ++row) {
            const QJsonObject candidate = candidates.at(row).toObject();
            const QString label = candidate.value(QStringLiteral("label")).toString();
            const QString status = candidate.value(QStringLiteral("status")).toString();
            const QJsonObject analysis = candidate.value(QStringLiteral("analysis")).toObject();
            const QJsonArray failures = candidate.value(QStringLiteral("hard_failures")).toArray();
            const auto analysisStatus = [&analysis](const QString& name) {
                return analysis.value(name).toObject().value(QStringLiteral("status"))
                    .toString(QStringLiteral("incomplete"));
            };
            m_candidateTable->setItem(row, 0, new QTableWidgetItem(label));
            m_candidateTable->setItem(row, 1, new QTableWidgetItem(
                failures.isEmpty() ? status : QStringLiteral("rejected (%1)").arg(failures.size())));
            m_candidateTable->setItem(row, 2, new QTableWidgetItem(analysisStatus(QStringLiteral("reach"))));
            m_candidateTable->setItem(row, 3, new QTableWidgetItem(analysisStatus(QStringLiteral("wall"))));
            m_candidateTable->setItem(row, 4, new QTableWidgetItem(analysisStatus(QStringLiteral("manufacturability"))));
            const QString mass = analysisStatus(QStringLiteral("mass"));
            const QString material = analysisStatus(QStringLiteral("material_use"));
            m_candidateTable->setItem(row, 5, new QTableWidgetItem(
                mass == material ? mass : mass + QStringLiteral(" / ") + material));
            const int rank = candidate.value(QStringLiteral("rank")).toInt(0);
            m_candidateTable->setItem(row, 6, new QTableWidgetItem(
                rank > 0 ? QStringLiteral("provisional #%1").arg(rank)
                         : QStringLiteral("not ranked")));
            if (!label.isEmpty()) m_candidate->addItem(label);
        }
        return;
    }
    static const QSet<QString> redesignOperations{
        QStringLiteral("capture_local_redesign_v2"),
        QStringLiteral("preview_local_redesign_v2"),
        QStringLiteral("commit_local_redesign_v2"),
        QStringLiteral("discard_local_redesign_v2"),
        QStringLiteral("rollback_local_redesign_v2")};
    if (!redesignOperations.contains(operation)) return;
    m_redesignBusy = false;
    const QString message = result.value(QStringLiteral("message")).toString();
    if (!ok) {
        m_requestCodexAfterDiscard = false;
        m_redesignState->setText(message.isEmpty()
            ? QStringLiteral("The collaborative edit operation failed; the baseline was not changed.")
            : message);
        updateRedesignActions();
        return;
    }

    const QJsonObject payload = operationPayload(result);
    if (operation == QStringLiteral("capture_local_redesign_v2")) {
        m_redesignPath = payload.value(QStringLiteral("path")).toString();
        m_previewReceiptPath.clear();
        m_commitReceiptPath.clear();
        m_previewIntent.clear();
        const QString faces = selectedFacesText(payload);
        m_faceId->setText(faces.isEmpty() ? QStringLiteral("Captured FaceN selection") : faces);
        m_redesignState->setText(QStringLiteral(
            "Selection captured and digest-bound. Ask Codex to create a visual sibling preview."));
    } else if (operation == QStringLiteral("preview_local_redesign_v2")) {
        m_previewReceiptPath = payload.value(QStringLiteral("receipt_path")).toString();
        m_commitReceiptPath.clear();
        m_previewIntent = m_faceIntent->text().trimmed();
        m_redesignState->setText(QStringLiteral(
            "Preview verified. Inspect the translucent candidate from every angle, then choose Apply, Modify, or Reject."));
    } else if (operation == QStringLiteral("commit_local_redesign_v2")) {
        m_commitReceiptPath = payload.value(QStringLiteral("receipt_path")).toString();
        m_redesignState->setText(QStringLiteral(
            "Edit applied. The original body is retained and can be restored with Rollback."));
    } else if (operation == QStringLiteral("discard_local_redesign_v2")) {
        m_previewReceiptPath.clear();
        m_commitReceiptPath.clear();
        m_previewIntent.clear();
        const bool askAgain = m_requestCodexAfterDiscard;
        m_requestCodexAfterDiscard = false;
        m_redesignState->setText(askAgain
            ? QStringLiteral("Previous preview removed. Codex will create a new preview from the revised intent.")
            : QStringLiteral("Preview rejected. The baseline remains unchanged."));
        updateRedesignActions();
        if (askAgain) requestCodexRedesign();
        return;
    } else if (operation == QStringLiteral("rollback_local_redesign_v2")) {
        m_redesignPath.clear();
        m_previewReceiptPath.clear();
        m_commitReceiptPath.clear();
        m_previewIntent.clear();
        m_faceId->setText(QStringLiteral("No faces captured"));
        m_redesignState->setText(QStringLiteral(
            "Rollback complete. The retained baseline is active again."));
    }
    updateRedesignActions();
}

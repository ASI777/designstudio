#include "EnclosureConceptTab.h"

#include "GlbMeshReader.h"
#include "InteractivePlacementModel.h"
#include "MeshProjectionView.h"
#include "StepMeshReader.h"
#include "designcore/balancing_sphere.h"

#include <QCheckBox>
#include <QComboBox>
#include <QCryptographicHash>
#include <QDateTime>
#include <QDoubleSpinBox>
#include <QDialog>
#include <QDialogButtonBox>
#include <QFileDialog>
#include <QFileInfo>
#include <QFormLayout>
#include <QGroupBox>
#include <QHeaderView>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QMessageBox>
#include <QPushButton>
#include <QRegularExpression>
#include <QSaveFile>
#include <QSignalBlocker>
#include <QSplitter>
#include <QTableWidget>
#include <QVBoxLayout>
#include <QUuid>
#include <QtEndian>

#include <algorithm>
#include <cmath>
#include <limits>

namespace designstudio {
namespace {

QJsonArray vectorJson(const QVector3D& value)
{
    return {double(value.x()), double(value.y()), double(value.z())};
}

QString compactNumber(double value)
{
    return QString::number(value, 'f', 3);
}

} // namespace

EnclosureConceptTab::EnclosureConceptTab(QWidget* parent) : QWidget(parent)
{
    auto* root = new QHBoxLayout(this);
    root->setContentsMargins(8, 8, 8, 8);
    auto* splitter = new QSplitter(Qt::Horizontal, this);
    root->addWidget(splitter);

    projectionView_ = new MeshProjectionView(splitter);
    splitter->addWidget(projectionView_);

    auto* controls = new QWidget(splitter);
    controls->setMinimumWidth(320);
    controls->setMaximumWidth(430);
    auto* controlsLayout = new QVBoxLayout(controls);

    modeLabel_ = new QLabel(
        QStringLiteral("GLB concept route · no PDF tracing\n"
                       "Verified mesh evidence only — not yet manufacturing B-Rep."), controls);
    modeLabel_->setWordWrap(true);
    modeLabel_->setStyleSheet(QStringLiteral(
        "background:#1f2937;color:#c9d1d9;border:1px solid #3b82f6;"
        "padding:8px;border-radius:4px;"));
    controlsLayout->addWidget(modeLabel_);

    auto* loadButton = new QPushButton(QStringLiteral("Load GLB concept…"), controls);
    controlsLayout->addWidget(loadButton);
    auto* loadStepButton = new QPushButton(
        QStringLiteral("Place interactive STEP component…"), controls);
    loadStepButton->setToolTip(QStringLiteral(
        "Load a validated STEP solid, then set its exact 6-DOF product placement."));
    controlsLayout->addWidget(loadStepButton);
    auto* sphereButton = new QPushButton(
        QStringLiteral("Generate self-balancing sphere STEP…"), controls);
    sphereButton->setToolTip(QStringLiteral(
        "Create a validated OCCT B-Rep assembly, individual part STEP files, "
        "mechatronic project and controller response."));
    controlsLayout->addWidget(sphereButton);

    auto* viewGroup = new QGroupBox(QStringLiteral("View and confirmed scale"), controls);
    auto* viewForm = new QFormLayout(viewGroup);
    projectionCombo_ = new QComboBox(viewGroup);
    projectionCombo_->addItems({QStringLiteral("Isometric"), QStringLiteral("Front"),
                                QStringLiteral("Right"), QStringLiteral("Top")});
    targetWidthSpin_ = new QDoubleSpinBox(viewGroup);
    targetWidthSpin_->setRange(0.001, 100000.0);
    targetWidthSpin_->setDecimals(3);
    targetWidthSpin_->setSuffix(QStringLiteral(" mm"));
    targetWidthSpin_->setEnabled(false);
    viewForm->addRow(QStringLiteral("Projection"), projectionCombo_);
    viewForm->addRow(QStringLiteral("Overall X width"), targetWidthSpin_);
    controlsLayout->addWidget(viewGroup);

    auto* placementGroup = new QGroupBox(
        QStringLiteral("Interactive component placement"), controls);
    auto* placementForm = new QFormLayout(placementGroup);
    coordinateSystemCombo_ = new QComboBox(placementGroup);
    coordinateSystemCombo_->addItem(QStringLiteral("World"), QStringLiteral("world"));
    coordinateSystemCombo_->addItem(
        QStringLiteral("Enclosure local"), QStringLiteral("enclosure_local"));
    coordinateSystemCombo_->addItem(QStringLiteral("PCB local"), QStringLiteral("pcb_local"));
    coordinateSystemCombo_->addItem(
        QStringLiteral("Surface local"), QStringLiteral("surface_local"));
    placementForm->addRow(QStringLiteral("Coordinates"), coordinateSystemCombo_);

    const QString axisNames[] = {QStringLiteral("X"), QStringLiteral("Y"), QStringLiteral("Z")};
    for (int axis = 0; axis < 3; ++axis) {
        translationSpins_[axis] = new QDoubleSpinBox(placementGroup);
        translationSpins_[axis]->setRange(-1.0e6, 1.0e6);
        translationSpins_[axis]->setDecimals(3);
        translationSpins_[axis]->setSuffix(QStringLiteral(" mm"));
        placementForm->addRow(axisNames[axis], translationSpins_[axis]);
    }
    const QString rotationNames[] = {
        QStringLiteral("Roll X"), QStringLiteral("Pitch Y"), QStringLiteral("Yaw Z")};
    for (int axis = 0; axis < 3; ++axis) {
        rotationSpins_[axis] = new QDoubleSpinBox(placementGroup);
        rotationSpins_[axis]->setRange(-3600.0, 3600.0);
        rotationSpins_[axis]->setDecimals(2);
        rotationSpins_[axis]->setSuffix(QStringLiteral("°"));
        placementForm->addRow(rotationNames[axis], rotationSpins_[axis]);
    }
    clearanceSpin_ = new QDoubleSpinBox(placementGroup);
    clearanceSpin_->setRange(0.0, 1000.0);
    clearanceSpin_->setDecimals(2);
    clearanceSpin_->setValue(1.0);
    clearanceSpin_->setSuffix(QStringLiteral(" mm"));
    placementForm->addRow(QStringLiteral("Body clearance"), clearanceSpin_);

    auto* snapRow = new QWidget(placementGroup);
    auto* snapLayout = new QHBoxLayout(snapRow);
    snapLayout->setContentsMargins(0, 0, 0, 0);
    gridSnapCheck_ = new QCheckBox(QStringLiteral("Grid"), snapRow);
    surfaceSnapCheck_ = new QCheckBox(QStringLiteral("Surface"), snapRow);
    axisSnapCheck_ = new QCheckBox(QStringLiteral("Axis"), snapRow);
    symmetrySnapCheck_ = new QCheckBox(QStringLiteral("Symmetry"), snapRow);
    clearanceSnapCheck_ = new QCheckBox(QStringLiteral("Clearance"), snapRow);
    for (QCheckBox* check : {gridSnapCheck_, surfaceSnapCheck_, axisSnapCheck_,
                             symmetrySnapCheck_, clearanceSnapCheck_})
        snapLayout->addWidget(check);
    placementForm->addRow(QStringLiteral("Snap"), snapRow);

    auto* lockRow = new QWidget(placementGroup);
    auto* lockLayout = new QHBoxLayout(lockRow);
    lockLayout->setContentsMargins(0, 0, 0, 0);
    positionLockCheck_ = new QCheckBox(QStringLiteral("Position"), lockRow);
    orientationLockCheck_ = new QCheckBox(QStringLiteral("Orientation"), lockRow);
    surfaceLockCheck_ = new QCheckBox(QStringLiteral("Surface"), lockRow);
    surfaceSnapCheck_->setEnabled(false);
    surfaceLockCheck_->setEnabled(false);
    surfaceSnapCheck_->setToolTip(QStringLiteral(
        "Reserved for a selected enclosure face with a stable face identifier."));
    surfaceLockCheck_->setToolTip(QStringLiteral(
        "Reserved for a selected enclosure face with a stable face identifier."));
    lockLayout->addWidget(positionLockCheck_);
    lockLayout->addWidget(orientationLockCheck_);
    lockLayout->addWidget(surfaceLockCheck_);
    placementForm->addRow(QStringLiteral("Locks"), lockRow);

    placementExportButton_ = new QPushButton(
        QStringLiteral("Export locked placement…"), placementGroup);
    placementExportButton_->setEnabled(false);
    placementForm->addRow(placementExportButton_);
    supportExportButton_ = new QPushButton(
        QStringLiteral("Export cavity/rib specification…"), placementGroup);
    supportExportButton_->setEnabled(false);
    supportExportButton_->setToolTip(QStringLiteral(
        "Create a revision-bound local/cloud support input from this locked placement."));
    placementForm->addRow(supportExportButton_);
    placementGroup->setEnabled(false);
    placementGroup->setObjectName(QStringLiteral("interactive_component_placement"));
    controlsLayout->addWidget(placementGroup);

    auto* intentGroup = new QGroupBox(QStringLiteral("Engineering intent"), controls);
    auto* intentForm = new QFormLayout(intentGroup);
    processCombo_ = new QComboBox(intentGroup);
    processCombo_->addItem(QStringLiteral("Unspecified"), QStringLiteral("unspecified"));
    processCombo_->addItem(QStringLiteral("CNC aluminium"), QStringLiteral("cnc_aluminum"));
    processCombo_->addItem(QStringLiteral("Sheet metal"), QStringLiteral("sheet_metal"));
    processCombo_->addItem(QStringLiteral("Die cast metal"), QStringLiteral("die_cast"));
    processCombo_->addItem(QStringLiteral("Metal additive"), QStringLiteral("metal_additive"));
    wallThicknessSpin_ = new QDoubleSpinBox(intentGroup);
    wallThicknessSpin_->setRange(0.1, 50.0);
    wallThicknessSpin_->setDecimals(2);
    wallThicknessSpin_->setValue(2.0);
    wallThicknessSpin_->setSuffix(QStringLiteral(" mm"));
    markerRoleCombo_ = new QComboBox(intentGroup);
    markerRoleCombo_->addItem(QStringLiteral("Datum"), QStringLiteral("datum"));
    markerRoleCombo_->addItem(QStringLiteral("Load point"), QStringLiteral("load_point"));
    markerRoleCombo_->addItem(QStringLiteral("Fixed support"), QStringLiteral("fixed_support"));
    markerRoleCombo_->addItem(QStringLiteral("Split line"), QStringLiteral("split_line"));
    markerRoleCombo_->addItem(QStringLiteral("Preserve surface"), QStringLiteral("preserve_surface"));
    markerRoleCombo_->addItem(QStringLiteral("Opening"), QStringLiteral("opening"));
    markerRoleCombo_->addItem(QStringLiteral("PCB mount"), QStringLiteral("pcb_mount"));
    markerRoleCombo_->addItem(QStringLiteral("Connector support"), QStringLiteral("connector_support"));
    forceSpin_ = new QDoubleSpinBox(intentGroup);
    forceSpin_->setRange(0.0, 1.0e7);
    forceSpin_->setDecimals(2);
    forceSpin_->setValue(100.0);
    forceSpin_->setSuffix(QStringLiteral(" N"));
    forceDirectionCombo_ = new QComboBox(intentGroup);
    forceDirectionCombo_->addItem(QStringLiteral("+X"), QVariant::fromValue(QVector3D(1, 0, 0)));
    forceDirectionCombo_->addItem(QStringLiteral("−X"), QVariant::fromValue(QVector3D(-1, 0, 0)));
    forceDirectionCombo_->addItem(QStringLiteral("+Y"), QVariant::fromValue(QVector3D(0, 1, 0)));
    forceDirectionCombo_->addItem(QStringLiteral("−Y"), QVariant::fromValue(QVector3D(0, -1, 0)));
    forceDirectionCombo_->addItem(QStringLiteral("+Z"), QVariant::fromValue(QVector3D(0, 0, 1)));
    forceDirectionCombo_->addItem(QStringLiteral("−Z"), QVariant::fromValue(QVector3D(0, 0, -1)));
    reinforcementCombo_ = new QComboBox(intentGroup);
    reinforcementCombo_->addItem(QStringLiteral("Derive from load cases"), QStringLiteral("derive_from_load_cases"));
    reinforcementCombo_->addItem(QStringLiteral("Ribs and gussets"), QStringLiteral("ribs_and_gussets"));
    reinforcementCombo_->addItem(QStringLiteral("Internal frame"), QStringLiteral("internal_frame"));
    reinforcementCombo_->addItem(QStringLiteral("Bosses / local doublers"), QStringLiteral("local_doublers"));
    safetyFactorSpin_ = new QDoubleSpinBox(intentGroup);
    safetyFactorSpin_->setRange(1.0, 10.0);
    safetyFactorSpin_->setDecimals(2);
    safetyFactorSpin_->setValue(2.0);
    intentForm->addRow(QStringLiteral("Process"), processCombo_);
    intentForm->addRow(QStringLiteral("Nominal wall"), wallThicknessSpin_);
    intentForm->addRow(QStringLiteral("Click marker role"), markerRoleCombo_);
    intentForm->addRow(QStringLiteral("Load magnitude"), forceSpin_);
    intentForm->addRow(QStringLiteral("Load direction"), forceDirectionCombo_);
    intentForm->addRow(QStringLiteral("Reinforcement"), reinforcementCombo_);
    intentForm->addRow(QStringLiteral("Target safety factor"), safetyFactorSpin_);
    controlsLayout->addWidget(intentGroup);

    summaryLabel_ = new QLabel(QStringLiteral("No GLB loaded."), controls);
    summaryLabel_->setWordWrap(true);
    summaryLabel_->setTextInteractionFlags(Qt::TextSelectableByMouse);
    controlsLayout->addWidget(summaryLabel_);

    markerTable_ = new QTableWidget(0, 3, controls);
    markerTable_->setHorizontalHeaderLabels(
        {QStringLiteral("Role"), QStringLiteral("Vertex"), QStringLiteral("Position mm")});
    markerTable_->horizontalHeader()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    markerTable_->horizontalHeader()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    markerTable_->horizontalHeader()->setSectionResizeMode(2, QHeaderView::Stretch);
    markerTable_->setSelectionBehavior(QAbstractItemView::SelectRows);
    markerTable_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    controlsLayout->addWidget(markerTable_, 1);

    auto* buttonRow = new QHBoxLayout;
    clearButton_ = new QPushButton(QStringLiteral("Clear markers"), controls);
    exportButton_ = new QPushButton(QStringLiteral("Export evidence…"), controls);
    clearButton_->setEnabled(false);
    exportButton_->setEnabled(false);
    buttonRow->addWidget(clearButton_);
    buttonRow->addWidget(exportButton_);
    controlsLayout->addLayout(buttonRow);
    splitter->addWidget(controls);
    splitter->setStretchFactor(0, 1);

    connect(loadButton, &QPushButton::clicked, this, &EnclosureConceptTab::chooseGlb);
    connect(loadStepButton, &QPushButton::clicked, this,
            &EnclosureConceptTab::chooseStepComponent);
    connect(sphereButton, &QPushButton::clicked, this,
            &EnclosureConceptTab::configureBalancingSphereDemo);
    connect(exportButton_, &QPushButton::clicked, this,
            &EnclosureConceptTab::chooseEvidenceDestination);
    connect(clearButton_, &QPushButton::clicked, this, &EnclosureConceptTab::clearMarkers);
    connect(placementExportButton_, &QPushButton::clicked, this,
            &EnclosureConceptTab::choosePlacementDestination);
    connect(supportExportButton_, &QPushButton::clicked, this,
            &EnclosureConceptTab::chooseSupportDestination);
    connect(targetWidthSpin_, &QDoubleSpinBox::valueChanged, this, [this] {
        scaleConfirmed_ = true;
        updateScale();
    });
    connect(projectionCombo_, &QComboBox::currentIndexChanged, this, [this](int index) {
        const MeshProjectionView::Projection projections[] = {
            MeshProjectionView::Projection::Isometric,
            MeshProjectionView::Projection::Front,
            MeshProjectionView::Projection::Right,
            MeshProjectionView::Projection::Top,
        };
        projectionView_->setProjection(projections[std::clamp(index, 0, 3)]);
    });
    connect(projectionView_, &MeshProjectionView::vertexPicked,
            this, &EnclosureConceptTab::addMarker);
    auto placementChanged = [this] { applyPlacementControls(); };
    for (QDoubleSpinBox* spin : translationSpins_)
        connect(spin, &QDoubleSpinBox::valueChanged, this, placementChanged);
    for (QDoubleSpinBox* spin : rotationSpins_)
        connect(spin, &QDoubleSpinBox::valueChanged, this, placementChanged);
    connect(clearanceSpin_, &QDoubleSpinBox::valueChanged, this, placementChanged);
    connect(coordinateSystemCombo_, &QComboBox::currentIndexChanged, this,
            [placementChanged](int) { placementChanged(); });
    for (QCheckBox* check : {gridSnapCheck_, surfaceSnapCheck_, axisSnapCheck_,
                             symmetrySnapCheck_, clearanceSnapCheck_,
                             positionLockCheck_, orientationLockCheck_, surfaceLockCheck_})
        connect(check, &QCheckBox::toggled, this, placementChanged);
    auto updateLoadControls = [this] {
        const bool isLoad = markerRoleCombo_->currentData().toString()
            == QStringLiteral("load_point");
        forceSpin_->setEnabled(isLoad);
        forceDirectionCombo_->setEnabled(isLoad);
    };
    connect(markerRoleCombo_, &QComboBox::currentIndexChanged, this,
            [updateLoadControls](int) { updateLoadControls(); });
    updateLoadControls();
}

bool EnclosureConceptTab::loadStepComponent(const QString& path, QString* errorMessage)
{
    const StepMeshLoadResult result = StepMeshReader::loadFile(path);
    if (!result.ok()) {
        if (errorMessage) *errorMessage = result.error;
        return false;
    }
    mesh_ = result.mesh;
    sourcePath_ = path;
    markers_.clear();
    scaleConfirmed_ = true;
    projectionView_->setUpAxis(MeshProjectionView::UpAxis::Z);
    projectionView_->setMesh(mesh_);
    QString componentId = QFileInfo(path).completeBaseName();
    componentId.replace(QRegularExpression(QStringLiteral("[^A-Za-z0-9._:-]")),
                        QStringLiteral("_"));
    if (componentId.isEmpty() || !componentId.front().isLetter())
        componentId.prepend(QStringLiteral("component_"));
    placement_ = InteractivePlacementModel::create(
        componentId.left(128), mesh_->sha256(), placementDocumentId_);
    placement_->documentRevision = placementDocumentRevision_;
    placement_->documentSha256 = placementDocumentSha256_;
    placement_->sourcePath = path;
    targetWidthSpin_->blockSignals(true);
    targetWidthSpin_->setValue(std::max(0.001, double(mesh_->sizeMm().x())));
    targetWidthSpin_->setEnabled(false);
    targetWidthSpin_->blockSignals(false);
    updatePlacementControls();
    updateScale();
    updateMarkerTable();
    exportButton_->setEnabled(false);
    placementExportButton_->setEnabled(false);
    if (auto* group = findChild<QGroupBox*>(
            QStringLiteral("interactive_component_placement")))
        group->setEnabled(true);
    modeLabel_->setText(QStringLiteral(
        "Interactive STEP placement · exact millimetres\n"
        "6-DOF preview only until the placement is locked and host-validated."));
    emit statusMessage(QStringLiteral("STEP component ready for 6-DOF placement: %1")
                           .arg(QFileInfo(path).fileName()));
    if (errorMessage) errorMessage->clear();
    return true;
}

void EnclosureConceptTab::chooseStepComponent()
{
    const QString path = QFileDialog::getOpenFileName(
        this, QStringLiteral("Place interactive STEP component"), QString(),
        QStringLiteral("STEP AP203/AP214/AP242 (*.step *.stp);;All files (*)"));
    if (path.isEmpty()) return;
    QString error;
    if (!loadStepComponent(path, &error))
        QMessageBox::critical(this, QStringLiteral("STEP rejected"), error);
}

void EnclosureConceptTab::setPlacementDocumentBinding(
    const QString& documentId, qint64 revision, const QString& sha256)
{
    placementDocumentId_ = documentId;
    placementDocumentRevision_ = revision;
    placementDocumentSha256_ = sha256;
    if (!placement_) return;
    placement_->documentId = documentId;
    placement_->documentRevision = revision;
    placement_->documentSha256 = sha256;
    updatePlacementControls();
    updateSummary();
}

void EnclosureConceptTab::setSupportDesignVolumeMm(const QVector3D& sizeMm)
{
    supportDesignVolumeMm_ = sizeMm;
    updatePlacementControls();
}

void EnclosureConceptTab::choosePlacementDestination()
{
    if (!placement_) return;
    QString suggested = QFileInfo(sourcePath_).completeBaseName()
        + QStringLiteral(".placement.json");
    QString path = QFileDialog::getSaveFileName(
        this, QStringLiteral("Export component placement"), suggested,
        QStringLiteral("Component placement (*.json)"));
    if (path.isEmpty()) return;
    if (!path.endsWith(QStringLiteral(".json"), Qt::CaseInsensitive))
        path += QStringLiteral(".json");
    QString error;
    if (!exportPlacement(path, &error))
        QMessageBox::critical(this, QStringLiteral("Placement export failed"), error);
    else
        emit statusMessage(QStringLiteral("Revision-bound component placement exported"));
}

void EnclosureConceptTab::chooseSupportDestination()
{
    if (!placement_) return;
    QString suggested = QFileInfo(sourcePath_).completeBaseName()
        + QStringLiteral(".support-generation.json");
    QString path = QFileDialog::getSaveFileName(
        this, QStringLiteral("Export cavity/rib specification"), suggested,
        QStringLiteral("Support generation specification (*.json)"));
    if (path.isEmpty()) return;
    if (!path.endsWith(QStringLiteral(".json"), Qt::CaseInsensitive))
        path += QStringLiteral(".json");
    QString error;
    if (!exportSupportSpecification(path, &error))
        QMessageBox::critical(this, QStringLiteral("Support export failed"), error);
    else
        emit statusMessage(QStringLiteral(
            "Revision-bound support specification exported; choose local or cloud refinement"));
}

void EnclosureConceptTab::applyPlacementControls()
{
    if (!placement_) return;
    InteractivePlacementModel::CoordinateSystem coordinateSystem;
    if (!InteractivePlacementModel::coordinateSystemFromName(
            coordinateSystemCombo_->currentData().toString(), &coordinateSystem)) return;
    placement_->coordinateSystem = coordinateSystem;
    placement_->snap = {gridSnapCheck_->isChecked(), surfaceSnapCheck_->isChecked(),
                        axisSnapCheck_->isChecked(), symmetrySnapCheck_->isChecked(),
                        clearanceSnapCheck_->isChecked()};
    placement_->locks = {positionLockCheck_->isChecked(),
                         orientationLockCheck_->isChecked(),
                         surfaceLockCheck_->isChecked()};

    QVector3D translation(float(translationSpins_[0]->value()),
                          float(translationSpins_[1]->value()),
                          float(translationSpins_[2]->value()));
    QVector3D rotation(float(rotationSpins_[0]->value()),
                       float(rotationSpins_[1]->value()),
                       float(rotationSpins_[2]->value()));
    if (placement_->snap.grid) {
        constexpr double GridMm = 0.5;
        translation.setX(float(std::round(translation.x() / GridMm) * GridMm));
        translation.setY(float(std::round(translation.y() / GridMm) * GridMm));
        translation.setZ(float(std::round(translation.z() / GridMm) * GridMm));
    }
    if (placement_->snap.axis) {
        constexpr double AngleIncrementDeg = 15.0;
        rotation.setX(float(std::round(rotation.x() / AngleIncrementDeg)
                            * AngleIncrementDeg));
        rotation.setY(float(std::round(rotation.y() / AngleIncrementDeg)
                            * AngleIncrementDeg));
        rotation.setZ(float(std::round(rotation.z() / AngleIncrementDeg)
                            * AngleIncrementDeg));
    }
    QString error;
    if (!placement_->setTranslationMm(translation, &error)
        || !placement_->setEulerDegrees(rotation, &error)
        || !placement_->setClearanceMm(clearanceSpin_->value(), &error)) {
        emit statusMessage(QStringLiteral("Placement rejected: %1").arg(error));
        return;
    }
    projectionView_->setModelTransform(placement_->transform());
    updatePlacementControls();
    updateSummary();
}

void EnclosureConceptTab::updatePlacementControls()
{
    if (!placement_) return;
    const QSignalBlocker coordinateBlock(coordinateSystemCombo_);
    const int coordinateIndex = coordinateSystemCombo_->findData(
        InteractivePlacementModel::coordinateSystemName(placement_->coordinateSystem));
    if (coordinateIndex >= 0) coordinateSystemCombo_->setCurrentIndex(coordinateIndex);
    const QVector3D translation = placement_->translationMm;
    const QVector3D rotation = placement_->eulerDegrees();
    for (int axis = 0; axis < 3; ++axis) {
        const QSignalBlocker translationBlock(translationSpins_[axis]);
        const QSignalBlocker rotationBlock(rotationSpins_[axis]);
        translationSpins_[axis]->setValue(translation[axis]);
        rotationSpins_[axis]->setValue(rotation[axis]);
        translationSpins_[axis]->setEnabled(!placement_->locks.position);
        rotationSpins_[axis]->setEnabled(!placement_->locks.orientation);
    }
    const QSignalBlocker clearanceBlock(clearanceSpin_);
    clearanceSpin_->setValue(placement_->clearanceMm);
    const struct CheckState { QCheckBox* check; bool value; } checks[] = {
        {gridSnapCheck_, placement_->snap.grid},
        {surfaceSnapCheck_, placement_->snap.surface},
        {axisSnapCheck_, placement_->snap.axis},
        {symmetrySnapCheck_, placement_->snap.symmetry},
        {clearanceSnapCheck_, placement_->snap.clearance},
        {positionLockCheck_, placement_->locks.position},
        {orientationLockCheck_, placement_->locks.orientation},
        {surfaceLockCheck_, placement_->locks.surfaceAnchor},
    };
    for (const CheckState& state : checks) {
        const QSignalBlocker blocker(state.check);
        state.check->setChecked(state.value);
    }
    placementExportButton_->setEnabled(
        placement_->locks.position && placement_->locks.orientation
        && placement_->documentId != QStringLiteral("unbound"));
    supportExportButton_->setEnabled(
        placement_->locks.position && placement_->locks.orientation
        && placement_->documentId != QStringLiteral("unbound")
        && supportDesignVolumeMm_.x() > 0.0f
        && supportDesignVolumeMm_.y() > 0.0f
        && supportDesignVolumeMm_.z() > 0.0f);
}

bool EnclosureConceptTab::generateBalancingSphereDemo(const QString& outputDirectory,
                                                       QString* errorMessage)
{
    dc::mechatronics::BalancingSphereConfig config;
    dc::mechatronics::BalancingSpherePackageResult result;
    std::string error;
    const bool ok = dc::mechatronics::generateBalancingSpherePackage(
        config, outputDirectory.toStdString(), result, error);
    if (!ok) {
        if (errorMessage) *errorMessage = QString::fromStdString(error);
        return false;
    }
    summaryLabel_->setText(
        QStringLiteral("Self-balancing sphere package generated\n"
                       "%1 validated B-Rep parts · %2 kg\n"
                       "Static balance envelope: ±%3°\n%4")
            .arg(result.validatedSolidCount)
            .arg(result.totalMassKg, 0, 'f', 3)
            .arg(result.maximumStaticAngleDeg, 0, 'f', 2)
            .arg(QString::fromStdString(result.assemblyStepPath)));
    emit statusMessage(QStringLiteral("Generated and STEP-validated self-balancing sphere package"));
    if (errorMessage) errorMessage->clear();
    return true;
}

void EnclosureConceptTab::configureBalancingSphereDemo()
{
    QDialog dialog(this);
    dialog.setWindowTitle(QStringLiteral("Self-balancing sphere demonstrator"));
    auto* form = new QFormLayout(&dialog);
    auto makeSpin = [&dialog](double minimum, double maximum, double value,
                              const QString& suffix, int decimals = 2) {
        auto* spin = new QDoubleSpinBox(&dialog);
        spin->setRange(minimum, maximum); spin->setValue(value);
        spin->setDecimals(decimals); spin->setSuffix(suffix);
        return spin;
    };
    auto* radius = makeSpin(75, 500, 80, QStringLiteral(" mm"));
    auto* wall = makeSpin(1, 25, 3, QStringLiteral(" mm"));
    auto* travel = makeSpin(5, 100, 35, QStringLiteral(" mm"));
    auto* ballast = makeSpin(0.05, 5, 0.35, QStringLiteral(" kg"), 3);
    auto* roll = makeSpin(-45, 45, -2, QStringLiteral("°"));
    auto* pitch = makeSpin(-45, 45, 3, QStringLiteral("°"));
    auto* rollRate = makeSpin(-360, 360, -6, QStringLiteral("°/s"));
    auto* pitchRate = makeSpin(-360, 360, 8, QStringLiteral("°/s"));
    form->addRow(QStringLiteral("Outer radius"), radius);
    form->addRow(QStringLiteral("Shell wall"), wall);
    form->addRow(QStringLiteral("Ballast travel per axis"), travel);
    form->addRow(QStringLiteral("Ballast mass"), ballast);
    form->addRow(QStringLiteral("Surface roll"), roll);
    form->addRow(QStringLiteral("Surface pitch"), pitch);
    form->addRow(QStringLiteral("Roll angular velocity"), rollRate);
    form->addRow(QStringLiteral("Pitch angular velocity"), pitchRate);
    auto* warning = new QLabel(
        QStringLiteral("This creates a design demonstrator. Stability, motor sizing, "
                       "battery safety and shell strength still require engineering review."),
        &dialog);
    warning->setWordWrap(true);
    form->addRow(warning);
    auto* buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel,
                                         &dialog);
    form->addRow(buttons);
    connect(buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    if (dialog.exec() != QDialog::Accepted) return;

    const QString directory = QFileDialog::getExistingDirectory(
        this, QStringLiteral("Choose sphere package directory"));
    if (directory.isEmpty()) return;
    dc::mechatronics::BalancingSphereConfig config;
    config.outerRadiusMm = radius->value();
    config.wallThicknessMm = wall->value();
    config.railTravelMm = travel->value();
    config.ballastMassKg = ballast->value();
    config.surfaceRollDeg = roll->value();
    config.surfacePitchDeg = pitch->value();
    config.rollRateDegS = rollRate->value();
    config.pitchRateDegS = pitchRate->value();
    config.targetSafetyFactor = safetyFactorSpin_->value();
    dc::mechatronics::BalancingSpherePackageResult result;
    std::string error;
    if (!dc::mechatronics::generateBalancingSpherePackage(
            config, directory.toStdString(), result, error)) {
        QMessageBox::critical(this, QStringLiteral("Sphere generation failed"),
                              QString::fromStdString(error));
        return;
    }
    summaryLabel_->setText(
        QStringLiteral("Self-balancing sphere package generated\n"
                       "%1 validated B-Rep parts · %2 kg\n"
                       "Ballast target: X %3 mm, Y %4 mm\n"
                       "Static balance envelope: ±%5°\n%6")
            .arg(result.validatedSolidCount)
            .arg(result.totalMassKg, 0, 'f', 3)
            .arg(result.initialCommand.targetXmm, 0, 'f', 2)
            .arg(result.initialCommand.targetYmm, 0, 'f', 2)
            .arg(result.maximumStaticAngleDeg, 0, 'f', 2)
            .arg(QString::fromStdString(result.assemblyStepPath)));
    emit statusMessage(QStringLiteral("Generated and STEP-validated self-balancing sphere package"));
}

bool EnclosureConceptTab::loadGlb(const QString& path, QString* errorMessage)
{
    const GlbLoadResult result = GlbMeshReader::loadFile(path);
    if (!result.ok()) {
        if (errorMessage) *errorMessage = result.errorSummary();
        return false;
    }
    mesh_ = result.mesh;
    sourcePath_ = path;
    markers_.clear();
    placement_.reset();
    scaleConfirmed_ = false;
    projectionView_->setUpAxis(MeshProjectionView::UpAxis::Y);
    projectionView_->setMesh(mesh_);
    placementExportButton_->setEnabled(false);
    if (auto* group = findChild<QGroupBox*>(
            QStringLiteral("interactive_component_placement")))
        group->setEnabled(false);

    const double sourceWidth = mesh_->sizeMm().x();
    const double defaultWidth = sourceWidth > 1e-9
        ? sourceWidth
        : std::max({double(mesh_->sizeMm().x()), double(mesh_->sizeMm().y()),
                    double(mesh_->sizeMm().z()), 1.0});
    targetWidthSpin_->blockSignals(true);
    targetWidthSpin_->setValue(defaultWidth);
    targetWidthSpin_->setEnabled(sourceWidth > 1e-9);
    targetWidthSpin_->blockSignals(false);
    updateScale();
    updateMarkerTable();
    exportButton_->setEnabled(true);
    clearButton_->setEnabled(false);
    if (errorMessage) errorMessage->clear();
    emit statusMessage(QStringLiteral("GLB verified: %1 vertices, %2 triangles")
                           .arg(mesh_->verticesMm().size())
                           .arg(mesh_->triangleIndices().size() / 3));
    return true;
}

void EnclosureConceptTab::chooseGlb()
{
    const QString path = QFileDialog::getOpenFileName(
        this, QStringLiteral("Load enclosure concept GLB"), QString(),
        QStringLiteral("glTF Binary (*.glb);;All files (*)"));
    if (path.isEmpty()) return;
    QString error;
    if (!loadGlb(path, &error))
        QMessageBox::critical(this, QStringLiteral("GLB rejected"), error);
}

void EnclosureConceptTab::chooseEvidenceDestination()
{
    if (!mesh_) return;
    if (markers_.size() >= 10'000) {
        emit statusMessage(QStringLiteral("Marker limit reached (10,000)"));
        return;
    }
    QString suggested = QFileInfo(sourcePath_).completeBaseName()
        + QStringLiteral(".mesh-evidence.json");
    QString path = QFileDialog::getSaveFileName(
        this, QStringLiteral("Export mesh evidence"), suggested,
        QStringLiteral("Mesh evidence (*.json)"));
    if (path.isEmpty()) return;
    if (!path.endsWith(QStringLiteral(".json"), Qt::CaseInsensitive)) path += QStringLiteral(".json");
    QString error;
    if (!exportEvidence(path, &error))
        QMessageBox::critical(this, QStringLiteral("Evidence export failed"), error);
}

void EnclosureConceptTab::addMarker(quint32 vertexIndex, const QVector3D& positionMm,
                                    const QString& projection)
{
    if (!mesh_) return;
    Marker marker;
    marker.id = QUuid::createUuid().toString(QUuid::WithoutBraces);
    marker.role = markerRoleCombo_->currentData().toString();
    marker.vertexIndex = vertexIndex;
    marker.positionMm = positionMm;
    marker.projection = projection;
    if (marker.role == QStringLiteral("load_point")) {
        marker.forceN = forceSpin_->value();
        marker.forceDirection = forceDirectionCombo_->currentData().value<QVector3D>();
    }
    markers_.push_back(std::move(marker));
    updateMarkerTable();
    emit statusMessage(QStringLiteral("Added %1 marker at vertex %2")
                           .arg(markers_.back().role).arg(vertexIndex));
}

void EnclosureConceptTab::clearMarkers()
{
    markers_.clear();
    updateMarkerTable();
    emit statusMessage(QStringLiteral("Enclosure evidence markers cleared"));
}

void EnclosureConceptTab::updateScale()
{
    if (!mesh_) return;
    const double sourceWidth = mesh_->sizeMm().x();
    const double scale = sourceWidth > 1e-9 ? targetWidthSpin_->value() / sourceWidth : 1.0;
    projectionView_->setScaleToConfirmedMm(scale);
    for (Marker& marker : markers_)
        marker.positionMm = mesh_->verticesMm().at(marker.vertexIndex) * float(scale);
    updateMarkerTable();
    updateSummary();
}

void EnclosureConceptTab::updateSummary()
{
    if (!mesh_) { summaryLabel_->setText(QStringLiteral("No GLB loaded.")); return; }
    const double scale = mesh_->sizeMm().x() > 1e-9
        ? targetWidthSpin_->value() / mesh_->sizeMm().x() : 1.0;
    const QVector3D size = mesh_->sizeMm() * float(scale);
    QString summary = QStringLiteral("%1\nSHA-256 %2…\n%3 vertices · %4 triangles\n"
                       "Working envelope: %5 × %6 × %7 mm · scale %8× (%9)")
            .arg(QFileInfo(sourcePath_).fileName(), mesh_->sha256().left(12))
            .arg(mesh_->verticesMm().size()).arg(mesh_->triangleIndices().size() / 3)
            .arg(compactNumber(size.x()), compactNumber(size.y()), compactNumber(size.z()))
            .arg(QString::number(scale, 'g', 8),
                 scaleConfirmed_ ? QStringLiteral("user confirmed")
                                 : QStringLiteral("source default"));
    if (placement_) {
        const QVector3D euler = placement_->eulerDegrees();
        summary += QStringLiteral("\nPlacement: [%1, %2, %3] mm · RPY [%4, %5, %6]° · %7")
            .arg(compactNumber(placement_->translationMm.x()),
                 compactNumber(placement_->translationMm.y()),
                 compactNumber(placement_->translationMm.z()),
                 compactNumber(euler.x()), compactNumber(euler.y()), compactNumber(euler.z()),
                 InteractivePlacementModel::coordinateSystemName(
                     placement_->coordinateSystem));
    }
    summaryLabel_->setText(summary);
}

void EnclosureConceptTab::updateMarkerTable()
{
    markerTable_->setRowCount(markers_.size());
    QVector<quint32> marked;
    marked.reserve(markers_.size());
    for (qsizetype row = 0; row < markers_.size(); ++row) {
        const Marker& marker = markers_.at(row);
        marked.push_back(marker.vertexIndex);
        markerTable_->setItem(row, 0, new QTableWidgetItem(marker.role));
        markerTable_->setItem(row, 1, new QTableWidgetItem(QString::number(marker.vertexIndex)));
        markerTable_->setItem(row, 2, new QTableWidgetItem(
            QStringLiteral("%1, %2, %3")
                .arg(compactNumber(marker.positionMm.x()), compactNumber(marker.positionMm.y()),
                     compactNumber(marker.positionMm.z()))));
    }
    projectionView_->setMarkedVertices(marked);
    clearButton_->setEnabled(!markers_.isEmpty());
}

bool EnclosureConceptTab::exportEvidence(const QString& path, QString* errorMessage) const
{
    if (!mesh_) {
        if (errorMessage) *errorMessage = QStringLiteral("no verified GLB is loaded");
        return false;
    }
    const double scale = mesh_->sizeMm().x() > 1e-9
        ? targetWidthSpin_->value() / mesh_->sizeMm().x() : 1.0;
    QJsonArray markers;
    bool haveLoad = false;
    bool haveSupport = false;
    for (const Marker& marker : markers_) {
        haveLoad = haveLoad || marker.role == QStringLiteral("load_point");
        haveSupport = haveSupport || marker.role == QStringLiteral("fixed_support");
        QJsonObject markerJson{
            {QStringLiteral("id"), marker.id},
            {QStringLiteral("role"), marker.role},
            {QStringLiteral("vertex_index"), double(marker.vertexIndex)},
            {QStringLiteral("position_mm"), vectorJson(marker.positionMm)},
            {QStringLiteral("projection"), marker.projection},
        };
        if (marker.role == QStringLiteral("load_point")) {
            markerJson.insert(QStringLiteral("load"), QJsonObject{
                {QStringLiteral("force_n"), marker.forceN},
                {QStringLiteral("direction_unit"), vectorJson(marker.forceDirection)},
            });
        }
        markers.append(markerJson);
    }
    const QJsonObject document{
        {QStringLiteral("schema"), QStringLiteral("urn:design-studio:schema:mesh-evidence:1")},
        {QStringLiteral("version"), 1},
        {QStringLiteral("created_at"), QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs)},
        {QStringLiteral("producer"), QJsonObject{
            {QStringLiteral("name"), QStringLiteral("DesignStudio")},
            {QStringLiteral("version"), QStringLiteral("1.0.0")},
        }},
        {QStringLiteral("source"), QJsonObject{
            {QStringLiteral("sha256"), mesh_->sha256()},
            {QStringLiteral("original_name"), QFileInfo(sourcePath_).fileName()},
            {QStringLiteral("byte_size"), double(mesh_->sourceBytes())},
            {QStringLiteral("media_type"), QStringLiteral("model/gltf-binary")},
            {QStringLiteral("linear_units"), QStringLiteral("m")},
        }},
        {QStringLiteral("geometry"), QJsonObject{
            {QStringLiteral("coordinate_system"), QStringLiteral("gltf-right-handed-y-up")},
            {QStringLiteral("concept_scale_factor"), scale},
            {QStringLiteral("scale_status"), scaleConfirmed_
                ? QStringLiteral("user_confirmed") : QStringLiteral("source_default")},
            {QStringLiteral("source_bounds_mm"), QJsonObject{
                {QStringLiteral("min"), vectorJson(mesh_->boundsMinMm())},
                {QStringLiteral("max"), vectorJson(mesh_->boundsMaxMm())},
            }},
            {QStringLiteral("scaled_bounds_mm"), QJsonObject{
                {QStringLiteral("min"), vectorJson(mesh_->boundsMinMm() * float(scale))},
                {QStringLiteral("max"), vectorJson(mesh_->boundsMaxMm() * float(scale))},
            }},
            {QStringLiteral("vertex_count"), double(mesh_->verticesMm().size())},
            {QStringLiteral("triangle_count"), double(mesh_->triangleIndices().size() / 3)},
        }},
        {QStringLiteral("enclosure_intent"), QJsonObject{
            {QStringLiteral("status"), QStringLiteral("concept")},
            {QStringLiteral("manufacturing_process"), processCombo_->currentData().toString()},
            {QStringLiteral("nominal_wall_thickness_mm"), wallThicknessSpin_->value()},
            {QStringLiteral("requires_engineering_review"), true},
        }},
        {QStringLiteral("structural_intent"), QJsonObject{
            {QStringLiteral("reinforcement_strategy"), reinforcementCombo_->currentData().toString()},
            {QStringLiteral("target_safety_factor"), safetyFactorSpin_->value()},
            {QStringLiteral("boundary_condition_status"),
                haveLoad && haveSupport ? QStringLiteral("complete_for_review")
                    : (haveLoad || haveSupport ? QStringLiteral("partial")
                                               : QStringLiteral("unconfirmed"))},
            {QStringLiteral("required_validation"), QJsonArray{
                QStringLiteral("load_case_review"), QStringLiteral("fea"),
                QStringLiteral("manufacturing_review")}},
        }},
        {QStringLiteral("markers"), markers},
    };

    QSaveFile output(path);
    if (!output.open(QIODevice::WriteOnly)) {
        if (errorMessage) *errorMessage = output.errorString();
        return false;
    }
    const QByteArray bytes = QJsonDocument(document).toJson(QJsonDocument::Indented);
    if (output.write(bytes) != bytes.size() || !output.commit()) {
        if (errorMessage) *errorMessage = output.errorString();
        return false;
    }
    if (errorMessage) errorMessage->clear();
    return true;
}

bool EnclosureConceptTab::exportPlacement(const QString& path, QString* errorMessage) const
{
    if (!placement_) {
        if (errorMessage) *errorMessage = QStringLiteral("no interactive STEP component is loaded");
        return false;
    }
    QString validationError;
    if (!placement_->validate(&validationError)) {
        if (errorMessage) *errorMessage = validationError;
        return false;
    }
    if (!placement_->locks.position || !placement_->locks.orientation) {
        if (errorMessage) {
            *errorMessage = QStringLiteral(
                "lock both component position and orientation before export");
        }
        return false;
    }
    if (placement_->documentId == QStringLiteral("unbound")) {
        if (errorMessage) {
            *errorMessage = QStringLiteral(
                "open a product workspace before exporting a revision-bound placement");
        }
        return false;
    }
    QJsonObject placementJson = placement_->toJson();
    placementJson.insert(QStringLiteral("source"), QJsonObject{
        {QStringLiteral("original_name"), QFileInfo(sourcePath_).fileName()},
        {QStringLiteral("path"), QFileInfo(sourcePath_).absoluteFilePath()},
    });
    // Source location is useful workspace evidence but deliberately excluded
    // from the portable placement contract. Keep the contract itself nested so
    // schema validation remains strict and host paths cannot alter identity.
    const QJsonObject document{
        {QStringLiteral("schema"), QStringLiteral("design-studio.placement-evidence/1")},
        {QStringLiteral("placement"), placement_->toJson()},
        {QStringLiteral("source"), placementJson.value(QStringLiteral("source"))},
        {QStringLiteral("created_at"),
         QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs)},
    };
    QSaveFile output(path);
    if (!output.open(QIODevice::WriteOnly)) {
        if (errorMessage) *errorMessage = output.errorString();
        return false;
    }
    const QByteArray bytes = QJsonDocument(document).toJson(QJsonDocument::Indented);
    if (output.write(bytes) != bytes.size() || !output.commit()) {
        if (errorMessage) *errorMessage = output.errorString();
        return false;
    }
    if (errorMessage) errorMessage->clear();
    return true;
}

bool EnclosureConceptTab::exportSupportSpecification(
    const QString& path, QString* errorMessage) const
{
    if (!placement_ || !mesh_) {
        if (errorMessage) *errorMessage = QStringLiteral("no STEP placement is loaded");
        return false;
    }
    QString validationError;
    if (!placement_->validate(&validationError)
        || !placement_->locks.position || !placement_->locks.orientation) {
        if (errorMessage) {
            *errorMessage = validationError.isEmpty()
                ? QStringLiteral("lock position and orientation before support export")
                : validationError;
        }
        return false;
    }
    if (placement_->documentId == QStringLiteral("unbound")
        || supportDesignVolumeMm_.x() <= 0.0f
        || supportDesignVolumeMm_.y() <= 0.0f
        || supportDesignVolumeMm_.z() <= 0.0f) {
        if (errorMessage) {
            *errorMessage = QStringLiteral(
                "an active mechanical contract with a positive design volume is required");
        }
        return false;
    }
    QVector3D minimum(std::numeric_limits<float>::max(),
                      std::numeric_limits<float>::max(),
                      std::numeric_limits<float>::max());
    QVector3D maximum(std::numeric_limits<float>::lowest(),
                      std::numeric_limits<float>::lowest(),
                      std::numeric_limits<float>::lowest());
    const QMatrix4x4 transform = placement_->transform();
    for (const QVector3D& vertex : mesh_->verticesMm()) {
        const QVector3D point = transform.map(vertex);
        minimum.setX(std::min(minimum.x(), point.x()));
        minimum.setY(std::min(minimum.y(), point.y()));
        minimum.setZ(std::min(minimum.z(), point.z()));
        maximum.setX(std::max(maximum.x(), point.x()));
        maximum.setY(std::max(maximum.y(), point.y()));
        maximum.setZ(std::max(maximum.z(), point.z()));
    }
    if (minimum.x() < 0.0f || minimum.y() < 0.0f || minimum.z() < 0.0f
        || maximum.x() > supportDesignVolumeMm_.x()
        || maximum.y() > supportDesignVolumeMm_.y()
        || maximum.z() > supportDesignVolumeMm_.z()) {
        if (errorMessage) {
            *errorMessage = QStringLiteral(
                "the transformed component must remain inside the mechanical design volume");
        }
        return false;
    }

    QJsonArray anchors;
    QJsonArray loadCases;
    const QString primaryAnchorId = QStringLiteral("mount:%1").arg(placement_->placementId);
    anchors.append(QJsonObject{
        {QStringLiteral("id"), primaryAnchorId},
        {QStringLiteral("position_mm"), vectorJson((minimum + maximum) * 0.5f)},
        {QStringLiteral("kind"), QStringLiteral("mount")},
    });
    for (const Marker& marker : markers_) {
        QString kind;
        if (marker.role == QStringLiteral("load_point")) kind = QStringLiteral("load");
        else if (marker.role == QStringLiteral("pcb_mount")) kind = QStringLiteral("pcb");
        else if (marker.role == QStringLiteral("connector_support"))
            kind = QStringLiteral("connector");
        else if (marker.role == QStringLiteral("fixed_support"))
            kind = QStringLiteral("mount");
        else continue;
        const QString anchorId = QStringLiteral("%1:%2").arg(kind, marker.id);
        anchors.append(QJsonObject{
            {QStringLiteral("id"), anchorId},
            {QStringLiteral("position_mm"), vectorJson(marker.positionMm)},
            {QStringLiteral("kind"), kind},
        });
        if (marker.role == QStringLiteral("load_point")) {
            loadCases.append(QJsonObject{
                {QStringLiteral("anchor_id"), anchorId},
                {QStringLiteral("force_n"),
                 vectorJson(marker.forceDirection * float(marker.forceN))},
            });
        }
    }
    QString process = QStringLiteral("fdm");
    const QString processIntent = processCombo_->currentData().toString();
    if (processIntent == QStringLiteral("cnc_aluminum")) process = QStringLiteral("cnc");
    else if (processIntent == QStringLiteral("sheet_metal")) process = QStringLiteral("cnc");
    else if (processIntent == QStringLiteral("metal_additive"))
        process = QStringLiteral("sls");
    else if (processIntent == QStringLiteral("die_cast"))
        process = QStringLiteral("injection_molding");
    const QString seedMaterial = placement_->componentSha256
        + placement_->documentSha256 + placement_->placementId;
    const QByteArray seedDigest = QCryptographicHash::hash(
        seedMaterial.toUtf8(), QCryptographicHash::Sha256);
    const quint32 seed = qFromBigEndian<quint32>(
        reinterpret_cast<const uchar*>(seedDigest.constData())) & 0x7fffffffU;
    const QJsonObject documentBinding{
        {QStringLiteral("document_id"), placement_->documentId},
        {QStringLiteral("revision"), double(placement_->documentRevision)},
        {QStringLiteral("sha256"), placement_->documentSha256},
    };
    const QJsonObject specification{
        {QStringLiteral("schema"), QStringLiteral("design-studio.support-generation/1")},
        {QStringLiteral("document"), documentBinding},
        {QStringLiteral("placements"), QJsonArray{placement_->toJson()}},
        {QStringLiteral("design_volume_mm"), QJsonObject{
            {QStringLiteral("min"), QJsonArray{0.0, 0.0, 0.0}},
            {QStringLiteral("max"), vectorJson(supportDesignVolumeMm_)},
        }},
        {QStringLiteral("anchors"), anchors},
        {QStringLiteral("forbidden_bounds_mm"), QJsonArray{QJsonObject{
            {QStringLiteral("min"), vectorJson(minimum)},
            {QStringLiteral("max"), vectorJson(maximum)},
        }}},
        {QStringLiteral("load_cases"), loadCases},
        {QStringLiteral("manufacturing"), QJsonObject{
            {QStringLiteral("process"), process},
            {QStringLiteral("minimum_wall_mm"), wallThicknessSpin_->value()},
            {QStringLiteral("minimum_rib_mm"),
             std::max(0.1, wallThicknessSpin_->value() * 0.6)},
            {QStringLiteral("clearance_mm"), placement_->clearanceMm},
        }},
        {QStringLiteral("seed"), double(seed)},
    };
    QSaveFile output(path);
    if (!output.open(QIODevice::WriteOnly)) {
        if (errorMessage) *errorMessage = output.errorString();
        return false;
    }
    const QByteArray bytes =
        QJsonDocument(specification).toJson(QJsonDocument::Indented);
    if (output.write(bytes) != bytes.size() || !output.commit()) {
        if (errorMessage) *errorMessage = output.errorString();
        return false;
    }
    if (errorMessage) errorMessage->clear();
    return true;
}

} // namespace designstudio

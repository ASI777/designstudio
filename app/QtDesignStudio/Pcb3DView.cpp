#include "Pcb3DView.h"

#include "MeshProjectionView.h"
#include "GlbMeshReader.h"
#include "ProjectModel.h"
#include "StepMeshReader.h"
#include "SemanticAssembly.h"

#include <QComboBox>
#include <QApplication>
#include <QCheckBox>
#include <QDir>
#include <QFileDialog>
#include <QFile>
#include <QFileInfo>
#include <QHBoxLayout>
#include <QJsonDocument>
#include <QJsonArray>
#include <QJsonObject>
#include <QEventLoop>
#include <QFuture>
#include <QFutureWatcher>
#include <QLabel>
#include <QPushButton>
#include <QProgressBar>
#include <QtConcurrent/QtConcurrent>
#include <QVBoxLayout>

namespace designstudio {

Pcb3DView::Pcb3DView(ProjectModel* model, QWidget* parent)
    : QWidget(parent), model_(model)
{
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(8, 8, 8, 8);

    auto* controls = new QHBoxLayout;
    auto* openButton = new QPushButton(QStringLiteral("Open board STEP…"), this);
    reloadButton_ = new QPushButton(QStringLiteral("Reload"), this);
    reloadButton_->setEnabled(false);
    auto* fitButton = new QPushButton(QStringLiteral("Fit"), this);
    auto* projection = new QComboBox(this);
    projection->addItems({QStringLiteral("Isometric"), QStringLiteral("Top"),
                          QStringLiteral("Bottom / underside"),
                          QStringLiteral("Front"), QStringLiteral("Right")});
    auto* display = new QComboBox(this);
    display->addItems({QStringLiteral("Shaded"), QStringLiteral("Shaded + mesh edges"),
                       QStringLiteral("Wireframe")});
    stateLabel_ = new QLabel(this);
    stateLabel_->setTextInteractionFlags(Qt::TextSelectableByMouse);
    stateLabel_->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Preferred);
    stateLabel_->setWordWrap(true);
    progressBar_ = new QProgressBar(this);
    progressBar_->setRange(0, 0);
    progressBar_->setTextVisible(false);
    progressBar_->setFixedHeight(3);
    progressBar_->setVisible(false);
    controls->addWidget(openButton);
    controls->addWidget(reloadButton_);
    controls->addWidget(fitButton);
    controls->addSpacing(8);
    controls->addWidget(new QLabel(QStringLiteral("View:"), this));
    controls->addWidget(projection);
    controls->addWidget(display);
    controls->addStretch(1);
    // Inspection toggles live on their own row: a single combined toolbar
    // overflows on narrower windows and Qt clips the trailing checkboxes.
    auto* toggles = new QHBoxLayout;
    auto* section = new QCheckBox(QStringLiteral("Section at board mid-plane"), this);
    section->setToolTip(QStringLiteral(
        "Inspection-only slice: hides geometry above the 0.56 × 1.6 mm board plane. "
        "It does not modify the source CAD or manufacturing data."));
    toggles->addWidget(section);
    auto* explode = new QCheckBox(QStringLiteral("Exploded view"), this);
    explode->setToolTip(QStringLiteral(
        "Separate each solid outward from the model centre so housing, optics and "
        "interior reservations are individually visible. Display-only; it never "
        "modifies the source STEP or project data."));
    toggles->addWidget(explode);
    auto* components = new QCheckBox(QStringLiteral("Component bodies"), this);
    components->setChecked(true);
    components->setToolTip(QStringLiteral(
        "Show or hide 3D component bodies for stackup, underside, and service-access review."));
    toggles->addWidget(components);
    auto* clearance = new QCheckBox(QStringLiteral("Clearance overlay"), this);
    clearance->setToolTip(QStringLiteral(
        "Inspection-only visual keep-out boxes from semantic component ranges; not a DRC result."));
    toggles->addWidget(clearance);
    toggles->addStretch(1);
    layout->addLayout(controls);
    layout->addLayout(toggles);
    layout->addWidget(stateLabel_);
    layout->addWidget(progressBar_);

    projectionView_ = new MeshProjectionView(this);
    projectionView_->setUpAxis(MeshProjectionView::UpAxis::Z);
    projectionView_->setNavigationEnabled(true);
    projectionView_->setDisplayMode(MeshProjectionView::DisplayMode::Shaded);
    projectionView_->setScenePalette(MeshProjectionView::ScenePalette::PcbAssembly);
    layout->addWidget(projectionView_, 1);
    loadWatcher_ = new QFutureWatcher<StepMeshLoadResult>(this);
    setEmptyState(QStringLiteral(
        "No completed PCB STEP found. Generate the board assembly or open an AP242/AP214 STEP file."));

    connect(openButton, &QPushButton::clicked, this, &Pcb3DView::chooseStep);
    connect(fitButton, &QPushButton::clicked, projectionView_, &MeshProjectionView::fitView);
    connect(reloadButton_, &QPushButton::clicked, this, [this] {
        if (!sourcePath_.isEmpty()) {
            sourceSize_ = -1;
            sourceModifiedMs_ = -1;
            sourceAssemblySize_ = -1;
            sourceAssemblyModifiedMs_ = -1;
            loadStep(sourcePath_);
        }
    });
    connect(projection, &QComboBox::currentIndexChanged, this, [this](int index) {
        const MeshProjectionView::Projection views[] = {
            MeshProjectionView::Projection::Isometric,
            MeshProjectionView::Projection::Top,
            MeshProjectionView::Projection::Bottom,
            MeshProjectionView::Projection::Front,
            MeshProjectionView::Projection::Right,
        };
        projectionView_->setProjection(views[qBound(0, index, 4)]);
    });
    connect(section, &QCheckBox::toggled, projectionView_,
            &MeshProjectionView::setSectionMode);
    connect(explode, &QCheckBox::toggled, projectionView_,
            &MeshProjectionView::setExploded);
    connect(components, &QCheckBox::toggled, projectionView_,
            &MeshProjectionView::setComponentBodiesVisible);
    connect(clearance, &QCheckBox::toggled, projectionView_,
            &MeshProjectionView::setClearanceOverlay);
    connect(display, &QComboBox::currentIndexChanged, this, [this](int index) {
        const MeshProjectionView::DisplayMode modes[] = {
            MeshProjectionView::DisplayMode::Shaded,
            MeshProjectionView::DisplayMode::ShadedEdges,
            MeshProjectionView::DisplayMode::Wireframe,
        };
        projectionView_->setDisplayMode(modes[qBound(0, index, 2)]);
    });
    // Do not import a mechanical STEP merely because an electronics project
    // opened.  The PCB 3D tab requests the asset lazily, keeping project load
    // and the other tabs responsive.
    connect(loadWatcher_, &QFutureWatcher<StepMeshLoadResult>::finished,
            this, &Pcb3DView::finishStepLoad);
    connect(projectionView_, &MeshProjectionView::componentPicked, this,
            [this](const QString& semanticId, const QString& reference) {
        selectedSemanticId_ = semanticId;
        emit componentSelected(reference, semanticId);
        emit statusMessage(reference.isEmpty()
                               ? QStringLiteral("Selected semantic CAD object: %1").arg(semanticId)
                               : QStringLiteral("Selected %1 (%2)").arg(reference, semanticId));
    });
}

QString Pcb3DView::discoverBoardStep() const
{
    const QFileInfo project(model_->filePath());
    if (!project.exists()) return {};
    const QDir directory = project.absoluteDir();
    const QString openedStem = project.completeBaseName();
    QStringList stems{openedStem};
    // A project/3 file is an explicit wrapper around the editable project/2
    // document.  Both documents intentionally share one mechanical assembly.
    if (openedStem.endsWith(QStringLiteral("-v3")))
        stems.append(openedStem.chopped(3));
    QStringList candidates;
    for (const QString& stem : stems) {
        candidates.append({
            directory.filePath(stem + QStringLiteral("-board.step")),
            directory.filePath(stem + QStringLiteral("-board-preview.step")),
            directory.filePath(stem + QStringLiteral("-enclosure-reference.step")),
            directory.filePath(stem + QStringLiteral(".step")),
            directory.filePath(stem + QStringLiteral("-production-package/mechanical/")
                               + stem + QStringLiteral("-board.step")),
            directory.filePath(QStringLiteral("production-package/mechanical/")
                               + stem + QStringLiteral("-board.step")),
            directory.filePath(stem + QStringLiteral("-fab/mechanical/")
                               + stem + QStringLiteral("-board.step")),
        });
    }
    for (const QString& candidate : candidates)
        if (QFileInfo::exists(candidate)) return QFileInfo(candidate).absoluteFilePath();
    return {};
}

void Pcb3DView::refreshFromProject()
{
    const QString discovered = discoverBoardStep();
    if (!discovered.isEmpty()) {
        loadStep(discovered);
        return;
    }
    sourcePath_.clear();
    sourceSize_ = -1;
    sourceModifiedMs_ = -1;
    sourceAssemblySize_ = -1;
    sourceAssemblyModifiedMs_ = -1;
    meshLoaded_ = false;
    ++loadGeneration_;
    loadInProgress_ = false;
    loadingPath_.clear();
    progressBar_->setVisible(false);
    projectionView_->setMesh({});
    selectedSemanticId_.clear();
    emit semanticAssemblyChanged({});
    reloadButton_->setEnabled(false);
    setEmptyState(QStringLiteral(
        "No completed board STEP is beside this project. Use Open board STEP… to inspect one."));
}

bool Pcb3DView::loadStep(const QString& path, QString* errorMessage)
{
    const QFileInfo requested(path);
    const QString absolutePath = requested.absoluteFilePath();
    const qint64 requestedSize = requested.exists() ? requested.size() : -1;
    const qint64 requestedModifiedMs = requested.exists()
        ? requested.lastModified().toMSecsSinceEpoch() : -1;
    const QFileInfo requestedAssembly(SemanticAssemblyReader::sidecarPath(absolutePath));
    const qint64 requestedAssemblySize = requestedAssembly.exists()
        ? requestedAssembly.size() : -1;
    const qint64 requestedAssemblyModifiedMs = requestedAssembly.exists()
        ? requestedAssembly.lastModified().toMSecsSinceEpoch() : -1;
    if (meshLoaded_ && absolutePath == sourcePath_
        && requestedSize == sourceSize_
        && requestedModifiedMs == sourceModifiedMs_
        && requestedAssemblySize == sourceAssemblySize_
        && requestedAssemblyModifiedMs == sourceAssemblyModifiedMs_) {
        projectionView_->fitView();
        if (errorMessage) errorMessage->clear();
        emit statusMessage(QStringLiteral("PCB CAD already loaded: %1")
                               .arg(requested.fileName()));
        return true;
    }

    if (!requested.exists() || !requested.isFile()) {
        const QString message = QStringLiteral("STEP file does not exist: %1").arg(path);
        setEmptyState(message);
        if (errorMessage) *errorMessage = message;
        return false;
    }
    if (loadInProgress_ && absolutePath == loadingPath_) {
        if (errorMessage) errorMessage->clear();
        return true;
    }
    if (loadWatcher_->isRunning()) {
        const QString message = QStringLiteral("Another STEP model is still loading; please wait");
        if (errorMessage) *errorMessage = message;
        emit statusMessage(message);
        return false;
    }

    stateLabel_->setText(QStringLiteral("Loading %1…").arg(requested.fileName()));
    reloadButton_->setEnabled(false);
    progressBar_->setVisible(true);
    loadInProgress_ = true;
    loadingPath_ = absolutePath;
    loadingGeneration_ = ++loadGeneration_;
    ++importCount_;
    // OpenCascade and tessellation are deliberately outside the GUI thread.
    // A stale completion is discarded when another file/project is requested.
    loadWatcher_->setFuture(QtConcurrent::run([absolutePath]() {
        return StepMeshReader::loadFile(absolutePath);
    }));
    if (errorMessage) errorMessage->clear();
    return true;
}

void Pcb3DView::finishStepLoad()
{
    const QString completedPath = loadingPath_;
    const quint64 completedGeneration = loadingGeneration_;
    const StepMeshLoadResult result = loadWatcher_->result();
    if (!loadInProgress_ || completedPath.isEmpty()
        || completedGeneration != loadGeneration_)
        return;
    loadInProgress_ = false;
    loadingPath_.clear();
    progressBar_->setVisible(false);
    if (!result.ok()) {
        projectionView_->setMesh({});
        meshLoaded_ = false;
        emit semanticAssemblyChanged({});
        setEmptyState(result.error);
        emit statusMessage(QStringLiteral("PCB 3D load failed: %1").arg(result.error));
        return;
    }
    const QFileInfo requested(completedPath);
    sourcePath_ = completedPath;
    sourceSize_ = requested.size();
    sourceModifiedMs_ = requested.lastModified().toMSecsSinceEpoch();
    const QFileInfo assembly(SemanticAssemblyReader::sidecarPath(completedPath));
    sourceAssemblySize_ = assembly.exists() ? assembly.size() : -1;
    sourceAssemblyModifiedMs_ = assembly.exists()
        ? assembly.lastModified().toMSecsSinceEpoch() : -1;
    meshLoaded_ = true;
    projectionView_->setMesh(result.mesh);
    emit semanticAssemblyChanged(result.mesh->semanticAssemblyJson());
    selectedSemanticId_.clear();
    reloadButton_->setEnabled(true);
    const QVector3D size = result.mesh->sizeMm();
    const QFileInfo source(completedPath);
    bool engineeringPreview = source.completeBaseName().endsWith(
        QStringLiteral("-board-preview"));
    bool previewManifest = false;
    int previewComponentCount = 0;
    QFile evidence(source.absolutePath() + QLatin1Char('/')
                   + source.completeBaseName() + QStringLiteral(".preview.json"));
    if (evidence.open(QIODevice::ReadOnly)) {
        const QJsonObject sidecar = QJsonDocument::fromJson(evidence.readAll()).object();
        engineeringPreview = engineeringPreview
            || sidecar.value(QStringLiteral("release_status")).toString()
                   == QStringLiteral("engineering_preview_only");
        const QJsonArray components = sidecar.value(QStringLiteral("components")).toArray();
        previewManifest = !components.isEmpty();
        previewComponentCount = components.size();
    }
    const QString identity = result.mesh->assembly() && result.mesh->assembly()->identityAvailable
        ? QStringLiteral("semantic AP242 identity: %1 components")
              .arg(result.mesh->assembly()->components.size())
        : previewManifest
        ? QStringLiteral("preview component manifest: %1 references; AP242 identity pending")
              .arg(previewComponentCount)
        : QStringLiteral("legacy flattened STEP: component identity unavailable");
    stateLabel_->setText(QStringLiteral("%1%2 · %3 × %4 × %5 mm · %6 triangles · %7")
                             .arg(engineeringPreview
                                      ? QStringLiteral("ENGINEERING PREVIEW · NOT CAM · ")
                                      : QString())
                             .arg(source.fileName())
                             .arg(size.x(), 0, 'f', 2)
                             .arg(size.y(), 0, 'f', 2)
                             .arg(size.z(), 0, 'f', 2)
                             .arg(result.mesh->triangleIndices().size() / 3)
                             .arg(identity));
    emit statusMessage(QStringLiteral("Loaded PCB CAD: %1").arg(completedPath));
}

void Pcb3DView::fitToModel()
{
    projectionView_->fitView();
}

bool Pcb3DView::semanticIdentityAvailable() const noexcept
{
    return projectionView_ && projectionView_->semanticIdentityAvailable();
}

void Pcb3DView::selectComponent(const QString& referenceOrSemanticId)
{
    if (!projectionView_ || !projectionView_->hasMesh()) return;
    if (projectionView_->semanticIdentityAvailable()) {
        projectionView_->setSelectedComponentReference(referenceOrSemanticId);
        selectedSemanticId_ = projectionView_->selectedComponent();
    }
}

void Pcb3DView::chooseStep()
{
    const QString path = QFileDialog::getOpenFileName(
        this, QStringLiteral("Open completed PCB STEP"),
        model_->filePath().isEmpty() ? QString() : QFileInfo(model_->filePath()).absolutePath(),
        QStringLiteral("STEP models (*.step *.stp);;All files (*)"));
    if (!path.isEmpty()) loadStep(path);
}

void Pcb3DView::setEmptyState(const QString& message)
{
    stateLabel_->setText(message);
}

} // namespace designstudio

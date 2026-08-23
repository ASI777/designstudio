#pragma once

#include <QString>
#include <QByteArray>
#include <QWidget>
#include <QFutureWatcher>

#include "StepMeshReader.h"

class QLabel;
class QProgressBar;
class QPushButton;
class ProjectModel;

namespace designstudio {
class MeshProjectionView;

class Pcb3DView final : public QWidget {
    Q_OBJECT
public:
    explicit Pcb3DView(ProjectModel* model, QWidget* parent = nullptr);

    bool loadStep(const QString& path, QString* errorMessage = nullptr);
    void refreshFromProject();
    void fitToModel();
    QString sourcePath() const { return sourcePath_; }
    bool hasRenderableMesh() const noexcept { return meshLoaded_; }
    quint64 importCount() const noexcept { return importCount_; }
    bool loadInProgress() const noexcept { return loadInProgress_; }
    bool semanticIdentityAvailable() const noexcept;
    QString selectedSemanticId() const noexcept { return selectedSemanticId_; }
    void selectComponent(const QString& referenceOrSemanticId);

signals:
    void statusMessage(const QString& message);
    void semanticAssemblyChanged(const QByteArray& semanticAssemblyJson);
    void componentSelected(const QString& referenceDesignator,
                           const QString& semanticId);

private:
    QString discoverBoardStep() const;
    void chooseStep();
    void setEmptyState(const QString& message);
    void finishStepLoad();

    ProjectModel* model_{};
    MeshProjectionView* projectionView_{};
    QLabel* stateLabel_{};
    QProgressBar* progressBar_{};
    QPushButton* reloadButton_{};
    QString sourcePath_;
    qint64 sourceSize_{-1};
    qint64 sourceModifiedMs_{-1};
    qint64 sourceAssemblySize_{-1};
    qint64 sourceAssemblyModifiedMs_{-1};
    bool meshLoaded_{false};
    bool loadInProgress_{false};
    quint64 importCount_{0};
    quint64 loadGeneration_{0};
    quint64 loadingGeneration_{0};
    QString loadingPath_;
    QFutureWatcher<StepMeshLoadResult>* loadWatcher_{};
    QString selectedSemanticId_;
};

} // namespace designstudio

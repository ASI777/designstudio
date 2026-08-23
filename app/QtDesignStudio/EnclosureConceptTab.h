#pragma once

#include "InteractivePlacementModel.h"

#include <QVector>
#include <QVector3D>
#include <QWidget>

#include <array>
#include <memory>
#include <optional>

class QCheckBox;
class QComboBox;
class QDoubleSpinBox;
class QLabel;
class QPushButton;
class QTableWidget;

namespace designstudio {
class GlbMesh;
class MeshProjectionView;

class EnclosureConceptTab final : public QWidget {
    Q_OBJECT
public:
    explicit EnclosureConceptTab(QWidget* parent = nullptr);

    bool loadGlb(const QString& path, QString* errorMessage = nullptr);
    bool loadStepComponent(const QString& path, QString* errorMessage = nullptr);
    bool exportEvidence(const QString& path, QString* errorMessage = nullptr) const;
    bool exportPlacement(const QString& path, QString* errorMessage = nullptr) const;
    bool exportSupportSpecification(const QString& path,
                                    QString* errorMessage = nullptr) const;
    void setPlacementDocumentBinding(const QString& documentId, qint64 revision,
                                     const QString& sha256);
    void setSupportDesignVolumeMm(const QVector3D& sizeMm);
    bool generateBalancingSphereDemo(const QString& outputDirectory,
                                      QString* errorMessage = nullptr);
    bool hasMesh() const noexcept { return mesh_ != nullptr; }

signals:
    void statusMessage(const QString& message);

private:
    struct Marker {
        QString id;
        QString role;
        quint32 vertexIndex = 0;
        QVector3D positionMm;
        QString projection;
        double forceN = 0.0;
        QVector3D forceDirection;
    };

    void chooseGlb();
    void chooseStepComponent();
    void choosePlacementDestination();
    void chooseSupportDestination();
    void chooseEvidenceDestination();
    void configureBalancingSphereDemo();
    void addMarker(quint32 vertexIndex, const QVector3D& positionMm,
                   const QString& projection);
    void clearMarkers();
    void updateScale();
    void updateSummary();
    void updateMarkerTable();
    void applyPlacementControls();
    void updatePlacementControls();

    std::shared_ptr<const GlbMesh> mesh_;
    QString sourcePath_;
    QVector<Marker> markers_;
    bool scaleConfirmed_ = false;
    std::optional<InteractivePlacementModel> placement_;
    QString placementDocumentId_ = QStringLiteral("unbound");
    qint64 placementDocumentRevision_ = 0;
    QString placementDocumentSha256_ = QString(64, QLatin1Char('0'));
    QVector3D supportDesignVolumeMm_;

    MeshProjectionView* projectionView_{};
    QComboBox* projectionCombo_{};
    QComboBox* markerRoleCombo_{};
    QComboBox* processCombo_{};
    QComboBox* forceDirectionCombo_{};
    QComboBox* reinforcementCombo_{};
    QComboBox* coordinateSystemCombo_{};
    QDoubleSpinBox* targetWidthSpin_{};
    QDoubleSpinBox* wallThicknessSpin_{};
    QDoubleSpinBox* forceSpin_{};
    QDoubleSpinBox* safetyFactorSpin_{};
    std::array<QDoubleSpinBox*, 3> translationSpins_{};
    std::array<QDoubleSpinBox*, 3> rotationSpins_{};
    QDoubleSpinBox* clearanceSpin_{};
    QCheckBox* gridSnapCheck_{};
    QCheckBox* surfaceSnapCheck_{};
    QCheckBox* axisSnapCheck_{};
    QCheckBox* symmetrySnapCheck_{};
    QCheckBox* clearanceSnapCheck_{};
    QCheckBox* positionLockCheck_{};
    QCheckBox* orientationLockCheck_{};
    QCheckBox* surfaceLockCheck_{};
    QLabel* summaryLabel_{};
    QLabel* modeLabel_{};
    QTableWidget* markerTable_{};
    QPushButton* exportButton_{};
    QPushButton* clearButton_{};
    QPushButton* placementExportButton_{};
    QPushButton* supportExportButton_{};
};

} // namespace designstudio

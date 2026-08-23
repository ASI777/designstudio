#pragma once

#include <QJsonObject>
#include <QList>
#include <QWidget>

class QComboBox;
class QDoubleSpinBox;
class QLabel;
class QLineEdit;
class QPushButton;
class QSpinBox;
class QTabWidget;
class QTableWidget;

class PhysicalDesignPanel final : public QWidget {
    Q_OBJECT
public:
    explicit PhysicalDesignPanel(QWidget* parent = nullptr);
    void setOperationResult(const QString& operation, bool ok,
                            const QJsonObject& result);

Q_SIGNALS:
    void operationRequested(const QString& operation, const QJsonObject& arguments);
    void codexPromptRequested(const QString& prompt);
    void statusMessage(const QString& message);

private:
    QTabWidget* m_steps{};
    QLineEdit* m_evidencePath{};
    QLabel* m_evidenceDigest{};
    QComboBox* m_evidenceKind{};
    QDoubleSpinBox* m_knownLength{};
    QDoubleSpinBox* m_pixelsPerMm{};
    QComboBox* m_workflowMode{};
    QComboBox* m_volumeKind{};
    QTableWidget* m_volumes{};
    QLineEdit* m_sessionPath{};
    QTableWidget* m_candidateTable{};
    QLineEdit* m_planPath{};
    QLineEdit* m_planId{};
    QLineEdit* m_material{};
    QLineEdit* m_desire{};
    QComboBox* m_process{};
    QLineEdit* m_participantId{};
    QComboBox* m_handGroup{};
    QDoubleSpinBox* m_handLength{};
    QDoubleSpinBox* m_handBreadth{};
    QComboBox* m_candidate{};
    QSpinBox* m_testOrder{};
    QList<QSpinBox*> m_ratings;
    QLineEdit* m_faceIntent{};
    QLineEdit* m_faceId{};
    QComboBox* m_continuity{};
    QDoubleSpinBox* m_expansion{};
    QLabel* m_redesignState{};
    QPushButton* m_captureRedesignButton{};
    QPushButton* m_askCodexRedesignButton{};
    QPushButton* m_applyRedesignButton{};
    QPushButton* m_modifyRedesignButton{};
    QPushButton* m_rejectRedesignButton{};
    QPushButton* m_rollbackRedesignButton{};
    QString m_redesignPath;
    QString m_previewReceiptPath;
    QString m_commitReceiptPath;
    QString m_previewIntent;
    bool m_redesignBusy{false};
    bool m_requestCodexAfterDiscard{false};
    QLineEdit* m_topologySourcePath{};
    QTableWidget* m_topologyCandidates{};
    QLineEdit* m_mechanicalSourcePath{};
    QTableWidget* m_mechanicalRequirements{};

    void chooseEvidence();
    void createGuidedSession();
    void exportBucks();
    void recordObservation();
    void requestFaceRedesign();
    void requestCodexRedesign();
    void applyRedesign();
    void modifyRedesign();
    void rejectRedesign();
    void rollbackRedesign();
    void updateRedesignActions();
};

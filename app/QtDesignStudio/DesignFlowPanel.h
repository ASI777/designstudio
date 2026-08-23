#pragma once

#include <QJsonObject>
#include <QVector>
#include <QWidget>

class QFrame;
class QLabel;
class QLineEdit;
class QPushButton;

// A visible, approval-gated control surface for the complete industrial
// product workflow.  It does not construct geometry itself; it reflects the
// typed Codex/FreeCAD operations already owned by MainWindow and gives the
// designer one place to start or resume the flow.
class DesignFlowPanel final : public QWidget {
    Q_OBJECT
public:
    explicit DesignFlowPanel(QWidget* parent = nullptr);

    void setWorkspaceState(bool open, const QString& detail = {});
    void setToolPending(const QString& tool, const QJsonObject& arguments = {});
    void setToolResult(const QString& tool, bool ok, const QString& detail = {});

signals:
    void startRequested(const QString& prompt);
    void resumeRequested(const QString& prompt);
    void openChatRequested();

private:
    struct StageView {
        QString id;
        QFrame* card{};
        QLabel* status{};
        QLabel* detail{};
    };

    QLineEdit* m_productInput{};
    QLabel* m_flowState{};
    QPushButton* m_startButton{};
    QPushButton* m_resumeButton{};
    QVector<StageView> m_stages;
    bool m_flowActive{};
    int m_currentStage{-1};

    void setStageStatus(int index, const QString& status, const QString& detail);
    int stageForTool(const QString& tool) const;
    QString flowProduct() const;
};

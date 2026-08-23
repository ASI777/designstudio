#pragma once

#include <QJsonObject>
#include <QWidget>

class QLabel;
class QTextEdit;

// Human-readable evidence surface for the typed propagation/run receipt.
// It deliberately exposes incomplete gates and rerun reasons instead of
// presenting a green result when an exact solver was not available.
class PropagationPanel final : public QWidget {
    Q_OBJECT
public:
    explicit PropagationPanel(QWidget* parent = nullptr);
    void setPending(const QString& detail);
    void setRun(const QJsonObject& result);

private:
    QLabel* m_summary{};
    QTextEdit* m_details{};
};

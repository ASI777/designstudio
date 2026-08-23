#pragma once

#include <QWidget>

#include <memory>

class QLabel;
class QTreeWidget;

namespace designstudio {
class CadAssistSession;
}

class CadAssistPanel final : public QWidget {
public:
    explicit CadAssistPanel(QWidget* parent = nullptr);

    void setSession(std::shared_ptr<const designstudio::CadAssistSession> session,
                    const QString& sourcePath);
    void clearSession(const QString& reason);

    bool hasSession() const noexcept { return session_ != nullptr; }
    QString summaryText() const;

private:
    QLabel* stateLabel_{};
    QLabel* noticeLabel_{};
    QTreeWidget* details_{};
    std::shared_ptr<const designstudio::CadAssistSession> session_;
};

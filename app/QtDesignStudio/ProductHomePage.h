#pragma once

#include <QWidget>
#include <QStringList>

class QLabel;
class QVBoxLayout;

// Focused start surface for complete product workspaces.  It intentionally has
// no loose-document examples or generic CAD workbench shortcuts.
class ProductHomePage final : public QWidget {
    Q_OBJECT
public:
    explicit ProductHomePage(QWidget* parent = nullptr);

    void setRecentProducts(const QStringList& paths);

signals:
    void newProductRequested();
    void openProductRequested();
    void importFreeCadRequested();
    void importElectronicsRequested();
    void resumeLastSessionRequested();
    void assistantPromptRequested(const QString& prompt);
    void applicationStarterRequested(const QString& prompt);
    void recentProductRequested(const QString& path);

private:
    QVBoxLayout* m_recentLayout{};
    QLabel* m_recentEmpty{};
};

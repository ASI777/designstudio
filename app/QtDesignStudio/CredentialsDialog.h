#pragma once
#include <QDialog>

class QLineEdit;
class QCheckBox;
class QComboBox;
class QLabel;

// Reads/writes ~/.config/designstudio/env so vendor API keys are
// persisted across launches. Calling accept() also calls qputenv()
// so subprocesses spawned in the same session inherit the new values
// immediately (no restart required).
class CredentialsDialog : public QDialog {
    Q_OBJECT
public:
    explicit CredentialsDialog(QWidget* parent = nullptr);

private:
    // DigiKey
    QLineEdit* m_dkId{};
    QLineEdit* m_dkSecret{};
    QCheckBox* m_dkSandbox{};

    // Mouser
    QLineEdit* m_mouserKey{};

    // Nexar (Octopart)
    QLineEdit* m_nexarId{};
    QLineEdit* m_nexarSecret{};

    // Claude / Anthropic
    QLineEdit* m_anthropicKey{};
    QCheckBox* m_forceApi{};
    QComboBox* m_advisorModel{};

    QLabel*    m_statusLabel{};

    void loadFromEnvFile();
    void saveToEnvFile();

    // Update or insert a KEY=value line in the env file content.
    // If value is empty the line is commented out.
    static void upsertKey(QStringList& lines, const QString& key, const QString& value);

    static QString envFilePath();
};

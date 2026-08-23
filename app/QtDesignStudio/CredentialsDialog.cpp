#include "CredentialsDialog.h"

#include <QTabWidget>
#include <QFormLayout>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QGroupBox>
#include <QLineEdit>
#include <QCheckBox>
#include <QComboBox>
#include <QLabel>
#include <QPushButton>
#include <QDialogButtonBox>
#include <QToolButton>
#include <QFile>
#include <QDir>
#include <QTextStream>
#include <QRegularExpression>
#include <QStandardPaths>
#include <QCoreApplication>

// ── Helpers ───────────────────────────────────────────────────────────────────

static QLineEdit* makeSecretField(QWidget* parent) {
    auto* le = new QLineEdit(parent);
    le->setEchoMode(QLineEdit::Password);
    le->setPlaceholderText("(not set)");
    return le;
}

static QWidget* wrapWithReveal(QLineEdit* field) {
    auto* w    = new QWidget;
    auto* hlay = new QHBoxLayout(w);
    hlay->setContentsMargins(0,0,0,0);
    hlay->setSpacing(2);
    hlay->addWidget(field, 1);
    auto* eye = new QToolButton(w);
    eye->setText("👁");
    eye->setCheckable(true);
    eye->setFixedWidth(28);
    eye->setStyleSheet("QToolButton{border:none;background:transparent;font-size:14px;}"
                       "QToolButton:checked{color:#58a6ff;}");
    QObject::connect(eye, &QToolButton::toggled, field, [field](bool v){
        field->setEchoMode(v ? QLineEdit::Normal : QLineEdit::Password);
    });
    hlay->addWidget(eye);
    return w;
}

// ── Constructor ───────────────────────────────────────────────────────────────

CredentialsDialog::CredentialsDialog(QWidget* parent) : QDialog(parent) {
    setWindowTitle("API Credentials");
    setMinimumWidth(480);

    auto* vbox = new QVBoxLayout(this);

    auto* tabs = new QTabWidget(this);

    // ── DigiKey tab ──────────────────────────────────────────────────────────
    auto* dkWidget = new QWidget;
    auto* dkForm   = new QFormLayout(dkWidget);
    dkForm->setFieldGrowthPolicy(QFormLayout::ExpandingFieldsGrow);
    dkForm->setSpacing(8);
    dkForm->setContentsMargins(12,12,12,12);

    m_dkId     = new QLineEdit; m_dkId->setPlaceholderText("(not set)");
    m_dkSecret = makeSecretField(this);
    m_dkSandbox= new QCheckBox("Use sandbox catalog (sandbox-api.digikey.com)");

    dkForm->addRow("Client ID:", m_dkId);
    dkForm->addRow("Client Secret:", wrapWithReveal(m_dkSecret));
    dkForm->addRow("", m_dkSandbox);

    auto* dkNote = new QLabel(
        "<a href='https://developer.digikey.com/'>Register at developer.digikey.com</a>"
        " → create an app → Production → OAuth 2.0 → Client Credentials.");
    dkNote->setOpenExternalLinks(true);
    dkNote->setWordWrap(true);
    dkNote->setStyleSheet("color:#8b949e;font-size:11px;");
    dkForm->addRow("", dkNote);

    tabs->addTab(dkWidget, "DigiKey");

    // ── Mouser / Nexar tab ───────────────────────────────────────────────────
    auto* mvWidget = new QWidget;
    auto* mvForm   = new QFormLayout(mvWidget);
    mvForm->setFieldGrowthPolicy(QFormLayout::ExpandingFieldsGrow);
    mvForm->setSpacing(8);
    mvForm->setContentsMargins(12,12,12,12);

    m_mouserKey   = makeSecretField(this);
    m_nexarId     = new QLineEdit; m_nexarId->setPlaceholderText("(not set)");
    m_nexarSecret = makeSecretField(this);

    auto* mouserNote = new QLabel(
        "<a href='https://www.mouser.com/api/'>Register at mouser.com/api</a>"
        " for a Mouser Search API key.");
    mouserNote->setOpenExternalLinks(true);
    mouserNote->setWordWrap(true);
    mouserNote->setStyleSheet("color:#8b949e;font-size:11px;");

    auto* nexarNote = new QLabel(
        "<a href='https://nexar.com/api'>Register at nexar.com/api</a>"
        " (Octopart GraphQL) for multi-vendor normalized data.");
    nexarNote->setOpenExternalLinks(true);
    nexarNote->setWordWrap(true);
    nexarNote->setStyleSheet("color:#8b949e;font-size:11px;");

    mvForm->addRow("Mouser API Key:", wrapWithReveal(m_mouserKey));
    mvForm->addRow("", mouserNote);
    mvForm->addRow("Nexar Client ID:", m_nexarId);
    mvForm->addRow("Nexar Client Secret:", wrapWithReveal(m_nexarSecret));
    mvForm->addRow("", nexarNote);

    tabs->addTab(mvWidget, "Mouser / Nexar");

    // ── Codex-powered Design Assistant and footprint extraction ─────────────
    auto* extractionWidget = new QWidget;
    auto* extractionForm = new QFormLayout(extractionWidget);
    extractionForm->setFieldGrowthPolicy(QFormLayout::ExpandingFieldsGrow);
    extractionForm->setSpacing(8);
    extractionForm->setContentsMargins(12,12,12,12);

    auto* extractionNote = new QLabel(
        "The Design Assistant and controlled datasheet extractor reuse your "
        "local Codex CLI login. Authenticate once by running <tt>codex login</tt>. "
        "Downloaded PDFs are hashed and generated footprints still require "
        "visual review before PCB placement.");
    extractionNote->setWordWrap(true);
    extractionNote->setStyleSheet("color:#8b949e;font-size:11px;");
    extractionForm->addRow("Model:", new QLabel("gpt-5.6-luna"));
    extractionForm->addRow("Reasoning:", new QLabel("Extra High (xhigh)"));
    extractionForm->addRow("Authentication:", new QLabel("Codex CLI login"));
    extractionForm->addRow("", extractionNote);
    tabs->addTab(extractionWidget, "Codex");

    // ── Claude / Anthropic tab ───────────────────────────────────────────────
    auto* aiWidget = new QWidget;
    auto* aiForm   = new QFormLayout(aiWidget);
    aiForm->setFieldGrowthPolicy(QFormLayout::ExpandingFieldsGrow);
    aiForm->setSpacing(8);
    aiForm->setContentsMargins(12,12,12,12);

    m_anthropicKey = makeSecretField(this);
    m_forceApi     = new QCheckBox("Force Anthropic API (skip Claude CLI)");
    m_advisorModel = new QComboBox;
    m_advisorModel->addItems({
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    });

    auto* aiNote = new QLabel(
        "The AI chat works without a key if you are logged in via the Claude CLI "
        "(<tt>claude</tt>). An API key is only needed when using DS_FORCE_API or "
        "running without the CLI.");
    aiNote->setWordWrap(true);
    aiNote->setStyleSheet("color:#8b949e;font-size:11px;");

    aiForm->addRow("Anthropic API Key:", wrapWithReveal(m_anthropicKey));
    aiForm->addRow("", m_forceApi);
    aiForm->addRow("Advisor model:", m_advisorModel);
    aiForm->addRow("", aiNote);

    tabs->addTab(aiWidget, "Claude / AI");

    vbox->addWidget(tabs);

    // ── Status label ─────────────────────────────────────────────────────────
    m_statusLabel = new QLabel;
    m_statusLabel->setStyleSheet("color:#3fb950;font-size:11px;padding:2px;");
    vbox->addWidget(m_statusLabel);

    // ── Buttons ───────────────────────────────────────────────────────────────
    auto* buttons = new QDialogButtonBox(
        QDialogButtonBox::Ok | QDialogButtonBox::Cancel, this);
    vbox->addWidget(buttons);
    connect(buttons, &QDialogButtonBox::accepted, this, [this]{
        saveToEnvFile();
        accept();
    });
    connect(buttons, &QDialogButtonBox::rejected, this, &QDialog::reject);

    loadFromEnvFile();
}

// ── env file path ─────────────────────────────────────────────────────────────

QString CredentialsDialog::envFilePath() {
    const QString configuredRoot = qEnvironmentVariable("XDG_CONFIG_HOME");
    const QString root = configuredRoot.isEmpty()
        ? QDir::homePath() + QStringLiteral("/.config") : configuredRoot;
    return QDir(root).filePath(QStringLiteral("designstudio/env"));
}

// ── Load ──────────────────────────────────────────────────────────────────────

static QString readEnvKey(const QStringList& lines, const QString& key) {
    // Matches both "KEY=value" and "# KEY=value" (commented-out).
    // Returns the value if the key is active (not commented), empty string otherwise.
    QRegularExpression active(QString("^%1=(.*)$").arg(QRegularExpression::escape(key)));
    for (const QString& line : lines) {
        auto m = active.match(line.trimmed());
        if (m.hasMatch()) {
            QString v = m.captured(1).trimmed();
            // Strip surrounding quotes
            if ((v.startsWith('"') && v.endsWith('"')) ||
                (v.startsWith('\'') && v.endsWith('\'')))
                v = v.mid(1, v.length() - 2);
            return v;
        }
    }
    return {};
}

void CredentialsDialog::loadFromEnvFile() {
    QFile f(envFilePath());
    if (!f.open(QIODevice::ReadOnly | QIODevice::Text)) return;

    QStringList lines;
    QTextStream in(&f);
    while (!in.atEnd())
        lines << in.readLine();

    m_dkId->setText(readEnvKey(lines, "DIGIKEY_CLIENT_ID"));
    m_dkSecret->setText(readEnvKey(lines, "DIGIKEY_CLIENT_SECRET"));
    m_dkSandbox->setChecked(readEnvKey(lines, "DIGIKEY_SANDBOX") == "1");
    m_mouserKey->setText(readEnvKey(lines, "MOUSER_API_KEY"));
    m_nexarId->setText(readEnvKey(lines, "NEXAR_CLIENT_ID"));
    m_nexarSecret->setText(readEnvKey(lines, "NEXAR_CLIENT_SECRET"));
    m_anthropicKey->setText(readEnvKey(lines, "ANTHROPIC_API_KEY"));
    m_forceApi->setChecked(readEnvKey(lines, "DS_FORCE_API") == "1");

    QString model = readEnvKey(lines, "ADVISOR_MODEL");
    int idx = m_advisorModel->findText(model);
    if (idx >= 0) m_advisorModel->setCurrentIndex(idx);
}

// ── Save ──────────────────────────────────────────────────────────────────────

// Uncomments and sets, or appends, a KEY=value line. Empty value = comment out.
void CredentialsDialog::upsertKey(QStringList& lines, const QString& key, const QString& value) {
    // Match active line: KEY=...
    QRegularExpression active(QString("^(%1=)(.*)$").arg(QRegularExpression::escape(key)));
    // Match commented line: # KEY=... or #KEY=...
    QRegularExpression commented(QString("^#\\s*(%1=)(.*)$").arg(QRegularExpression::escape(key)));

    for (QString& line : lines) {
        if (active.match(line).hasMatch() || commented.match(line).hasMatch()) {
            line = value.isEmpty()
                ? QString("# %1=").arg(key)
                : QString("%1='%2'").arg(key, value);
            return;
        }
    }
    // Key not found anywhere — append it.
    if (!value.isEmpty())
        lines << QString("%1='%2'").arg(key, value);
}

void CredentialsDialog::saveToEnvFile() {
    // Ensure the directory exists, creating the file from a template if absent.
    QString path = envFilePath();
    QDir().mkpath(QFileInfo(path).absolutePath());
    const QRegularExpression safeValue(QStringLiteral("^[A-Za-z0-9_./:+~=,@%\\-]*$"));
    const QStringList credentialValues{
        m_dkId->text().trimmed(), m_dkSecret->text().trimmed(),
        m_mouserKey->text().trimmed(), m_nexarId->text().trimmed(),
        m_nexarSecret->text().trimmed(),
        m_anthropicKey->text().trimmed()
    };
    for (const QString& value : credentialValues) {
        if (!safeValue.match(value).hasMatch()) {
            m_statusLabel->setText("Error: credentials contain unsupported shell characters");
            m_statusLabel->setStyleSheet("color:#f85149;font-size:11px;padding:2px;");
            return;
        }
    }

    QStringList lines;
    {
        QFile f(path);
        if (f.open(QIODevice::ReadOnly | QIODevice::Text)) {
            QTextStream in(&f);
            while (!in.atEnd())
                lines << in.readLine();
        } else {
            lines << "# DesignStudio environment — managed by DesignStudio (API Credentials dialog)"
                  << "# You can also edit this file directly; run.sh sources it at launch."
                  << "";
        }
    }

    upsertKey(lines, "DIGIKEY_CLIENT_ID",     m_dkId->text().trimmed());
    upsertKey(lines, "DIGIKEY_CLIENT_SECRET",  m_dkSecret->text().trimmed());
    upsertKey(lines, "DIGIKEY_SANDBOX",        m_dkSandbox->isChecked() ? "1" : "");
    upsertKey(lines, "MOUSER_API_KEY",         m_mouserKey->text().trimmed());
    upsertKey(lines, "NEXAR_CLIENT_ID",        m_nexarId->text().trimmed());
    upsertKey(lines, "NEXAR_CLIENT_SECRET",    m_nexarSecret->text().trimmed());
    upsertKey(lines, "ANTHROPIC_API_KEY",      m_anthropicKey->text().trimmed());
    upsertKey(lines, "DS_FORCE_API",           m_forceApi->isChecked() ? "1" : "");
    upsertKey(lines, "ADVISOR_MODEL",
              m_advisorModel->currentText() == "claude-opus-4-8"
                  ? "" : m_advisorModel->currentText());

    QFile out(path);
    if (!out.open(QIODevice::WriteOnly | QIODevice::Text)) {
        m_statusLabel->setText("Error: could not write to " + path);
        m_statusLabel->setStyleSheet("color:#f85149;font-size:11px;padding:2px;");
        return;
    }
    if (!out.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner)) {
        out.close();
        m_statusLabel->setText("Error: could not secure credential file permissions");
        m_statusLabel->setStyleSheet("color:#f85149;font-size:11px;padding:2px;");
        return;
    }
    QTextStream s(&out);
    for (const QString& line : lines)
        s << line << "\n";
    s.flush();
    out.close();
    if (!QFile::setPermissions(path, QFileDevice::ReadOwner | QFileDevice::WriteOwner)) {
        m_statusLabel->setText("Error: could not secure credential file permissions");
        m_statusLabel->setStyleSheet("color:#f85149;font-size:11px;padding:2px;");
        return;
    }

    // Push into this process's environment so child processes inherit immediately.
    auto setOrUnset = [](const char* key, const QString& val) {
        if (val.isEmpty()) qunsetenv(key);
        else               qputenv(key, val.toUtf8());
    };
    setOrUnset("DIGIKEY_CLIENT_ID",    m_dkId->text().trimmed());
    setOrUnset("DIGIKEY_CLIENT_SECRET",m_dkSecret->text().trimmed());
    setOrUnset("DIGIKEY_SANDBOX",      m_dkSandbox->isChecked() ? "1" : "");
    setOrUnset("MOUSER_API_KEY",       m_mouserKey->text().trimmed());
    setOrUnset("NEXAR_CLIENT_ID",      m_nexarId->text().trimmed());
    setOrUnset("NEXAR_CLIENT_SECRET",  m_nexarSecret->text().trimmed());
    setOrUnset("ANTHROPIC_API_KEY",    m_anthropicKey->text().trimmed());
    setOrUnset("DS_FORCE_API",         m_forceApi->isChecked() ? "1" : "");
    if (m_advisorModel->currentText() != "claude-opus-4-8")
        qputenv("ADVISOR_MODEL", m_advisorModel->currentText().toUtf8());
    else
        qunsetenv("ADVISOR_MODEL");

    m_statusLabel->setText("Saved to " + path);
}

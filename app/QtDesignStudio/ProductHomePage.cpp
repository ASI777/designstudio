#include "ProductHomePage.h"

#include <QFileInfo>
#include <QFrame>
#include <QGridLayout>
#include <QHBoxLayout>
#include <QLabel>
#include <QList>
#include <QLineEdit>
#include <QPair>
#include <QPushButton>
#include <QScrollArea>
#include <QStringList>
#include <QVBoxLayout>

namespace {
QPushButton* homeAction(const QString& title, const QString& detail, QWidget* parent) {
    auto* button = new QPushButton(title + QStringLiteral("\n") + detail, parent);
    button->setMinimumHeight(66);
    button->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
    return button;
}
}

ProductHomePage::ProductHomePage(QWidget* parent) : QWidget(parent) {
    setObjectName(QStringLiteral("designstudio_product_home"));
    auto* root = new QVBoxLayout(this);
    root->setContentsMargins(28, 24, 28, 24);
    root->setSpacing(16);

    auto* title = new QLabel(QStringLiteral("DesignStudio Product Home"), this);
    QFont titleFont = title->font();
    titleFont.setPointSize(titleFont.pointSize() + 6);
    titleFont.setBold(true);
    title->setFont(titleFont);
    root->addWidget(title);
    auto* subtitle = new QLabel(
        QStringLiteral("Create or resume one workspace containing mechanical, schematic, PCB, contracts and evidence."),
        this);
    subtitle->setWordWrap(true);
    root->addWidget(subtitle);

    auto* industrialFlow = homeAction(
        QStringLiteral("Start Industrial Design Flow"),
        QStringLiteral("Guide intent → requirements → CAD → electronics → harness → verification → release"),
        this);
    industrialFlow->setObjectName(QStringLiteral("product_home_start_industrial_flow"));
    industrialFlow->setMinimumHeight(74);
    industrialFlow->setStyleSheet(QStringLiteral(
        "QPushButton{background:#27394A;border:1px solid #4E86B4;color:#F0F6FC;"
        "border-radius:7px;text-align:left;padding:10px 14px;font-weight:700;}"
        "QPushButton:hover{background:#31516B;border-color:#78B7E5;}"));
    root->addWidget(industrialFlow);
    connect(industrialFlow, &QPushButton::clicked, this, [this] {
        emit applicationStarterRequested(QStringLiteral(
            "Start the automatic DesignStudio industrial design flow. Ask for the product idea, "
            "then guide intent/evidence, requirements, concept architecture, mechanical CAD, exact electronics, "
            "PCB and harness integration, verification, authoritative drawings, and manufacturing release in order. "
            "Keep unknowns explicit and require approval before every mutation."));
    });

    auto* actions = new QGridLayout;
    auto* newProduct = homeAction(QStringLiteral("New Product"),
                                  QStringLiteral("Create a complete .dsworkspace"), this);
    auto* openProduct = homeAction(QStringLiteral("Open Product"),
                                   QStringLiteral("Open a workspace or manifest"), this);
    auto* importCad = homeAction(QStringLiteral("Import Existing FreeCAD Project"),
                                QStringLiteral("Migrate an .FCStd document"), this);
    auto* importElectronics = homeAction(QStringLiteral("Import Existing Electronics Project"),
                                        QStringLiteral("Migrate an existing .dsproj"), this);
    auto* resume = homeAction(QStringLiteral("Resume Last Session"),
                              QStringLiteral("Return to the most recent product"), this);
    actions->addWidget(newProduct, 0, 0);
    actions->addWidget(openProduct, 0, 1);
    actions->addWidget(importCad, 1, 0);
    actions->addWidget(importElectronics, 1, 1);
    actions->addWidget(resume, 2, 0, 1, 2);
    root->addLayout(actions);

    connect(newProduct, &QPushButton::clicked, this, &ProductHomePage::newProductRequested);
    connect(openProduct, &QPushButton::clicked, this, &ProductHomePage::openProductRequested);
    connect(importCad, &QPushButton::clicked, this, &ProductHomePage::importFreeCadRequested);
    connect(importElectronics, &QPushButton::clicked, this,
            &ProductHomePage::importElectronicsRequested);
    connect(resume, &QPushButton::clicked, this, &ProductHomePage::resumeLastSessionRequested);

    auto* promptRow = new QHBoxLayout;
    auto* prompt = new QLineEdit(this);
    prompt->setPlaceholderText(QStringLiteral("Describe the product you want to design…"));
    auto* ask = new QPushButton(QStringLiteral("Open Codex"), this);
    promptRow->addWidget(prompt, 1);
    promptRow->addWidget(ask);
    root->addLayout(promptRow);
    auto submitPrompt = [this, prompt] {
        const QString text = prompt->text().trimmed();
        if (!text.isEmpty()) emit assistantPromptRequested(text);
    };
    connect(ask, &QPushButton::clicked, this, submitPrompt);
    connect(prompt, &QLineEdit::returnPressed, this, submitPrompt);

    auto* starterTitle = new QLabel(QStringLiteral("Start with a product idea"), this);
    QFont starterFont = starterTitle->font();
    starterFont.setBold(true);
    starterTitle->setFont(starterFont);
    root->addWidget(starterTitle);
    auto* starters = new QGridLayout;
    const QList<QPair<QString, QString>> starterPrompts{
        {QStringLiteral("Synchronous buck converter"), QStringLiteral("Build a synchronous buck converter")},
        {QStringLiteral("Precision acquisition board"), QStringLiteral("I need a precision acquisition board")},
        {QStringLiteral("2.4 GHz wireless sensor"), QStringLiteral("I need a wireless temperature sensor")},
        {QStringLiteral("USB/Gigabit high-speed board"), QStringLiteral("Design a USB/Gigabit high-speed board")},
        {QStringLiteral("BLDC servo controller"), QStringLiteral("Design a controller for a small BLDC servo")},
        {QStringLiteral("Industrial condition monitor"), QStringLiteral("Build an industrial condition monitor")},
        {QStringLiteral("Robotic joint capstone"), QStringLiteral("Design a controller for a small robot joint")},
    };
    for (int i = 0; i < starterPrompts.size(); ++i) {
        auto* button = homeAction(starterPrompts.at(i).first, QStringLiteral("Start a guided configuration"), this);
        starters->addWidget(button, i / 2, i % 2);
        connect(button, &QPushButton::clicked, this, [this, prompt = starterPrompts.at(i).second] {
            emit applicationStarterRequested(prompt);
        });
    }
    root->addLayout(starters);

    auto* recentTitle = new QLabel(QStringLiteral("Recent Products"), this);
    QFont recentFont = recentTitle->font();
    recentFont.setBold(true);
    recentTitle->setFont(recentFont);
    root->addWidget(recentTitle);
    auto* recentHost = new QWidget(this);
    m_recentLayout = new QVBoxLayout(recentHost);
    m_recentLayout->setContentsMargins(0, 0, 0, 0);
    m_recentEmpty = new QLabel(QStringLiteral("No recent products yet."), recentHost);
    m_recentLayout->addWidget(m_recentEmpty);
    m_recentLayout->addStretch(1);
    root->addWidget(recentHost, 1);
}

void ProductHomePage::setRecentProducts(const QStringList& paths) {
    while (m_recentLayout->count() > 1) {
        QLayoutItem* item = m_recentLayout->takeAt(0);
        delete item->widget();
        delete item;
    }
    if (paths.isEmpty()) {
        m_recentEmpty = new QLabel(QStringLiteral("No recent products yet."), this);
        m_recentLayout->insertWidget(0, m_recentEmpty);
        return;
    }
    m_recentEmpty = nullptr;
    for (const QString& path : paths) {
        auto* button = new QPushButton(
            QStringLiteral("%1\n%2").arg(QFileInfo(path).completeBaseName(), path), this);
        button->setToolTip(path);
        connect(button, &QPushButton::clicked, this,
                [this, path] { emit recentProductRequested(path); });
        m_recentLayout->insertWidget(m_recentLayout->count() - 1, button);
    }
}

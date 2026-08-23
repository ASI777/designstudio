#include "AgentActivityPanel.h"

#include <QListWidget>
#include <QListWidgetItem>
#include <QLabel>
#include <QToolButton>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QFont>
#include <algorithm>

AgentActivityPanel::AgentActivityPanel(QWidget* parent) : QWidget(parent) {
    setMinimumWidth(280);

    auto* vbox = new QVBoxLayout(this);
    vbox->setContentsMargins(0,0,0,0);
    vbox->setSpacing(0);

    // ── Header ────────────────────────────────────────────────────────────────
    auto* header = new QWidget;
    header->setStyleSheet("background:#161b22;border-bottom:1px solid #30363d;");
    auto* hlay = new QHBoxLayout(header);
    hlay->setContentsMargins(10,6,8,6);

    auto* title = new QLabel("Agent Activity");
    title->setStyleSheet("color:#e6edf3;font-weight:600;font-size:12px;");

    m_countLabel = new QLabel("0 runs");
    m_countLabel->setStyleSheet("color:#8b949e;font-size:11px;");

    m_clearBtn = new QToolButton;
    m_clearBtn->setText("Clear");
    m_clearBtn->setStyleSheet("QToolButton{background:transparent;border:none;"
                               "color:#8b949e;font-size:11px;}"
                               "QToolButton:hover{color:#c9d1d9;}");
    connect(m_clearBtn, &QToolButton::clicked, this, &AgentActivityPanel::onClear);

    hlay->addWidget(title);
    hlay->addStretch();
    hlay->addWidget(m_countLabel);
    hlay->addWidget(m_clearBtn);
    vbox->addWidget(header);

    // ── List ──────────────────────────────────────────────────────────────────
    m_list = new QListWidget;
    m_list->setWordWrap(true);
    m_list->setSpacing(1);
    m_list->setStyleSheet(
        "QListWidget{background:#0d1117;border:none;padding:2px;}"
        "QListWidget::item{background:transparent;padding:3px 6px;"
        "color:#c9d1d9;font-size:11px;}"
        "QListWidget::item:hover{background:#161b22;}"
        "QListWidget::item:selected{background:#1c2128;border:none;}"
    );
    connect(m_list, &QListWidget::itemClicked, this, &AgentActivityPanel::onItemExpand);
    vbox->addWidget(m_list, 1);

    // ── Legend ────────────────────────────────────────────────────────────────
    auto* legend = new QLabel("● Running  ✓ Done  ✗ Failed  — click to expand");
    legend->setStyleSheet("color:#484f58;font-size:10px;padding:3px 8px;"
                          "border-top:1px solid #21262d;background:#0d1117;");
    vbox->addWidget(legend);
}

// ── Slot: agent started ───────────────────────────────────────────────────────
void AgentActivityPanel::onAgentStarted(const QString& name) {
    auto* run = new AgentRun;
    run->name    = name;
    run->state   = AgentRun::Running;
    run->started = QDateTime::currentDateTime();

    m_runs.insert(name, run);
    m_totalRuns++;

    auto* item = new QListWidgetItem;
    item->setData(Qt::UserRole, name);
    m_list->insertItem(0, item);
    run->item = item;

    updateItem(run);
}

// ── Slot: line of output ──────────────────────────────────────────────────────
void AgentActivityPanel::onAgentOutput(const QString& name, const QString& text) {
    auto* run = currentRun(name);
    if (!run) return;

    // Split multiline output into individual log lines
    for (const QString& line : text.split('\n')) {
        QString trimmed = line.trimmed();
        if (!trimmed.isEmpty())
            run->lines.append(trimmed);
    }
    // Show only last 3 lines in the item label (full log on click)
    updateItem(run);
}

// ── Slot: agent finished ──────────────────────────────────────────────────────
void AgentActivityPanel::onAgentFinished(const QString& name, bool ok) {
    auto* run = currentRun(name);
    if (!run) return;

    run->state = ok ? AgentRun::Done : AgentRun::Failed;
    run->ended = QDateTime::currentDateTime();
    if (!ok) m_failed++;

    updateItem(run);

    auto vals = m_runs.values();
    int running = (int)std::count_if(vals.begin(), vals.end(),
        [](AgentRun* r){ return r->state == AgentRun::Running; });
    (void)running;
    m_countLabel->setText(QString("%1 runs%2")
        .arg(m_totalRuns)
        .arg(m_failed > 0 ? QString(", %1 failed").arg(m_failed) : ""));
}

// ── Item rendering ────────────────────────────────────────────────────────────
void AgentActivityPanel::updateItem(AgentRun* run) {
    if (!run->item) return;

    QString icon = stateIcon(run->state);
    QString elapsed = elapsedStr(run);

    // Headline
    QString text = icon + " " + run->name + "  " +
                   "<span style='color:#484f58'>" + elapsed + "</span>";

    // Last 3 output lines (truncated)
    int startLine = qMax(0, run->lines.size() - 3);
    for (int i = startLine; i < run->lines.size(); ++i) {
        QString l = run->lines[i];
        if (l.length() > 90) l = l.left(87) + "…";
        QString color = "#484f58";
        if (l.contains("error", Qt::CaseInsensitive) || l.contains("failed", Qt::CaseInsensitive))
            color = "#f85149";
        else if (l.contains("critical", Qt::CaseInsensitive) || l.contains("warning", Qt::CaseInsensitive))
            color = "#d29922";
        else if (l.contains("complete") || l.contains("passed") || l.contains("PASS"))
            color = "#3fb950";
        text += "<br><span style='color:" + color + ";font-family:monospace;'>"
                + l.toHtmlEscaped() + "</span>";
    }

    run->item->setText(text);

    // Color code the list item background
    if (run->state == AgentRun::Running)
        run->item->setBackground(QColor("#0d1e3d"));
    else if (run->state == AgentRun::Done)
        run->item->setBackground(QColor("#0d1a0d"));
    else
        run->item->setBackground(QColor("#2d1111"));
}

void AgentActivityPanel::onItemExpand(QListWidgetItem* item) {
    QString name = item->data(Qt::UserRole).toString();
    auto* run = m_runs.value(name, nullptr);
    if (!run) return;

    // Toggle full log display
    QString icon = stateIcon(run->state);
    QString elapsed = elapsedStr(run);
    QString text = icon + " " + run->name + "  "
                   + "<span style='color:#484f58'>" + elapsed + "</span>";

    // Show all lines when clicked
    for (const QString& l : run->lines) {
        QString safe = l.toHtmlEscaped();
        if (safe.length() > 120) safe = safe.left(117) + "…";
        QString color = "#6e7681";
        if (l.contains("error", Qt::CaseInsensitive) || l.contains("failed"))
            color = "#f85149";
        else if (l.contains("complete") || l.contains("PASS"))
            color = "#3fb950";
        text += "<br><span style='color:" + color + ";font-family:monospace;font-size:10px;'>"
                + safe + "</span>";
    }
    item->setText(text);
}

void AgentActivityPanel::onClear() {
    for (auto* run : m_runs.values())
        if (run->state == AgentRun::Running) return;  // don't clear while running

    m_list->clear();
    qDeleteAll(m_runs);
    m_runs.clear();
    m_totalRuns = 0;
    m_failed    = 0;
    m_countLabel->setText("0 runs");
}

// ── Helpers ───────────────────────────────────────────────────────────────────
AgentRun* AgentActivityPanel::currentRun(const QString& name) {
    return m_runs.value(name, nullptr);
}

QString AgentActivityPanel::elapsedStr(const AgentRun* run) const {
    if (run->state == AgentRun::Running) {
        int secs = run->started.secsTo(QDateTime::currentDateTime());
        return QString("%1s…").arg(secs);
    }
    int ms = (int)run->started.msecsTo(run->ended);
    if (ms < 1000) return QString("%1ms").arg(ms);
    return QString("%1.%2s").arg(ms/1000).arg((ms%1000)/100);
}

QString AgentActivityPanel::stateIcon(AgentRun::State s) {
    switch (s) {
    case AgentRun::Running: return "<span style='color:#1f6feb'>●</span>";
    case AgentRun::Done:    return "<span style='color:#3fb950'>✓</span>";
    case AgentRun::Failed:  return "<span style='color:#f85149'>✗</span>";
    }
    return "?";
}

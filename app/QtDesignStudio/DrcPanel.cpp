#include "DrcPanel.h"
#include "ProjectModel.h"
#include <QTreeWidget>
#include <QTreeWidgetItem>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QLabel>
#include <QPushButton>
#include <QHeaderView>
#include <QtConcurrent/QtConcurrentRun>
#include <QFutureWatcher>
#include <QFileDialog>
#include <QSaveFile>
#include <QJsonDocument>
#include <QMessageBox>
#include <QTimer>

DrcPanel::DrcPanel(ProjectModel* model, CoreBridge* core, QWidget* parent)
    : QWidget(parent), m_model(model), m_core(core)
{
    auto* vbox = new QVBoxLayout(this);
    vbox->setContentsMargins(8,8,8,8);
    vbox->setSpacing(6);

    auto* header = new QHBoxLayout;
    auto* title = new QLabel("Continuous Verification", this);
    title->setProperty("heading","true");
    m_runBtn = new QPushButton("Run Verification", this);
    m_runBtn->setProperty("primary","true");
    m_runBtn->setFixedHeight(28);
    m_exportBtn = new QPushButton("Export Verification…", this);
    m_exportBtn->setFixedHeight(28);
    header->addWidget(title);
    header->addStretch();
    header->addWidget(m_exportBtn);
    header->addWidget(m_runBtn);
    vbox->addLayout(header);

    m_summary = new QLabel("No DRC run yet", this);
    m_summary->setProperty("muted","true");
    vbox->addWidget(m_summary);

    m_tree = new QTreeWidget(this);
    m_tree->setColumnCount(3);
    m_tree->setHeaderLabels({"Sev","Rule","Description"});
    m_tree->header()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    m_tree->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    m_tree->header()->setSectionResizeMode(2, QHeaderView::Stretch);
    m_tree->setAlternatingRowColors(true);
    m_tree->setRootIsDecorated(false);
    vbox->addWidget(m_tree, 1);

    connect(m_runBtn, &QPushButton::clicked, this, &DrcPanel::runDrc);
    connect(m_exportBtn, &QPushButton::clicked, this, &DrcPanel::exportVerification);

    connect(m_tree, &QTreeWidget::itemActivated, this, [this](QTreeWidgetItem* item){
        double x = item->data(0, Qt::UserRole+1).toDouble();
        double y = item->data(0, Qt::UserRole+2).toDouble();
        if (x || y) emit violationSelected(x, y);
    });

    m_autoTimer = new QTimer(this);
    m_autoTimer->setSingleShot(true);
    m_autoTimer->setInterval(400);
    connect(m_autoTimer, &QTimer::timeout, this, &DrcPanel::runDrc);
    connect(m_model, &ProjectModel::modified, this, [this]{ m_autoTimer->start(); });
    connect(m_model, &ProjectModel::loaded, this, [this]{ m_autoTimer->start(); });
}

DrcPanel::~DrcPanel() {
    m_autoTimer->stop();
    if (m_watcher) {
        disconnect(m_watcher, nullptr, this, nullptr);
        m_watcher->future().waitForFinished();
    }
}

void DrcPanel::exportVerification() {
    if (!(m_core && m_core->isLoaded())) {
        QMessageBox::critical(this, "Verification unavailable",
            "The native verification engine is unavailable; no sign-off report was created.");
        return;
    }
    const QString path = QFileDialog::getSaveFileName(this, "Export verification report",
        QStringLiteral("design-verification.json"), "JSON (*.json)");
    if (path.isEmpty()) return;
    const QJsonObject report = m_core->buildVerificationReport(m_model);
    QSaveFile file(path);
    if (!file.open(QIODevice::WriteOnly)
        || file.write(QJsonDocument(report).toJson(QJsonDocument::Indented)) < 0
        || !file.commit()) {
        QMessageBox::critical(this, "Export failed", "Could not write the verification report.");
        return;
    }
    const QString status = report.value("overall_status").toString();
    QMessageBox::information(this, "Verification exported",
        QString("Report status: %1\n%2").arg(status.toUpper(), path));
}

void DrcPanel::runDrc() {
    if (m_running) {
        m_pendingRerun = true;
        return;
    }

    // Offline path is cheap — keep it synchronous.
    if (!(m_core && m_core->isLoaded())) {
        populate(runOfflineChecks());
        return;
    }

    // Native DRC on a dense board can take seconds; run it off the UI thread so
    // the window stays responsive (same pattern as auto-route in MainWindow).
    m_running = true;
    m_runBtn->setEnabled(false);
    m_runBtn->setText("Running…");

    CoreBridge*   core  = m_core;
    ProjectModel* model = m_model;
    auto* watcher = new QFutureWatcher<QJsonObject>(this);
    m_watcher = watcher;
    connect(watcher, &QFutureWatcher<QJsonObject>::finished, this,
            [this, watcher]{
        populateVerification(watcher->result());
        m_runBtn->setEnabled(true);
        m_runBtn->setText("Run Verification");
        m_running = false;
        m_watcher = nullptr;
        watcher->deleteLater();
        if (m_pendingRerun) {
            m_pendingRerun = false;
            m_autoTimer->start(0);
        }
    });
    watcher->setFuture(QtConcurrent::run([core, model]() -> QJsonObject {
        return core->buildVerificationReport(model);
    }));
}

QVector<DrcViolation> DrcPanel::runOfflineChecks() const {
    QVector<DrcViolation> violations;
    // Check for footprints outside board
    for (auto& fp : m_model->footprints) {
        if (!m_model->isFootprintInsideBoard(fp, fp.x_mm, fp.y_mm, fp.rotDeg)) {
            DrcViolation v;
            v.severity    = DrcViolation::Error;
            v.rule        = "BOARD_EDGE";
            v.description = fp.ref + " footprint/courtyard is outside the PCB substrate";
            v.x_mm = fp.x_mm; v.y_mm = fp.y_mm;
            violations.append(v);
        }
    }
    // Check for traces with no net
    for (auto& t : m_model->traces) {
        if (t.netId < 0) {
            DrcViolation v;
            v.severity = DrcViolation::Warning;
            v.rule     = "UNASSIGNED_NET";
            v.description = "Trace segment has no net assigned";
            v.x_mm = t.ax_mm; v.y_mm = t.ay_mm;
            violations.append(v);
        }
    }
    if (violations.isEmpty()) {
        DrcViolation unavailable;
        unavailable.severity = DrcViolation::Error;
        unavailable.rule = "DRC_ENGINE_UNAVAILABLE";
        unavailable.description = "Native DRC engine is unavailable; manufacturing validation cannot pass.";
        violations.append(unavailable);
    }
    return violations;
}

void DrcPanel::clearResults() {
    m_tree->clear();
    m_summary->setText("No DRC run yet");
}

void DrcPanel::populate(const QVector<DrcViolation>& violations) {
    m_tree->clear();
    int errors = 0, warnings = 0;
    for (auto& v : violations) {
        auto* item = new QTreeWidgetItem(m_tree);
        QString sevText;
        QString color;
        if (v.severity == DrcViolation::Error)   { sevText="ERR";  color="#f85149"; ++errors; }
        else if (v.severity==DrcViolation::Warning){ sevText="WARN"; color="#d29922"; ++warnings; }
        else                                      { sevText="INFO"; color="#58a6ff"; }
        item->setText(0, sevText);
        item->setText(1, v.rule);
        item->setText(2, v.description);
        item->setForeground(0, QColor(color));
        item->setData(0, Qt::UserRole+1, v.x_mm);
        item->setData(0, Qt::UserRole+2, v.y_mm);
    }

    if (errors == 0 && warnings == 0) {
        m_summary->setText("DRC passed — no violations");
        m_summary->setStyleSheet("color:#3fb950;");
    } else {
        m_summary->setText(QString("%1 error%2, %3 warning%4")
            .arg(errors).arg(errors==1?"":"s")
            .arg(warnings).arg(warnings==1?"":"s"));
        m_summary->setStyleSheet(errors > 0 ? "color:#f85149;" : "color:#d29922;");
    }
}

void DrcPanel::populateVerification(const QJsonObject& report) {
    m_tree->clear();
    int errors = 0, warnings = 0, incomplete = 0;
    const QJsonObject categories = report.value("categories").toObject();
    for (auto categoryIt = categories.begin(); categoryIt != categories.end(); ++categoryIt) {
        const QString categoryName = categoryIt.key();
        const QJsonObject category = categoryIt.value().toObject();
        const QString status = category.value("status").toString("incomplete");
        if (status == "incomplete") ++incomplete;
        const QJsonArray findings = category.value("findings").toArray();
        if (findings.isEmpty()) {
            auto* item = new QTreeWidgetItem(m_tree);
            item->setText(0, status == "pass" ? "PASS" : status.toUpper());
            item->setText(1, categoryName);
            item->setText(2, category.value("required").toBool()
                ? "Required category" : "Advisory category");
            item->setForeground(0, QColor(status == "pass" ? "#3fb950"
                : status == "not_applicable" ? "#8b949e" : "#d29922"));
            continue;
        }
        for (const auto& findingValue : findings) {
            const QJsonObject finding = findingValue.toObject();
            const QString severity = finding.value("severity").toString("info");
            if (severity == "error") ++errors;
            else if (severity == "warning") ++warnings;
            auto* item = new QTreeWidgetItem(m_tree);
            item->setText(0, severity == "error" ? "ERR"
                : severity == "warning" ? "WARN" : "INFO");
            item->setText(1, categoryName + "/" + finding.value("code").toString());
            item->setText(2, finding.value("message").toString());
            item->setForeground(0, QColor(severity == "error" ? "#f85149"
                : severity == "warning" ? "#d29922" : "#58a6ff"));
            const QJsonObject metrics = finding.value("metrics").toObject();
            item->setData(0, Qt::UserRole+1, metrics.value("x_mm").toDouble());
            item->setData(0, Qt::UserRole+2, metrics.value("y_mm").toDouble());
        }
    }
    const QString overall = report.value("overall_status").toString("incomplete");
    m_summary->setText(QString("Verification %1 — %2 error(s), %3 warning(s), "
                              "%4 incomplete category(s)")
        .arg(overall.toUpper()).arg(errors).arg(warnings).arg(incomplete));
    m_summary->setStyleSheet(overall == "pass" ? "color:#3fb950;"
        : overall == "fail" ? "color:#f85149;" : "color:#d29922;");
}

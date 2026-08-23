#include "PropagationPanel.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QFontDatabase>
#include <QLabel>
#include <QStringList>
#include <QTextEdit>
#include <QVBoxLayout>

PropagationPanel::PropagationPanel(QWidget* parent) : QWidget(parent) {
    auto* layout = new QVBoxLayout(this);
    m_summary = new QLabel(QStringLiteral(
        "No coupled engineering propagation has run for the current edit."), this);
    m_summary->setWordWrap(true);
    layout->addWidget(m_summary);
    m_details = new QTextEdit(this);
    m_details->setReadOnly(true);
    m_details->setFont(QFontDatabase::systemFont(QFontDatabase::FixedFont));
    layout->addWidget(m_details, 1);
}

void PropagationPanel::setPending(const QString& detail) {
    m_summary->setText(QStringLiteral("Propagation pending · %1").arg(detail));
    m_details->clear();
}

void PropagationPanel::setRun(const QJsonObject& result) {
    const QString status = result.value(QStringLiteral("status")).toString();
    const QJsonArray changed = result.value(QStringLiteral("changed_node_ids")).toArray();
    const QJsonArray affected = result.value(QStringLiteral("affected_node_ids")).toArray();
    const QJsonObject summary = result.value(QStringLiteral("solver_summary")).toObject();
    m_summary->setText(QStringLiteral("Propagation: %1 · %2 changed · %3 affected · "
                                      "%4 pass · %5 fail · %6 incomplete")
                           .arg(status)
                           .arg(changed.size())
                           .arg(affected.size())
                           .arg(summary.value(QStringLiteral("pass")).toInt())
                           .arg(summary.value(QStringLiteral("fail")).toInt())
                           .arg(summary.value(QStringLiteral("incomplete")).toInt()));
    m_summary->setStyleSheet(status == QStringLiteral("pass")
                                 ? QStringLiteral("color:#3fb950;")
                                 : status == QStringLiteral("fail")
                                       ? QStringLiteral("color:#f85149;")
                                       : QStringLiteral("color:#d29922;"));

    QStringList lines;
    lines << QStringLiteral("Run: %1").arg(result.value(QStringLiteral("run_id")).toString())
          << QStringLiteral("Edit: %1").arg(result.value(QStringLiteral("edit_reason")).toString())
          << QStringLiteral("Affected objects:");
    for (const QJsonValue& object : result.value(QStringLiteral("affected_objects")).toArray()) {
        const QJsonObject item = object.toObject();
        lines << QStringLiteral("  %1 · %2 · %3")
                     .arg(item.value(QStringLiteral("name")).toString(),
                          item.value(QStringLiteral("domain")).toString(),
                          item.value(QStringLiteral("node_id")).toString());
    }
    lines << QStringLiteral("\nSolver evidence:");
    for (const QJsonValue& value : result.value(QStringLiteral("evidence")).toArray()) {
        const QJsonObject item = value.toObject();
        lines << QStringLiteral("  [%1] %2 (%3) — %4")
                     .arg(item.value(QStringLiteral("status")).toString(),
                          item.value(QStringLiteral("gate")).toString(),
                          item.value(QStringLiteral("domain")).toString(),
                          item.value(QStringLiteral("rerun_reason")).toString());
        const QJsonArray assumptions = item.value(QStringLiteral("assumptions")).toArray();
        if (!assumptions.isEmpty())
            lines << QStringLiteral("      assumptions: %1").arg([&] {
                QStringList values;
                for (const QJsonValue& assumption : assumptions) values << assumption.toString();
                return values.join(QStringLiteral("; "));
            }());
    }
    const QJsonObject scores = result.value(QStringLiteral("score_changes")).toObject();
    if (!scores.isEmpty()) {
        lines << QStringLiteral("\nScore changes:");
        for (auto it = scores.begin(); it != scores.end(); ++it) {
            const QJsonObject score = it.value().toObject();
            lines << QStringLiteral("  %1: %2 → %3 (%4)")
                         .arg(it.key())
                         .arg(score.value(QStringLiteral("before")).toDouble())
                         .arg(score.value(QStringLiteral("after")).toDouble())
                         .arg(score.value(QStringLiteral("unit")).toString());
        }
    }
    m_details->setPlainText(lines.join(QStringLiteral("\n")));
}

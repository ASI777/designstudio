#include "CadAssistPanel.h"

#include "CadAssistContract.h"

#include <QFileInfo>
#include <QLabel>
#include <QTreeWidget>
#include <QTreeWidgetItem>
#include <QVBoxLayout>

namespace {

void addDetail(QTreeWidget* tree, const QString& name, const QString& value)
{
    auto* item = new QTreeWidgetItem(tree);
    item->setText(0, name);
    item->setText(1, value);
}

} // namespace

CadAssistPanel::CadAssistPanel(QWidget* parent)
    : QWidget(parent)
{
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(8, 8, 8, 8);

    stateLabel_ = new QLabel(this);
    stateLabel_->setWordWrap(true);
    layout->addWidget(stateLabel_);

    noticeLabel_ = new QLabel(
        QStringLiteral("Validated evidence only — loading this session cannot modify the project."),
        this);
    noticeLabel_->setWordWrap(true);
    noticeLabel_->setProperty("muted", true);
    layout->addWidget(noticeLabel_);

    details_ = new QTreeWidget(this);
    details_->setColumnCount(2);
    details_->setHeaderLabels({QStringLiteral("Field"), QStringLiteral("Value")});
    details_->setRootIsDecorated(false);
    layout->addWidget(details_, 1);

    clearSession(QStringLiteral("No CAD-assist evidence loaded"));
}

void CadAssistPanel::setSession(
    std::shared_ptr<const designstudio::CadAssistSession> session,
    const QString& sourcePath)
{
    session_ = std::move(session);
    details_->clear();
    if (!session_) {
        clearSession(QStringLiteral("No CAD-assist evidence loaded"));
        return;
    }

    stateLabel_->setText(QStringLiteral("Validated session %1")
                             .arg(session_->sessionId()));
    addDetail(details_, QStringLiteral("Contract"), QFileInfo(sourcePath).fileName());
    addDetail(details_, QStringLiteral("Document ID"), session_->documentId());
    addDetail(details_, QStringLiteral("Base revision"),
              QString::number(session_->baseRevision()));
    addDetail(details_, QStringLiteral("Project SHA-256"), session_->baseSha256());
    addDetail(details_, QStringLiteral("Drawing SHA-256"), session_->sourceSha256());
    addDetail(details_, QStringLiteral("Pages"), QString::number(session_->pageCount()));
    addDetail(details_, QStringLiteral("Calibrations"),
              QString::number(session_->calibrationIds().size()));
    addDetail(details_, QStringLiteral("Views"),
              QString::number(session_->viewboxIds().size()));
    addDetail(details_, QStringLiteral("Targets"),
              QString::number(session_->targetIds().size()));
    addDetail(details_, QStringLiteral("Elements"),
              QString::number(session_->elementIds().size()));
    addDetail(details_, QStringLiteral("Deviations"),
              QString::number(session_->deviationIds().size()));
    details_->resizeColumnToContents(0);
}

void CadAssistPanel::clearSession(const QString& reason)
{
    session_.reset();
    details_->clear();
    stateLabel_->setText(reason);
}

QString CadAssistPanel::summaryText() const
{
    return stateLabel_->text();
}
